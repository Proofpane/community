import { useEffect, useMemo, useState } from "react";
import { Check, ChevronRight, CircleDot, Cloud, GitBranch, GitPullRequest, ListTodo, Loader2, Search, ShieldCheck, Sparkles, X } from "lucide-react";
import { Api, type AccountKind, type AccountsView, type ClaudeStatus, type CustomAgent, type Held, type Issue, type LinearIssue, type PullListed } from "../api";
import type { AgentKind } from "../types";
import { Modal } from "./Modal";
import { head as cutTo, visible } from "../text";
import { AgentBadge, Kbd, agentLabel } from "./bits";
import { SettingsFields, settingsNote } from "./ChatSettings";
import type { Chosen } from "../chatSettings";
import { day } from "../when";

export const MAX_TASK = 4000;     // the workspace's own limit on a session's task

// the task being written, kept while the dialog is closed (by Escape, or a click beside it) and the page reloaded, until
// the sessions start: a closed dialog never loses what was typed in it. This browser's own, as a per-person convenience.
const DRAFT = "pp-new-session-draft";
export function readDraft(): string {
  try { return localStorage.getItem(DRAFT) || ""; } catch { return ""; }
}
export function keepDraft(text: string): void {
  try { if (text.trim()) localStorage.setItem(DRAFT, text); else localStorage.removeItem(DRAFT); } catch { /* not kept */ }
}

// the agents whose every tool call the workspace's gate sees, through their own hooks: everywhere, Claude Code (and the
// other providers' models it runs), Gemini CLI and Qwen Code (through the settings the workspace gives them); in a
// hosted workspace also Codex and Grok Build, through root's hook files there. The others' changes are checked when
// the session finishes, as every session's are
const GATED_EVERYWHERE: AgentKind[] = ["claude", "chat", "gemini", "qwen", "kimi", "minimax", "deepseek"];
export function gatedAgents(provider?: string): Set<AgentKind> {
  return new Set<AgentKind>(provider === "fly" ? [...GATED_EVERYWHERE, "codex", "grok"] : GATED_EVERYWHERE);
}

// a hosted workspace's claude terminals and chats hold their gated commands for this deployment's approval queue
// (the workspace gives them open_edition/ade_hook.py); said only where the workspace's agent says its
// machine holds them (heldSentence), with what it reaches and what it does not: the hook's list (ade_hook.GATE), and
// the exceptions that touch Claude Code
export const HELD_HERE = "In this hosted workspace, a Bash command from a Claude Code terminal or chat that the "
  + "approval hook's list names (git push, gh pr merge or gh release, fly deploy, npm publish, docker push, "
  + "terraform apply, kubectl apply, rm -rf and a few more) waits for your approval under Approvals, with a "
  + "confirmation for each; any other question stays the session's own. Not held so: the same act in other words "
  + "(git -C dir push, rm -r -f) or by an MCP server's tool; an agent's run, a replay or a task's attempt, which run "
  + "with no one there to approve; the other programs and your own agents; a shell's own commands, a claude started "
  + "in one among them; a claude started with --bare; a session that rewrites its own settings files; a seat whose "
  + "organisation's managed settings turn other hooks off; and what a command does that its words do not say.";
// the same machine, with its hook missing a part it needs: each command on the list is refused, never let through
export const HELD_REFUSED_HERE = "This hosted workspace's approval hook is missing a part it needs (this "
  + "deployment's address, its hook token, or the hook itself), so a Bash command on its list from a Claude Code "
  + "terminal or chat is refused rather than held for your approval, until the machine is next started from stopped "
  + "with them.";
// and a machine whose image has no hook at all
export const HELD_NONE_HERE = "This hosted workspace's machine runs an image without the approval hook, so a Claude "
  + "Code terminal's or chat's push, merge, deploy or delete does not wait for your approval here.";

/** What the page says of the approval hook, for a hosted workspace whose agent has said whether it holds (null before,
 *  and for the local provider, which has none). */
export function heldSentence(provider?: string, held?: Held | null): string | null {
  if (provider !== "fly" || !held) return null;
  return held === "held" ? HELD_HERE : held === "refused" ? HELD_REFUSED_HERE : HELD_NONE_HERE;
}

/** A session's task from a GitHub issue: its title first (the pull request's title too), its text, then a line that
 *  closes the issue when the pull request is merged. Cut to the workspace's limit, saying where the rest is; only what
 *  the person can see of someone else's text (text.ts, visible). */
export function taskFromIssue(i: Issue, max = MAX_TASK): string {
  const close = `\n\nCloses #${i.number}`;
  const head = visible(i.title).trim();
  const body = visible(i.body).trim();
  const more = i.url ? `\n\n[The issue goes on: ${i.url}]` : "\n\n[The issue goes on.]";
  const room = max - head.length - 2 - close.length;
  if (body.length <= room && !i.cut) return `${head}${body ? `\n\n${body}` : ""}${close}`;
  const cut = cutTo(body, Math.max(0, room - more.length)).trimEnd();
  return cutTo(`${head}${cut ? `\n\n${cut}` : ""}${more}${close}`, max);
}

/** A session's task from a Linear issue: its identifier and title first, its text, then where it is in Linear. Cut to
 *  the workspace's limit, saying where the rest is. */
export function taskFromLinear(i: LinearIssue, max = MAX_TASK): string {
  const head = `${i.identifier}: ${visible(i.title).trim()}`;
  const where = i.url ? `\n\n[The issue in Linear: ${i.url}]` : "";
  const body = visible(i.description).trim();
  const room = max - head.length - 2 - where.length;
  if (body.length <= room && !i.cut) return `${head}${body ? `\n\n${body}` : ""}${where}`;
  const more = "\n\n[It goes on, in Linear.]";
  const cut = cutTo(body, Math.max(0, room - more.length)).trimEnd();
  return cutTo(`${head}${cut ? `\n\n${cut}` : ""}${more}${where}`, max);
}

/** A session's task from an open pull request: what to do with it first (review it, as written; the person changes
 *  that), its title and text, and where it is. Cut to the workspace's limit, saying where the rest is. */
export function taskFromPull(p: PullListed, max = MAX_TASK): string {
  const head = `Review pull request #${p.number}: ${visible(p.title).trim()}`;
  const where = p.url ? `\n\n[The pull request: ${p.url}]` : "";
  const body = visible(p.body).trim();
  const room = max - head.length - 2 - where.length;
  if (body.length <= room && !p.cut) return `${head}${body ? `\n\n${body}` : ""}${where}`;
  const more = "\n\n[It goes on, in the pull request.]";
  const cut = cutTo(body, Math.max(0, room - more.length)).trimEnd();
  return cutTo(`${head}${cut ? `\n\n${cut}` : ""}${more}${where}`, max);
}

export interface NewSessionRequest {
  repo: string;
  task: string;
  agents: AgentKind[];      // more than one: the same task, one worktree per agent, in parallel
  /** The person's own agents chosen too, by id (custom_agents.py): each one session more, as an agent is. */
  customs?: string[];
  base?: string;
  /** Started from an open pull request's head, fetched from GitHub, in place of a base. */
  pull?: number;
  chatSettings?: Record<string, string>;    // a chat's model, effort and permission mode; the others have none
  /** Started on another session's task, to be compared with it: that session, and its group if it has one. */
  again?: { id: string; group?: string };
  /** The Claude Code and Codex accounts its sessions start on, where the person has more than one (accounts.py); none,
   *  the one new sessions start on. */
  accounts?: { claude?: string; codex?: string };
  /** A cloud session of the person's Claude account to continue here, through Claude Code's own teleport: its id or
   *  claude.ai/code link, or "" for Claude Code's picker of the account's cloud sessions. One claude session. */
  teleport?: string;
}

/** The task a session continued from claude.ai is given when none is typed. The workspace names such a session after
 *  the branch it continues once it takes its work (workspace.TELEPORT_TASK, the same words). */
export const TELEPORT_TASK = "Continued from claude.ai";

/** A cloud session's id as claude.ai shows it, or a claude.ai/code link to one: what the workspace takes for teleport
 *  (workspace.teleport_id); blank is Claude Code's own picker. */
export function cloudProblem(value: string): string | null {
  const v = value.trim();
  if (!v) return null;
  if (/^(?:session|cse)_[A-Za-z0-9_-]{1,100}$/.test(v)) return null;
  if (/^https:\/\/claude\.ai\/code\/(?:session|cse)_[A-Za-z0-9_-]{1,100}\/?(?:[?#]\S*)?$/.test(v)) return null;
  return "A cloud session is named by its id (session_... or cse_...) or its claude.ai/code link; leave it blank to pick from your list.";
}

/** A session whose task more agents are to try, from the same commit, in its group. */
export interface Again { id: string; title: string; repo: string; task: string; base: string; group?: string }

/** The agent a new session starts with: the first offered that can start, before a chat or a shell (an agent is what
 *  a task is for), else a shell, which always can. */
export function firstAgent(offered: { kind: AgentKind; available: boolean }[]): AgentKind[] {
  const can = offered.filter((a) => a.available).map((a) => a.kind);
  return [can.find((k) => k !== "chat" && k !== "shell") || (can.includes("chat") ? "chat" : "shell")];
}

export function NewSession(props: {
  repos: string[];
  githubRepos?: string[];   // those whose origin is on GitHub: they have issues to start from
  agents: { kind: AgentKind; available: boolean; note?: string }[];
  /** The person's own agents, offered after the ones the workspace knows. */
  customAgents?: CustomAgent[];
  /** Claude Code's and Codex's accounts in the workspace: with more than one, the session's is chosen here. */
  accounts?: AccountsView | null;
  /** The workspace's provider: which agents the gate sees depends on it (gatedAgents). */
  provider?: string;
  /** Whether its claude terminals and chats hold their gated commands; null until its agent says. */
  held?: Held | null;
  defaultRepo?: string;
  /** Try another session's task with more agents: its repository, task and commit, and none of its agents chosen. */
  again?: Again;
  onStart: (req: NewSessionRequest) => Promise<void> | void;
  onClose: () => void;
  onClone: () => void;
  /** Connections, where an agent that waits for a sign-in or a key gets it (the task written so far is kept). */
  onConnections?: () => void;
}) {
  const again = props.again;
  const [repo, setRepo] = useState(again?.repo || props.defaultRepo || props.repos[0] || "");
  // another session's task to try with more agents is that session's; else the one being written before
  const [task, setTaskOnly] = useState(again ? again.task || "" : readDraft());
  const setTask = (text: string) => { setTaskOnly(text); if (!again) keepDraft(text); };
  const [agents, setAgents] = useState<AgentKind[]>(again ? [] : firstAgent(props.agents));
  const [more, setMore] = useState(false);      // the agents that cannot start here, shown
  const gated = gatedAgents(props.provider);
  const [base, setBase] = useState(again?.base || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [picking, setPicking] = useState(false);
  const [chosen, setChosen] = useState<Chosen>({});
  // the account each program's sessions start on, where there is more than one: the one chosen for new sessions first
  const [onAccount, setOnAccount] = useState<{ claude?: string; codex?: string }>({});
  const [issue, setIssue] = useState<Issue | null>(null);
  const [pickingPull, setPickingPull] = useState(false);
  const [pull, setPull] = useState<PullListed | null>(null);
  // a cloud session to continue here (teleport): null, not chosen; "" or its id or link, chosen
  const [cloud, setCloud] = useState<string | null>(null);
  const [linearOn, setLinearOn] = useState(false);
  const [pickingLinear, setPickingLinear] = useState(false);
  const [linearIssue, setLinearIssue] = useState<LinearIssue | null>(null);
  useEffect(() => {
    let live = true;
    Api.linear().then((st) => live && setLinearOn(st.set && !st.error), () => {});
    return () => { live = false; };
  }, []);
  const onGithub = (props.githubRepos || []).includes(repo);
  const [customs, setCustoms] = useState<string[]>([]);
  // one of the person's own found missing since it was chosen: not started
  const missing = (props.customAgents || []).filter((a) => a.available === false).map((a) => a.id).join(",");
  useEffect(() => { if (missing) setCustoms((cur) => cur.filter((id) => !missing.split(",").includes(id))); }, [missing]);
  const chosenCount = agents.length + customs.length;
  const needsTask = agents.some((a) => a !== "shell") || customs.length > 0;
  const claudeHere = props.agents.some((a) => a.kind === "claude" && a.available);
  const canStart = cloud !== null
    ? !!repo && repo !== "Workspace" && claudeHere && !busy
    : !!repo && chosenCount > 0 && (!needsTask || task.trim().length > 0) && !busy;

  const toggle = (k: AgentKind) =>
    setAgents((cur) => (cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k]));
  // an agent found unable to start once the workspace says (not installed, Claude not signed in) leaves the choice;
  // a first choice left empty by that falls to the next agent that can start
  const unavailable = props.agents.filter((a) => !a.available).map((a) => a.kind).join(",");
  useEffect(() => {
    if (!unavailable) return;
    const out = new Set(unavailable.split(","));
    setAgents((cur) => {
      if (!cur.some((k) => out.has(k))) return cur;
      const kept = cur.filter((k) => !out.has(k));
      return kept.length || again ? kept : firstAgent(props.agents);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unavailable]);

  const several = (k: AccountKind) => (props.accounts?.[k].accounts.length ?? 0) > 1;
  const accountOf = (k: AccountKind) => onAccount[k] ?? props.accounts?.[k].active ?? "default";
  // a picker for each program with more than one account whose agent is chosen
  const pickers = (["claude", "codex"] as const).filter((k) => several(k) && (k === "claude" ? agents.some((a) => a === "claude" || a === "chat") : agents.includes(k)));
  // whose cloud sessions teleport lists: the Claude Code account chosen above, where there is more than one
  const cloudRow = props.accounts?.claude.accounts.find((a) => a.id === accountOf("claude"));
  const cloudWho = !several("claude") || !cloudRow ? "the account Claude Code here is signed in with"
    : cloudRow.id === "default" ? "Claude Code's own sign-in here" : `the account ${cloudRow.name}`;
  const chooseRepo = (r: string) => { setRepo(r); setPicking(false); setIssue(null); setPickingPull(false); setPull(null); };

  const start = async () => {
    if (!canStart) return;
    if (cloud !== null) {
      const problem = cloudProblem(cloud);
      if (problem) { setError(problem); return; }
    }
    setBusy(true);
    setError("");
    try {
      if (cloud !== null) {
        // one claude session, from the repository's head: teleport brings the cloud session's branch and conversation
        await props.onStart({ repo, task: task.trim() || TELEPORT_TASK, agents: ["claude"], customs: [],
          teleport: cloud.trim(), accounts: several("claude") ? { claude: accountOf("claude") } : {} });
        props.onClose();
        return;
      }
      const chatSettings = Object.fromEntries(Object.entries(chosen).filter(([, v]) => v)) as Record<string, string>;
      await props.onStart({ repo, task: task.trim(), agents, customs, base: pull ? undefined : base.trim() || undefined, pull: pull?.number,
        chatSettings: agents.includes("chat") ? chatSettings : undefined,
        accounts: Object.fromEntries(pickers.map((k) => [k, accountOf(k)])),
        again: again ? { id: again.id, group: again.group } : undefined });
      if (!again) keepDraft("");          // started: the draft is done with
      props.onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const fanout = chosenCount > 1;
  const hint = useMemo(
    () => (fanout ? `${chosenCount} sessions in parallel, one worktree each, so you can compare and keep the best.` : ""),
    [fanout, chosenCount],
  );

  // the agents that can start here first; those that cannot (not installed, or waiting for a key) under "more",
  // each with its reason, closed until asked for, unless none can start
  const ready = props.agents.filter((a) => a.available);
  const notReady = props.agents.filter((a) => !a.available);
  const missingCustoms = (props.customAgents || []).filter((a) => a.available === false);
  const moreShown = more || (ready.length === 0 && !(props.customAgents || []).some((a) => a.available !== false));
  const builtIn = ({ kind, available, note }: { kind: AgentKind; available: boolean; note?: string }) => {
    const on = agents.includes(kind);
    return (
      <button
        key={kind}
        disabled={!available}
        aria-pressed={on}
        onClick={() => toggle(kind)}
        title={!available ? note || "Not installed in this workspace"
          : gated.has(kind) ? `${agentLabel(kind)}: the workspace's gate sees each tool call it makes`
          : kind === "shell" ? "A shell in the worktree"
          : kind === "codex" || kind === "grok" ? `${agentLabel(kind)}: the gate holds it in a hosted workspace; here its change is checked when you finish`
          : kind === "kimicode" || kind === "mcode" ? `${agentLabel(kind)}: its hooks are its user's own to change, so the gate does not count on them; its change is checked when you finish`
          : `${agentLabel(kind)}: it has no hooks for the gate; its change is checked when you finish`}
        className={`flex items-center gap-2 rounded-lg border px-2.5 py-2 text-left text-[12.5px] transition-colors ${
          on ? "border-accent bg-accent/10 text-fg" : available ? "border-line-strong text-fg-dim hover:border-faint" : "border-line text-muted"
        }`}
      >
        <AgentBadge kind={kind} size={18} />
        <span className="min-w-0 flex-1">
          <span className="block break-words leading-tight">{agentLabel(kind)}</span>
          {/* why it cannot start, in full: a title is not read on a phone, and a disabled tile takes no focus */}
          {!available && <span className="mt-0.5 block text-[11px] leading-snug text-muted">{note || "Not installed in this workspace"}</span>}
        </span>
        {gated.has(kind) && <ShieldCheck size={12} className="flex-none text-faint" aria-label="Gated" />}
        {on && <Check size={13} className="text-accent" />}
      </button>
    );
  };
  const custom = (a: CustomAgent) => {
    const on = customs.includes(a.id);
    const missing = a.available === false;          // its program is not found in the workspace
    return (
      <button key={`custom-${a.id}`} disabled={missing} aria-pressed={on}
        onClick={() => setCustoms((cur) => (on ? cur.filter((x) => x !== a.id) : [...cur, a.id]))}
        title={missing ? `${a.name}: its program is not found in this workspace (${a.command})`
          : `${a.name}, your own program: ${a.command}. The gate does not see inside it, as with a shell; its change is checked when you finish`}
        className={`flex items-center gap-2 rounded-lg border px-2.5 py-2 text-left text-[12.5px] transition-colors ${
          on ? "border-accent bg-accent/10 text-fg" : missing ? "border-line text-muted" : "border-line-strong text-fg-dim hover:border-faint"}`}>
        <AgentBadge kind="custom" name={a.name} size={18} />
        <span className="min-w-0 flex-1">
          <span className="block break-words leading-tight">{a.name}</span>
          {missing && <span className="mt-0.5 block text-[11px] leading-snug text-muted">Not found in this workspace</span>}
        </span>
        {on && <Check size={13} className="text-accent" />}
      </button>
    );
  };

  return (
    <Modal
      title={again ? "Try with another agent" : "New session"}
      onClose={props.onClose}
      width={620}
      footer={
        <>
          {error && <span className="mr-auto text-[12px] text-fail">{error}</span>}
          <span className="mr-2 text-[11.5px] text-faint [@media(pointer:coarse)]:hidden"><Kbd>⌘</Kbd> <Kbd>↵</Kbd> to start</span>
          <button className="btn" onClick={props.onClose}>Cancel</button>
          <button className="btn btn-primary" disabled={!canStart} onClick={start}>
            <Sparkles size={13} /> {busy ? "Starting…" : cloud !== null ? "Continue from claude.ai" : fanout ? `Start ${chosenCount} sessions` : "Start"}
          </button>
        </>
      }
    >
      <div className="space-y-4" onKeyDown={(e) => e.key === "Enter" && (e.metaKey || e.ctrlKey) && start()}>
        {again && (
          <p className="rounded-lg border border-line bg-panel px-3 py-2 text-[12px] text-fg-dim">
            The task of <b className="font-medium text-fg">{again.title}</b>, from the same commit
            (<span className="font-mono">{again.base.slice(0, 7)}</span>). Each agent you choose works in a worktree of its own,
            and they open beside it, to compare and keep the best.
          </p>
        )}
        <div>
          <label className="label mb-1.5 block">Repository</label>
          {again ? (
            // the task's own repository: the agents start from its commit, which no other repository has
            <div className="input flex items-center text-fg-dim" aria-label="Repository">{repo}</div>
          ) : props.repos.length ? (
            <div className="flex gap-2">
              <select aria-label="Repository" className="input" value={repo} onChange={(e) => chooseRepo(e.target.value)}>
                {props.repos.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
              <button className="btn" onClick={props.onClone}>{props.provider === "local" ? "Add…" : "Clone…"}</button>
            </div>
          ) : (
            <button className="btn" onClick={props.onClone}>{props.provider === "local" ? "Add a repository first" : "Clone a repository first"}</button>
          )}
        </div>

        <div>
          <div className="mb-1.5 flex items-center gap-2">
            <label className="label">Task</label>
            {issue && (
              <span className="pill text-[11px]">
                <CircleDot size={11} className="text-done" /> From #{issue.number}
                <button className="text-muted hover:text-fg" onClick={() => setIssue(null)} title="Keep the text; forget the issue"><X size={11} /></button>
              </span>
            )}
            {pull && (
              <span className="pill text-[11px]">
                <GitPullRequest size={11} className="text-done" /> From pull request #{pull.number}
                <button className="text-muted hover:text-fg" onClick={() => setPull(null)} title="Keep the text; start from the base below instead"><X size={11} /></button>
              </span>
            )}
            {linearIssue && (
              <span className="pill text-[11px]">
                <ListTodo size={11} className="text-done" /> From {linearIssue.identifier}
                <button className="text-muted hover:text-fg" onClick={() => setLinearIssue(null)} title="Keep the text; forget the issue"><X size={11} /></button>
              </span>
            )}
            {(onGithub || linearOn) && !again && (
              <span className="ml-auto flex items-center gap-3">
                {linearOn && (
                  <button className="flex items-center gap-1 text-[12px] text-accent hover:underline" onClick={() => { setPickingLinear((p) => !p); setPicking(false); setPickingPull(false); }}>
                    <ListTodo size={12} /> {pickingLinear ? "Hide the list" : "From Linear"}
                  </button>
                )}
                {onGithub && (
                  <>
                    <button className="flex items-center gap-1 text-[12px] text-accent hover:underline" onClick={() => { setPicking((p) => !p); setPickingPull(false); setPickingLinear(false); }}>
                      <CircleDot size={12} /> {picking ? "Hide the list" : "From a GitHub issue"}
                    </button>
                    <button className="flex items-center gap-1 text-[12px] text-accent hover:underline" onClick={() => { setPickingPull((p) => !p); setPicking(false); setPickingLinear(false); }}>
                      <GitPullRequest size={12} /> {pickingPull ? "Hide the list" : "From a pull request"}
                    </button>
                  </>
                )}
              </span>
            )}
          </div>
          {picking && <IssuePicker repo={repo} onPick={(i) => { setIssue(i); setLinearIssue(null); setTask(taskFromIssue(i)); setPicking(false); }} />}
          {pickingPull && <PullPicker repo={repo} onPick={(p) => { setPull(p); setIssue(null); setLinearIssue(null); setTask(taskFromPull(p)); setPickingPull(false); }} />}
          {pickingLinear && <LinearPicker onPick={(i) => { setLinearIssue(i); setIssue(null); setPull(null); setTask(taskFromLinear(i)); setPickingLinear(false); }} />}
          <textarea aria-label="Task"
            autoFocus
            rows={issue || linearIssue ? 7 : 4}
            maxLength={MAX_TASK}
            className="input"
            placeholder={needsTask ? "What should the agent do? Be as specific as you would be with a colleague." : "Optional for a shell"}
            value={task}
            onChange={(e) => setTask(e.target.value)}
          />
          {task.length > MAX_TASK - 200 && <div className="mt-1 text-right text-[11px] text-faint">{task.length} of {MAX_TASK}</div>}
        </div>

        <div>
          <label className="label mb-1.5 block">Agents</label>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {ready.map(builtIn)}
            {(props.customAgents || []).filter((a) => a.available !== false).map(custom)}
          </div>
          {notReady.length + missingCustoms.length > 0 && (
            <>
              <button type="button" className="mt-2 flex items-center gap-1.5 text-[12px] text-muted hover:text-fg-dim"
                aria-expanded={moreShown} onClick={() => setMore((m) => !m)}>
                <ChevronRight size={13} className={`transition-transform ${moreShown ? "rotate-90" : ""}`} />
                {notReady.length + missingCustoms.length} more, not ready here
              </button>
              {moreShown && (
                <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {notReady.map(builtIn)}
                  {missingCustoms.map(custom)}
                </div>
              )}
              {moreShown && props.onConnections && notReady.some((a) => a.note?.includes("Connections")) && (
                <button type="button" className="mt-2 text-[12px] text-accent hover:underline" onClick={props.onConnections}>
                  Open Connections, to sign in or add a key
                </button>
              )}
            </>
          )}
          {hint && <p className="mt-2 text-[12px] text-accent">{hint}</p>}
          {pickers.map((k) => {
            const rows = props.accounts![k].accounts;
            const picked = rows.find((a) => a.id === accountOf(k));
            const out = picked?.status && picked.status.signed_in === false;
            return (
              <div key={k} className="mt-2 flex flex-wrap items-center gap-2 text-[12px]">
                <label className="text-muted" htmlFor={`account-${k}`}>{k === "claude" ? "Claude Code" : "Codex"} account</label>
                <select id={`account-${k}`} className="input h-7 !py-0 text-[12px]" value={accountOf(k)} onChange={(e) => setOnAccount((c) => ({ ...c, [k]: e.target.value }))}>
                  {rows.map((a) => {
                    const w = a.usage?.five_hour ? ` · 5h ${Math.min(100, a.usage.five_hour.used_percentage).toFixed(0)}%` : "";
                    const signedOut = a.status && a.status.signed_in === false ? " · not signed in" : "";
                    return <option key={a.id} value={a.id}>{a.name}{w}{signedOut}</option>;
                  })}
                </select>
                {out && <span className="text-wait">{picked!.name} is not signed in: sign it in from Connections first.</span>}
                {k === "claude" && picked && picked.status && (picked.status as ClaudeStatus).email && !out && (
                  <span className="text-faint">{(picked.status as ClaudeStatus).email}</span>
                )}
              </div>
            );
          })}
          <p className="mt-2 flex items-center gap-1.5 text-[11.5px] text-faint">
            <ShieldCheck size={12} className="flex-none" aria-hidden /> The shield marks an agent whose every tool call the workspace&apos;s gate sees; every session&apos;s change is checked when it finishes.
          </p>
          {heldSentence(props.provider, props.held) && (
            <p className="mt-1.5 text-[11.5px] text-faint">{heldSentence(props.provider, props.held)}</p>
          )}
          {agents.includes("chat") && (
            <div className="mt-3 rounded-lg border border-line px-3 py-2.5">
              <div className="mb-2 text-[11.5px] text-muted">The chat&apos;s settings; each can change while it runs.</div>
              <div className="flex flex-wrap gap-1.5">
                <SettingsFields chosen={chosen} onChange={(key, value) => setChosen((c) => ({ ...c, [key]: value }))} />
              </div>
              {settingsNote(chosen) && <div className="mt-2 text-[11.5px] text-muted">{settingsNote(chosen)}</div>}
            </div>
          )}
          {agents.includes("codex") && (
            <p className="mt-2 text-[11.5px] text-muted">Codex signs in from Connections, or asks the first time it starts: choose <b className="font-medium text-fg-dim">Sign in with Device Code</b> (a link and a one-time code to open on any device; the other ChatGPT choice needs a browser on the workspace&apos;s own machine), or an API key.</p>
          )}
          {agents.includes("gemini") && (
            <p className="mt-2 text-[11.5px] text-muted">Gemini CLI asks how to sign in the first time it starts: with Google (it shows a link, then takes the code it gives) or with an API key.</p>
          )}
          {agents.includes("grok") && (
            <p className="mt-2 text-[11.5px] text-muted">Grok Build signs in the first time it starts: it prints a link and a code to open on any device.</p>
          )}
          {agents.some((a) => a === "kimi" || a === "minimax" || a === "deepseek") && (
            <p className="mt-2 text-[11.5px] text-muted">Kimi K3, MiniMax M3 and DeepSeek run in Claude Code, unmodified, on your own key for each, through their makers&apos; Anthropic-compatible endpoints; the workspace&apos;s gate holds them as it holds Claude Code.</p>
          )}
          {agents.includes("kimicode") && (
            <p className="mt-2 text-[11.5px] text-muted">Kimi Code, Moonshot AI&apos;s own program, asks first whether to trust the folder (trusting starts the MCP servers the repository lists), and the first time, signs in with /login in its terminal. It cannot be started with its task: a button over its terminal types it at its prompt for you to send.</p>
          )}
          {agents.includes("mcode") && (
            <p className="mt-2 text-[11.5px] text-muted">MiniMax Code, MiniMax&apos;s own program, signs in with /login in its terminal the first time; then ask it to try the task again. In a workspace it does not report its steps to MiniMax (MCODE_DISABLE_EVAL_CAPTURE).</p>
          )}
          {agents.includes("qwen") && (
            <p className="mt-2 text-[11.5px] text-muted">Qwen Code asks how to sign in the first time it starts: with Qwen (it prints a link to open on any device) or with an API key of a provider it supports.</p>
          )}
          {agents.includes("opencode") && (
            <p className="mt-2 text-[11.5px] text-muted">OpenCode asks for a model provider the first time: connect one in it, or run <span className="font-mono">opencode auth login</span> in a shell.</p>
          )}
        </div>

        <div className="flex items-end gap-3">
          <div className="w-[200px] flex-none">
            <label className="label mb-1.5 block">From</label>
            {cloud !== null ? (
              <input aria-label="A cloud session's id or claude.ai/code link" className="input font-mono text-[12px]" value={cloud}
                placeholder="blank: pick from your list" onChange={(e) => setCloud(e.target.value)} />
            ) : pull ? (
              <div className="input flex items-center gap-1.5 text-[12px] text-fg-dim"
                title={pull.branch ? `The head of pull request #${pull.number}, its branch ${pull.branch}${pull.base ? ` into ${pull.base}` : ""}` : undefined}>
                <GitPullRequest size={12} className="flex-none" /> <span className="truncate"><span className="font-mono">#{pull.number}</span>&apos;s head</span>
              </div>
            ) : (
              <input aria-label="Start from: a branch, a tag or a commit" className="input font-mono text-[12px]" value={base} placeholder="the default branch" onChange={(e) => setBase(e.target.value)} />
            )}
          </div>
          <p className="flex items-start gap-1.5 pb-1.5 text-[11.5px] text-muted">
            <GitBranch size={12} className="mt-0.5 flex-none" />
            {cloud !== null
              ? `Claude Code's own teleport, in a fresh worktree of ${repo || "the repository"}: it opens the cloud session named, or lists the cloud sessions of ${cloudWho} recorded on ${repo || "this repository"} to pick one, then fetches its branch and its whole conversation, and the session takes that branch. A cloud session on more than one repository is listed, and continued, only in the one repository Claude Code recorded it under, its link given here included. It needs the cloud session's branch pushed. To work in ${repo || "the repository"} on that account, a plain session needs no cloud session. What you do here stays here: it does not reach the cloud session.`
              : pull ? "From the pull request's head, fetched from GitHub. Each session still works on a branch of its own: pp/ followed by its id."
                : "Each session works in its own worktree, on its own branch: pp/ followed by the session's id."}
          </p>
          {cloud !== null && !again && props.repos.some((r) => r !== repo) && (
            <p className="flex flex-wrap items-center gap-1.5 pb-1.5 text-[11.5px] text-muted">
              Not listed here? Try it in
              {props.repos.filter((r) => r !== repo).slice(0, 6).map((r) => (
                <button key={r} type="button" className="btn btn-sm !h-6 font-mono text-[11px]" onClick={() => chooseRepo(r)}>{r}</button>
              ))}
            </p>
          )}
          <button className={`btn btn-sm mb-1 flex-none ${cloud !== null ? "btn-primary" : ""}`} aria-pressed={cloud !== null}
            title={`Continue a cloud session of ${cloudWho} here (claude.ai, the Claude app's Code tab)`}
            onClick={() => { setCloud(cloud === null ? "" : null); setError(""); }}>
            <Cloud size={12} /> From claude.ai
          </button>
        </div>
      </div>
    </Modal>
  );
}

/** A repository's open issues, searched with GitHub's own search; picking one starts the task from it. */
function IssuePicker({ repo, onPick }: { repo: string; onPick: (i: Issue) => void }) {
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState<Issue[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    setError("");
    const t = setTimeout(async () => {
      try {
        const r = await Api.issues(repo, query.trim() || undefined);
        if (live) setShown(r.issues);
      } catch (e) {
        if (live) { setShown([]); setError(e instanceof Error ? e.message : String(e)); }
      }
    }, query ? 350 : 0);
    return () => { live = false; clearTimeout(t); };
  }, [repo, query]);
  return (
    <div className="mb-2 rounded-lg border border-line-strong bg-bg">
      <div className="flex items-center gap-2 border-b border-line px-2.5">
        <Search size={13} className="text-muted" />
        <input aria-label="Search the open issues" className="h-8 flex-1 bg-transparent text-[12.5px] outline-none placeholder:text-faint" placeholder="Search the open issues" value={query} onChange={(e) => setQuery(e.target.value)} />
        {shown === null && <Loader2 size={13} className="animate-spin text-muted" />}
      </div>
      <ul className="max-h-56 overflow-y-auto py-1">
        {error && <li className="px-3 py-2 text-[12px] text-fail">{error}</li>}
        {shown && !shown.length && !error && <li className="px-3 py-2 text-[12px] text-muted">{query ? "No open issue matches." : "No open issues."}</li>}
        {shown?.map((i) => (
          <li key={i.number}>
            <button className="flex w-full items-start gap-2 px-3 py-1.5 text-left hover:bg-hover" onClick={() => onPick(i)}>
              <CircleDot size={13} className="mt-0.5 flex-none text-done" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[12.5px] text-fg">{i.title} <span className="font-mono text-muted">#{i.number}</span></span>
                <span className="block truncate text-[11px] text-faint">
                  {i.author ? `${i.author}` : ""}{i.labels.length ? ` · ${i.labels.join(", ")}` : ""}{i.updated_at ? ` · updated ${day(i.updated_at)}` : ""}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The person's open Linear issues, or a search of their Linear workspace's (by text, or an identifier such as ENG-12);
 *  picking one starts the task from it. */
function LinearPicker({ onPick }: { onPick: (i: LinearIssue) => void }) {
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState<LinearIssue[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    setError("");
    const t = setTimeout(async () => {
      try {
        const r = await Api.linearIssues(query.trim() || undefined);
        if (live) setShown(r.issues);
      } catch (e) {
        if (live) { setShown([]); setError(e instanceof Error ? e.message : String(e)); }
      }
    }, query ? 350 : 0);
    return () => { live = false; clearTimeout(t); };
  }, [query]);
  return (
    <div className="mb-2 rounded-lg border border-line-strong bg-bg">
      <div className="flex items-center gap-2 border-b border-line px-2.5">
        <Search size={13} className="text-muted" />
        <input className="h-8 flex-1 bg-transparent text-[12.5px] outline-none placeholder:text-faint" placeholder="Search Linear, or an identifier such as ENG-12"
          aria-label="Search Linear" value={query} onChange={(e) => setQuery(e.target.value)} />
        {shown === null && <Loader2 size={13} className="animate-spin text-muted" />}
      </div>
      <ul className="max-h-56 overflow-y-auto py-1">
        {error && <li className="px-3 py-2 text-[12px] text-fail">{error}</li>}
        {shown && !shown.length && !error && <li className="px-3 py-2 text-[12px] text-muted">{query ? "No open issue matches." : "No open issues are yours."}</li>}
        {shown?.map((i) => (
          <li key={i.identifier}>
            <button className="flex w-full items-start gap-2 px-3 py-1.5 text-left hover:bg-hover" onClick={() => onPick(i)}>
              <ListTodo size={13} className="mt-0.5 flex-none text-done" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[12.5px] text-fg"><span className="font-mono text-muted">{i.identifier}</span> {i.title}</span>
                <span className="block truncate text-[11px] text-faint">
                  {[i.state, i.priority && i.priority !== "No priority" ? i.priority : null, i.team, i.labels.join(", ") || null,
                    i.updated_at ? `updated ${day(i.updated_at)}` : null].filter(Boolean).join(" · ")}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A repository's open pull requests, searched with GitHub's own search; picking one starts the session from its head. */
function PullPicker({ repo, onPick }: { repo: string; onPick: (p: PullListed) => void }) {
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState<PullListed[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    setError("");
    const t = setTimeout(async () => {
      try {
        const r = await Api.pulls(repo, query.trim() || undefined);
        if (live) setShown(r.pulls);
      } catch (e) {
        if (live) { setShown([]); setError(e instanceof Error ? e.message : String(e)); }
      }
    }, query ? 350 : 0);
    return () => { live = false; clearTimeout(t); };
  }, [repo, query]);
  return (
    <div className="mb-2 rounded-lg border border-line-strong bg-bg">
      <div className="flex items-center gap-2 border-b border-line px-2.5">
        <Search size={13} className="text-muted" />
        <input aria-label="Search the open pull requests" className="h-8 flex-1 bg-transparent text-[12.5px] outline-none placeholder:text-faint" placeholder="Search the open pull requests" value={query} onChange={(e) => setQuery(e.target.value)} />
        {shown === null && <Loader2 size={13} className="animate-spin text-muted" />}
      </div>
      <ul className="max-h-56 overflow-y-auto py-1">
        {error && <li className="px-3 py-2 text-[12px] text-fail">{error}</li>}
        {shown && !shown.length && !error && <li className="px-3 py-2 text-[12px] text-muted">{query ? "No open pull request matches." : "No open pull requests."}</li>}
        {shown?.map((p) => (
          <li key={p.number}>
            <button className="flex w-full items-start gap-2 px-3 py-1.5 text-left hover:bg-hover" onClick={() => onPick(p)}>
              <GitPullRequest size={13} className={`mt-0.5 flex-none ${p.draft ? "text-muted" : "text-done"}`} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[12.5px] text-fg">{p.title} <span className="font-mono text-muted">#{p.number}</span>{p.draft && <span className="ml-1 text-[11px] text-muted">draft</span>}</span>
                <span className="block truncate text-[11px] text-faint">
                  {p.author ? `${p.author}` : ""}{p.branch ? ` · ${p.branch}${p.base ? ` into ${p.base}` : ""}` : ""}{p.updated_at ? ` · updated ${day(p.updated_at)}` : ""}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
