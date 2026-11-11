import { useCallback, useEffect, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { create } from "zustand";
import { ArrowDown, ArrowUp, Bot, Check, ChevronDown, ChevronRight, Circle, CircleDot, Copy, ExternalLink, FolderGit2, GripVertical, Hourglass, KeyRound, ListTodo, Loader2, LogOut, Plus, ShieldCheck, SquareTerminal, Trash2 } from "lucide-react";
import { Api, ApiError, GITHUB_DEVICE_PAGE, claudeSignInLink, type AccountKind, type AccountOwnerView, type AccountRecordView, type AccountRow, type ClaudeStatus, type CodexStatus, type HoldRef, type IdentityReport, type LinearStatus, type ModelProviderView, type ProofpaneStatus, type SessionRecord, type Window } from "../api";
import { windowsWords } from "../agents";
import { confirmWays, type ConfirmWays } from "../passkey";
import { useStore } from "../store";
import { ConfirmIt, type Change } from "./AccountDialog";
import { AccountRecord, mismatchWords } from "./AgentTask";
import { Modal } from "./Modal";
import { closedHere, RiskTerms, TERMS_IN_FLOW, untilClosedHere } from "./RiskTerms";
import { AgentBadge } from "./bits";
import { clock, dayTime } from "../when";

const MORE = "pp-connections-more";

/** The workspace's own sign-ins: Claude Code (a link, then the code it shows), Codex and GitHub (a device code). They
 *  run as programs in the workspace, so the credentials stay on its volume; this page only carries the code across.
 *  The sign-ins come first; the rest (other models, Linear, Proofpane, programs that ask, your own programs) under More, closed until
 *  opened, and kept open on this device once it is. */
/** What Connections reads while it is open: Claude Code's sign-in, GitHub's, the approvals, and the accounts at once;
 *  then the workspace and its lists, Claude Code's sign-in and the accounts every 4 seconds while the page is visible.
 *  Its own hook, so that the Manager's place to sign an account in (ManagerAccountSetup) reads them
 *  as Connections does. */
export function useConnectionsReads() {
  const st = useStore();
  useEffect(() => {
    void st.refreshClaude();
    void st.refreshGithub();
    void st.refreshApprovals();         // whether agents may ask for sessions
    void st.refreshAccounts();
    let n = 0;
    // the workspace and its lists read again only once the last look's read of them has answered: a look while it is
    // under way joins it, as the store joins its reads of Claude Code's sign-in and of the accounts
    let lists: Promise<void> | null = null;
    const t = setInterval(() => {
      // nothing is read while the page is hidden: the place that borrows these reads may stay
      // open through a confirmation's wait
      if (document.visibilityState !== "visible") return;
      lists ??= useStore.getState().refresh().finally(() => { lists = null; });
      void useStore.getState().refreshClaude();
      // each account's sign-in, asked of its program, every third look; while one signs in, each look
      const signing = useStore.getState().signIns.some((r) => r.running && (r.program === "claude-sign-in" || r.program === "codex-sign-in"));
      void useStore.getState().refreshAccounts(signing || ++n % 3 === 0);
    }, 4000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}

export function Connections({ onClose }: { onClose: () => void }) {
  const [more, setMore] = useState(() => { try { return localStorage.getItem(MORE) === "1"; } catch { return false; } });
  const toggle = () => {
    const next = !more;
    setMore(next);
    try { if (next) localStorage.setItem(MORE, "1"); else localStorage.removeItem(MORE); } catch { /* this page only */ }
  };
  useConnectionsReads();

  return (
    <Modal title="Connections" onClose={onClose} width={600}>
      <div className="space-y-4">
        <ClaudeCard />
        <CodexCard />
        <GithubCard />
        <button className="flex w-full items-center gap-3 rounded-lg border border-line px-4 py-3 text-left hover:bg-hover"
          aria-expanded={more} onClick={toggle}>
          <ChevronRight size={16} className={`flex-none text-muted transition-transform ${more ? "rotate-90" : ""}`} />
          <span className="min-w-0 flex-1">
            <span className="block text-[13px] font-semibold">More</span>
            <span className="block text-[12px] text-muted">Other models in Claude Code, Linear, Proofpane, programs that ask for sessions, your own programs</span>
          </span>
        </button>
        {more && (
          <>
            <ModelsCard />
            <LinearCard />
            <ProofpaneCard />
            <AsksCard />
            <CustomAgentsCard />
          </>
        )}
        <p className="text-[11.5px] text-faint">
          The sign-ins run inside your workspace, and the keys are kept there: their credentials stay on its volume, and
          this app never keeps them.
        </p>
      </div>
    </Modal>
  );
}

function Card(props: { icon: React.ReactNode; title: string; status: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-line-strong p-4">
      <div className="flex items-center gap-3">
        {props.icon}
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-semibold">{props.title}</div>
          <div className="break-words text-[12px] text-muted">{props.status}</div>
        </div>
      </div>
      {props.children && <div className="mt-3">{props.children}</div>}
    </div>
  );
}

/** Claude Code in the workspace: its accounts (its own, and any the person added, each signed in on its own), each one
 *  row with the one new sessions start on and what tasks' relays know of it (ClaudeAccounts); every task's account
 *  record (AccountRecords); and keeping it current. The status line is the sign-in of the account new sessions start
 *  on. */
function ClaudeCard() {
  const claude = useStore((s) => s.claude);
  const view = useStore((s) => s.accounts?.claude);
  const active = view?.accounts.find((a) => a.id === view.active);
  const on = active && active.id !== "default" ? `${active.name}: ` : "";
  const status = !claude ? "Checking…"
    : !claude.installed ? "Claude Code is not installed in this workspace"
    : claude.signed_in ? `${on}Signed in${claude.email ? ` as ${claude.email}` : ""}${claude.subscription ? ` · ${claude.subscription}` : ""}`
    : claude.signed_in === null ? `${on}Its sign-in could not be read just now: it is asked again`
    : `${on}Not signed in: Claude sessions and chats need it`;
  return (
    <Card icon={<AgentBadge kind="claude" size={28} />} title="Claude Code" status={status}>
      {/* the relay's rows are the app's: listed, with a confirmation's Cancel, while Claude Code's status is not read
          (the workspace stopped, say), and not where Claude Code is known not to be installed */}
      {claude?.installed !== false && <ClaudeAccounts />}
      {claude?.installed && <AccountRecords />}
      {claude?.installed && <ClaudeUpdates />}
    </Card>
  );
}

/** Codex in the workspace: its accounts, as Claude Code's. Each signs in with Codex's own sign-in: a link and a code in a
 *  hosted workspace, its browser's on this Mac. */
function CodexCard() {
  const view = useStore((s) => s.accounts?.codex);
  // known not to be here (the workspace's programs, read when the app opens): never shown, not shown and then taken away
  const absent = useStore((s) => s.programs?.codex === false);
  const own = view?.accounts.find((a) => a.id === "default")?.status as CodexStatus | null | undefined;
  if (absent || (view && own?.installed === false)) return null;
  const active = view?.accounts.find((a) => a.id === view.active);
  const status = !view ? "Checking…" : `New sessions start on ${active?.id === "default" ? "its own sign-in" : active?.name}`;
  return (
    <Card icon={<AgentBadge kind="codex" size={28} />} title="Codex" status={status}>
      {view && <AccountRows kind="codex" />}
    </Card>
  );
}

const SIGN_IN: Record<AccountKind, string> = { claude: "claude-sign-in", codex: "codex-sign-in" };
const LABEL: Record<AccountKind, string> = { claude: "Claude Code", codex: "Codex" };

/** What an account's sign-in is, in words. */
export function accountWords(kind: AccountKind, row: AccountRow): string {
  const sessions = row.sessions ? ` · ${row.sessions} session${row.sessions === 1 ? "" : "s"}` : "";
  const st = row.status;
  if (!st) return `Checking…${sessions}`;
  if (!st.installed) return "Not installed in this workspace";
  // a sign-in the workspace could not read (its program slow to answer, say) is not taken for a sign-out
  if (st.signed_in === null) return `Sign-in not read just now${sessions}`;
  if (!st.signed_in) return `Not signed in${sessions}`;
  if (kind === "claude") {
    const c = st as ClaudeStatus;
    return `Signed in${c.email ? ` as ${c.email}` : ""}${c.subscription ? ` · ${c.subscription}` : ""}${sessions}`;
  }
  const method = (st as CodexStatus).method;
  return `Signed in${method === "chatgpt" ? " with ChatGPT" : method === "api_key" ? " with an API key" : ""}${sessions}`;
}

/** An account's windows, as its programs last recorded them: the 5-hour one and the 7-day one. */
function Windows({ usage }: { usage: AccountRow["usage"] }) {
  const parts: [string, Window][] = [];
  if (usage?.five_hour) parts.push(["5h", usage.five_hour]);
  if (usage?.seven_day) parts.push(["7d", usage.seven_day]);
  if (!parts.length) return null;
  return (
    <span className="flex flex-none items-center gap-2 text-[11px] text-fg-dim">
      {parts.map(([name, w]) => {
        const pct = Math.min(100, w.used_percentage);
        return (
          <span key={name} className="flex items-center gap-1" title={`${name} window: ${pct.toFixed(0)}% used`}>
            <span className="text-faint">{name}</span>
            <span className={`font-mono ${pct >= 95 ? "text-fail" : pct >= 80 ? "text-wait" : ""}`}>{pct.toFixed(0)}%</span>
          </span>
        );
      })}
    </span>
  );
}

/** Whether the program's own sign-in (the "Default" account) may be signed out from here: in a hosted workspace it
 *  is the workspace's, so yes; on the person's own machine it is their own Claude Code's or Codex's, which the
 *  workspace refuses to sign out (workspace.OWN_SIGN_IN). */
export function ownSignOutHere(provider: string | undefined | null): boolean {
  return !!provider && provider !== "local";
}

/** The acts on one program's accounts, shared by its rows and its add form: a change, then the workspace's views read
 *  again, and `more` (what the caller reads of an account besides, the relay's rows) after a change and after a
 *  failure, since a refusal can come with a change (an identity read again). One act at a time (`busy`); a failure
 *  said under the list. */
function useAccountActions(kind: AccountKind, more?: () => Promise<void>) {
  const [busy, setBusy] = useState("");
  const [armed, setArmed] = useState("");
  const [error, setError] = useState("");
  const again = async () => {
    const st = useStore.getState();
    await Promise.all([st.refreshAccounts(), st.refresh(), st.refreshQuota(), kind === "claude" ? st.refreshClaude() : Promise.resolve(),
                       more ? more() : Promise.resolve()]);
  };
  const act = async (what: string, fn: () => Promise<unknown>) => {
    setBusy(what); setError("");
    try { await fn(); await again(); } catch (e) { setError(message(e)); if (more) await more().catch(() => {}); } finally { setBusy(""); }
  };
  const signIn = async (account: string, stuck?: string) => {
    if (stuck) await Api.removeSession(stuck);          // a sign-in that waits on nothing more: a new one in its place
    return Api.createSession({ repo: null, program: SIGN_IN[kind], task: `Sign in to ${LABEL[kind]}`, account });
  };
  return { busy, armed, setArmed, error, act, signIn };
}

type Acts = ReturnType<typeof useAccountActions>;

/** One account's line, and what hangs under it: the circle that marks the account new sessions start on (chosen
 *  here), its name and its sign-in in words, its sessions and its windows, Sign in (or Start again, in place of a
 *  sign-in that waits on nothing more), Sign out and Remove; then a running sign-in's steps, or what a sign-in that
 *  just ended said. `lead` and `tail` are what the caller puts first and last on the line (the relay's handles);
 *  `children` go under it (the relay's part). The program's own account is signed out from here only in a hosted
 *  workspace (`hostedOwn`), and never removed. An account the workspace no longer lists (`a.row` missing) has the
 *  line and nothing to act on; while the store has not read the workspace's list at all (`listed` false), that is
 *  what its line says instead. */
function AccountLine({ kind, a, active, signIns, hostedOwn, listed = true, busy, armed, setArmed, act, signIn, lead, tail, children }: {
  kind: AccountKind; a: TaskAccount; active: boolean; signIns: SessionRecord[]; hostedOwn: boolean; listed?: boolean;
  busy: string; armed: string; setArmed: Dispatch<SetStateAction<string>>; act: Acts["act"]; signIn: Acts["signIn"];
  lead?: ReactNode; tail?: ReactNode; children?: ReactNode;
}) {
  const row = a.row;
  const signedIn = row?.status?.signed_in === true;
  const signedOut = !!row && row.status?.signed_in === false && row.status.installed !== false;
  const reported = a.owner?.report?.email;
  const signedInAs = signedIn && kind === "claude" ? (row?.status as ClaudeStatus).email : undefined;
  // the newest sign-in the workspace started for this account, while it is not signed in: running, its steps; ended
  // without signing it in, what it said last
  const newest = signedIn || !row ? undefined : signIns
    .filter((r) => r.sign_in === true && r.program === SIGN_IN[kind] && (r.account || "default") === row.id)
    .sort((x, y) => x.created_at - y.created_at)
    .pop();
  const signing = newest?.running ? newest : undefined;
  // just now: an old one, signed in then and signed out since, says nothing of this
  const ended = newest && !newest.running && signedOut && (newest.ended_at || 0) * 1000 > Date.now() - 10 * 60_000
    ? newest : undefined;
  return (
    <>
      <div className="flex items-center gap-2">
        {lead}
        {row ? (
          <button role="radio" aria-checked={active} aria-label={`New ${LABEL[kind]} sessions start on ${row.name}`}
            className={`flex-none ${active ? "text-accent" : "text-muted hover:text-fg"}`} disabled={!!busy}
            title={active ? "New sessions start on this account" : "Start new sessions on this account"}
            onClick={() => { if (!active) void act(`use-${row.id}`, () => Api.chooseAccount(kind, row.id)); }}>
            {active ? <CircleDot size={14} /> : <Circle size={14} />}
          </button>
        ) : <span className="w-[14px] flex-none" aria-hidden="true" />}
        <div className="min-w-0 flex-1">
          {/* the email its start reported, which tasks' relays know it by, beside its name wherever the sign-in's own
              words under it do not say it already (not read, signed out, gone, or signed in as another address): a
              held or confirmed account is named by its email however its sign-in reads now */}
          <div className="truncate text-[12.5px] font-medium">
            {a.name}{reported && reported !== signedInAs && <span className="font-normal text-muted"> · {reported}</span>}
          </div>
          <div className="truncate text-[11.5px] text-muted">
            {row ? accountWords(kind, row) : listed ? "No longer in the workspace" : "The workspace's accounts are not read yet"}
          </div>
        </div>
        {row && <Windows usage={row.usage} />}
        {row && signedOut && (
          <button className="btn btn-sm" disabled={!!busy} onClick={() => void act(`in-${row.id}`, () => signIn(row.id, signing?.id))}
            title={signing ? "Start its sign-in again, in place of the one waiting" : undefined}>
            {busy === `in-${row.id}` ? <Loader2 size={12} className="animate-spin" /> : null} {signing ? "Start again" : "Sign in"}
          </button>
        )}
        {row && signedIn && (row.id !== "default" || hostedOwn) && (
          // the workspace's own sign-in, which new sessions start on until another account is chosen for them (the
          // circle): its sign-out takes a second press, as Remove does; another account's is one
          <button className={`icon-btn ${armed === `out-${row.id}` ? "text-fail" : ""}`} disabled={!!busy}
            aria-label={armed === `out-${row.id}` ? `Click again to sign ${row.name} out` : `Sign ${row.name} out`}
            title={row.id !== "default" ? `Sign ${row.name} out`
              : armed === `out-${row.id}` ? `Click again to sign ${row.name} out`
              : `Sign ${row.name} out: the workspace's own sign-in, in two presses`}
            onClick={() => {
              if (row.id === "default" && armed !== `out-${row.id}`) {
                setArmed(`out-${row.id}`);
                setTimeout(() => setArmed((x) => (x === `out-${row.id}` ? "" : x)), 4000);
                return;
              }
              setArmed("");
              void act(`out-${row.id}`, () => Api.accountSignOut(row.id, kind));
            }}><LogOut size={13} /></button>
        )}
        {row && row.id !== "default" && row.sessions === 0 && (
          <button className={`icon-btn ${armed === row.id ? "text-fail" : ""}`} disabled={!!busy}
            title={armed === row.id ? "Click again to remove it" : `Remove ${row.name}: signed out, and its folder in the workspace removed`}
            aria-label={armed === row.id ? `Click again to remove ${row.name}` : `Remove ${row.name}`}
            onClick={() => {
              if (armed !== row.id) { setArmed(row.id); setTimeout(() => setArmed((x) => (x === row.id ? "" : x)), 4000); return; }
              setArmed("");
              void act(`rm-${row.id}`, () => Api.removeAccount(row.id));
            }}><Trash2 size={13} /></button>
        )}
        {tail}
      </div>
      {signing && kind === "claude" && <ClaudeSignInSteps session={signing} />}
      {signing && kind === "codex" && <CodexSignInWords session={signing} />}
      {ended && (
        <div className="mt-2 text-[12px]" role="status">
          <div className="text-wait">The sign-in ended without signing it in{ended.sign_in_text ? ". It said:" : "."}</div>
          {ended.sign_in_text && (
            <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-bg p-2 font-mono text-[11px] text-fg-dim">{ended.sign_in_text}</pre>
          )}
        </div>
      )}
      {children}
    </>
  );
}

/** Adding an account: a name, then its sign-in started at once. Adding a Claude Code account is risk point 6: its text
 *  opens over the form each time the form opens, and the form works with it open. */
function AddAccount({ kind, busy, act, signIn }: { kind: AccountKind; busy: string; act: Acts["act"]; signIn: Acts["signIn"] }) {
  const [name, setName] = useState("");
  const [adding, setAdding] = useState(false);
  const add = () => act("add", async () => {
    const made = await Api.addAccount(kind, name.trim());
    setName(""); setAdding(false);
    await signIn(made.id);
  });
  if (!adding) {
    return (
      <button className="btn btn-sm mt-2" disabled={!!busy} onClick={() => { setAdding(true); setName(""); }}>
        <Plus size={12} /> Add a {LABEL[kind]} account
      </button>
    );
  }
  return (
    <div className={`mt-2 ${TERMS_IN_FLOW}`}>
      <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim()) void add(); }}>
        <input className="input h-8 min-w-0 flex-1 text-[12.5px]" aria-label={`The new ${LABEL[kind]} account's name`} maxLength={40}
          placeholder="A name for it: Work, or the account's own" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        <button className="btn btn-sm btn-primary" disabled={!name.trim() || !!busy}>{busy === "add" ? <Loader2 size={12} className="animate-spin" /> : null} Add and sign in</button>
        <button type="button" className="btn btn-sm" onClick={() => setAdding(false)}>Cancel</button>
      </form>
      {/* made with the form, so the text opens each time the form does, and goes with it */}
      {kind === "claude" && <RiskTerms point={6} kind="control" open />}
    </div>
  );
}

const ACCOUNTS_NOTE = "Each account signs in on its own and keeps its own conversations; your settings and instructions carry over. A session stays on the account it started on.";

/** One program's accounts, where tasks do not run on them (Codex's): its own ("Default") and the ones added, each as
 *  one AccountLine, the one new sessions start on chosen among them; adding one; removing one no session runs on. */
export function AccountRows({ kind }: { kind: AccountKind }) {
  const view = useStore((s) => s.accounts?.[kind]);
  const signIns = useStore((s) => s.signIns);
  const hostedOwn = useStore((s) => ownSignOutHere(s.ws?.provider));
  const acts = useAccountActions(kind);
  if (!view) return null;
  const { busy, armed, setArmed, error, act, signIn } = acts;
  return (
    <div>
      <div className="mb-1.5 text-[11.5px] font-medium text-muted">Accounts</div>
      <ul className="divide-y divide-line rounded-md border border-line" role="radiogroup" aria-label={`The ${LABEL[kind]} account new sessions start on`}>
        {view.accounts.map((row) => (
          <li key={row.id} className="px-2.5 py-2">
            <AccountLine kind={kind} a={{ account: row.id, name: row.name, row }} active={row.id === view.active} signIns={signIns}
              hostedOwn={hostedOwn} busy={busy} armed={armed} setArmed={setArmed} act={act} signIn={signIn} />
          </li>
        ))}
      </ul>
      <AddAccount kind={kind} busy={busy} act={act} signIn={signIn} />
      <p className="mt-1.5 text-[11.5px] text-faint">{ACCOUNTS_NOTE}</p>
      {error && <div className="mt-1.5 text-[12px] text-fail">{error}</div>}
    </div>
  );
}

/** A running Claude sign-in: its page's link, and the code that page gives, typed into it. */
function ClaudeSignInSteps({ session }: { session: SessionRecord }) {
  const setError = useStore((s) => s.setError);
  const [code, setCode] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const link = claudeSignInLink(session.sign_in_url);
  const send = async () => {
    if (!code.trim()) return;
    setBusy(true);
    try { await Api.signInCode(session.id, code.trim()); setCode(""); setSent(true); } catch (e) { setError(e); } finally { setBusy(false); }
  };
  return (
    <ol className="mt-2 space-y-2.5 text-[12.5px]">
      <li className="flex items-center gap-2">
        <span className="text-muted">1.</span>
        {link ? (
          <a className="btn btn-sm" href={link} target="_blank" rel="noreferrer noopener"><ExternalLink size={12} /> Open Claude's sign-in page</a>
        ) : (
          <span className="flex items-center gap-2 text-muted"><Loader2 size={12} className="animate-spin" /> Waiting for Claude Code's sign-in link…</span>
        )}
      </li>
      <li className="flex items-center gap-2">
        <span className="text-muted">2.</span>
        <input aria-label="The code Claude's page shows" className="input flex-1 font-mono text-[12px]" placeholder="Paste the code Claude shows you" value={code} onChange={(e) => setCode(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} />
        <button className="btn btn-primary" disabled={busy || !code.trim()} onClick={send}>Send</button>
      </li>
      {sent && <li className="text-muted">Code sent; Claude Code is checking it. This says it is signed in once it is.</li>}
    </ol>
  );
}

/** A running Codex sign-in, in its own words: where to sign in and the code to give there, its links opened here. */
function CodexSignInWords({ session }: { session: SessionRecord }) {
  const text = session.sign_in_text || "";
  if (!text) return <div className="mt-2 flex items-center gap-2 text-[12px] text-muted"><Loader2 size={12} className="animate-spin" /> Waiting for Codex's sign-in…</div>;
  // only OpenAI's own sign-in pages are links: the page never opens another address a program printed
  const parts = text.split(/(https:\/\/auth\.openai\.com\/[^\s"'<>]*)/g);
  return (
    <pre aria-label="Codex's sign-in" className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-bg p-2 font-mono text-[11.5px] text-fg-dim">
      {parts.map((part, i) => (i % 2 === 1
        ? <a key={i} className="text-accent hover:underline" href={part} target="_blank" rel="noreferrer noopener">{part}</a>
        : <span key={i}>{part}</span>))}
    </pre>
  );
}

const SEAT_PLANS = ["Claude Team", "Claude Enterprise"];
// what an account's drag carries, since some browsers start a drag only with data: a type of the page's own, which a
// field it is dropped on does not take as text
const ORDER_TYPE = "application/x-proofpane-account";
const ASKED = "Is this account yours? Use only your own accounts here, never a colleague's.";
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** An account's group, from its kind: your personal plans, or its organisation's seats, Team or Enterprise. */
export function groupWords(o: Pick<AccountOwnerView, "grp_kind" | "report">): string {
  if (o.grp_kind === "personal") return "your personal plans";
  if (o.grp_kind === "team" || o.grp_kind === "enterprise") {
    return `${o.report?.organization ?? "its organisation"}'s seats, ${o.grp_kind === "team" ? "Team" : "Enterprise"}`;
  }
  return "in no group: a task does not run on its plan";
}

/** When a confirmation that waits takes effect, worded as Encryption's holdWords words a hold's end. */
export function joinsWords(at: number, now = Date.now() / 1000): string {
  return `It joins your tasks' relay at ${at - now > 3600 ? dayTime(at) : clock(at)}, unless you cancel it first.`;
}

/** A confirmed account waiting to join whose identity, read again since, disagrees with its sign-in's record: the app
 *  lets the wait apply only to an account found as it was confirmed, so this one's ends without it joining (the row
 *  goes back to reported, or to changed) unless a read again finds it so first. Said with the mismatch, before the
 *  wait, on the row and on the New task form's pointer alike. */
export function waitEndsWords(hold: HoldRef | null, now = Date.now() / 1000): string {
  const at = hold ? ` at ${hold.takes_effect_at - now > 3600 ? dayTime(hold.takes_effect_at) : clock(hold.takes_effect_at)}` : "";
  return `It was confirmed before this read: its wait to join your tasks' relay ends${at} without it joining, unless a read again finds it as it was confirmed.`;
}

/** What the reads by themselves hold for the page, shared by every mount of Claude Code's list (Connections closes and
 *  opens; a read under way goes on): the accounts read since the page's account signed in, each once until seen signed
 *  out; those seen signed out, read again at their next sign-in; the chain the reads run on, one after another; the
 *  account being read; each account's last failure, kept until its next read or until the page sees it signed out; and
 *  a count moved at each read's end, on which the list mounted then reads the owners again. It is the signed-in
 *  account's: kept across a sign-out and the same account's sign-in again (a read under way at the sign-out is not
 *  made again), and forgotten when another account signs in on the page, when a read under way or waiting its turn
 *  ends without a word (its generation moved); a test forgets it with forgetAccountReads. An account waiting its turn
 *  is in `queued` until its read starts: a press of Read who it is meanwhile reads it at once and takes it off, so the
 *  chain does not read it a second time, and the page seeing it signed out takes it off, so that none is made of an
 *  account signed out; and a press marks its account read (`readsAsked`), so that no read by itself is queued behind
 *  it. `asking` is whether a read by itself waits on risk point 16's text (termsClosed). `waiting` is `queued` as the
 *  rows see it, the accounts whose read waits its turn, on which their Yes, it is mine waits,
 *  except while `asking`: the text holds nothing, so a read that waits on it holds no Yes.
 *  Every change to `queued` goes through queueAdd and queueDrop, which keep the two the same, but
 *  forgetAccountReads's, which empties both. */
const useReads = create<{ reading: string; failed: Record<string, string>; version: number; asking: boolean;
  waiting: string[] }>(() => ({ reading: "", failed: {}, version: 0, asking: false, waiting: [] }));
const readsAsked = new Set<string>();
const seenOut = new Set<string>();
const queued = new Set<string>();
const queueAdd = (id: string): void => { queued.add(id); useReads.setState({ waiting: [...queued] }); };
const queueDrop = (id: string): boolean => {
  const had = queued.delete(id);
  if (had) useReads.setState({ waiting: [...queued] });
  return had;
};
/** Each mount of the list's confirmations, for the reads by themselves: the account whose confirmation it holds open
 *  (`open`), and the account the latest press of Yes, it is mine there is opening one for while its ways are read
 *  (`opening`). A read by itself of an account one of them names is not made when its turn comes, since its report
 *  could change under the confirmation: it is set aside (`setAside`, the account and its name), and queued again as
 *  soon as no mount names it (released), whatever ended the confirmation, unless the page has seen the account signed
 *  out meanwhile, a press of Read it again has read it, or another account has signed in on the page
 *  (forgetAccountReads), which drop it. A mount's entry goes with the mount. */
const confirmations = new Map<object, { open?: string; opening?: string }>();
const confirmingHere = (account: string) => [...confirmations.values()].some((c) => c.open === account || c.opening === account);
const setAside = new Map<string, string>();
let readChain: Promise<void> = Promise.resolve();
let generation = 0;
// the page's account as it stands when this module loads, which is after the sign-in (the app loads Connections
// lazily): so the store's first change, of any kind, is not taken for another account signing in
let pageUser: string | null = useStore.getState().user?.id ?? null;
const without = (failed: Record<string, string>, id: string) => Object.fromEntries(Object.entries(failed).filter(([k]) => k !== id));
export function forgetAccountReads(): void {
  generation += 1;
  readsAsked.clear();
  seenOut.clear();
  queued.clear();
  setAside.clear();
  readChain = Promise.resolve();
  useReads.setState({ reading: "", failed: {}, version: 0, asking: false, waiting: [] });
}

/** Risk point 16: a read by
 *  itself waits until this browser keeps the owner's close of that text for the page's account, the text open over the
 *  list meanwhile (`asking`); where it keeps one, it waits for nothing. So the first read by itself in a browser opens
 *  the text, and so does the next in a browser whose storage was cleared since. A page with no account waits for
 *  nothing here, and the read's own look below makes no read for it. */
async function termsClosed(mine: number): Promise<void> {
  const account = useStore.getState().user?.id ?? "";
  if (!account || closedHere(16, account)) return;
  if (mine === generation) useReads.setState({ asking: true });
  await untilClosedHere(16, account);
  if (mine === generation) useReads.setState({ asking: false });
}

/** A read by itself of the account (the workspace's row's id and name), queued at the end of the page's chain, where
 *  the looks below decide in its turn whether it is made; the effect in ClaudeAccounts says which ones are queued,
 *  and `released` queues again one set aside. */
function queueRead(id: string, name: string): void {
  readsAsked.add(id);
  queueAdd(id);
  const mine = generation;
  readChain = readChain.then(async () => {
    // risk point 16: no read by itself until its text has opened in this browser and the owner has closed it
    // (termsClosed); before a press's own check below, so that a press while it waits reads at once, and alone
    await termsClosed(mine);
    // another account signed in while it waited its turn, a press read it meanwhile, or the page saw it signed out
    if (mine !== generation || !queueDrop(id)) return;
    // the workspace stopped since it was queued, or this browser keeps no close of point 16's text for the page's
    // account now (none is signed in): no read now, and a later look queues it again
    if (!useStore.getState().ws?.running || !closedHere(16, useStore.getState().user?.id ?? "")) { readsAsked.delete(id); return; }
    // its confirmation open on a mount of the list, or opening (confirmations): set aside, not read under it, and
    // queued again as soon as it ends (released). Looked at with nothing awaited before the row says it is read, from
    // which its Yes, it is mine waits, so that no confirmation opens on this page over this read either
    if (confirmingHere(id)) { setAside.set(id, name); return; }
    useReads.setState({ reading: id });
    let failure = "";
    try {
      // decided at the read by the app's own rows, read again now, not by the page's copy, which can be older than
      // a confirmation (this page's own, just made, or another page's): one confirmed or waiting to join is not
      // read, and the list reads the rows again
      const now = (await Api.taskAccounts()).accounts.find((x) => x.account === id);
      if (now && (now.state === "confirmed" || now.state === "held")) {
        if (mine === generation) useReads.setState((s) => ({ reading: "", version: s.version + 1 }));
        return;
      }
      if (mine !== generation) return;
      await Api.reportAccount(id);
    }
    catch (e) { failure = `${name}'s identity could not be read by itself: ${message(e)} Read who it is tries again.`; }
    if (mine !== generation) return;                                    // or while it ran: nothing of it is the new account's
    useReads.setState((s) => ({ reading: "", version: s.version + 1, failed: failure ? { ...s.failed, [id]: failure } : without(s.failed, id) }));
  });
}

/** Each read set aside whose account no mount's confirmation names now (confirmations), queued again: called wherever
 *  a confirmation ends, or a press of Yes, it is mine ends or gives way to another, Connections' own close among them,
 *  so that a read queued while Connections was open is not lost when it closes. */
function released(): void {
  for (const [id, name] of [...setAside]) {
    if (confirmingHere(id)) continue;
    setAside.delete(id);
    queueRead(id, name);
  }
}

useStore.subscribe((s) => {
  const id = s.user?.id ?? null;
  if (id === null || id === pageUser) return;                 // signed out, or the same account again: kept
  pageUser = id;
  forgetAccountReads();
});

/** What an account's frozen start reported of it, each field as it came, "none" where it gave none; and, where the read
 *  kept one, the orgId its `auth status` gave, which a confirmation names beside the report, so that a read that
 *  changed the orgId alone shows on the row too. */
function Report({ report, orgId }: { report: IdentityReport; orgId?: string | null }) {
  const fields: [string, string | null][] = [["Email", report.email], ["Organisation", report.organization], ["Plan", report.plan],
    ["Token source", report.token_source], ["Key source", report.api_key_source], ["Provider", report.api_provider]];
  if (orgId) fields.push(["Organisation id", orgId]);
  return (
    <dl aria-label="What its start reported" className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 rounded-md border border-line bg-bg px-2.5 py-1.5 text-[11.5px]">
      {fields.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-faint">{label}</dt>
          <dd className="min-w-0 break-all font-mono text-fg-dim">{value ?? "none"}</dd>
        </div>
      ))}
    </dl>
  );
}

/** One of the workspace's Claude Code accounts as tasks see it: the workspace's row (`row`, as the store keeps it) and
 *  the app's account-owner row (`owner`), either one missing. */
export interface TaskAccount { account: string; name: string; row?: AccountRow; owner?: AccountOwnerView }

/** The accounts in the relay's order: those the app has a row for, by its position (the workspace's order between
 *  equals), then the others in the workspace's order. A row whose account the workspace no longer lists stays, so that
 *  a confirmation waiting on it can still be cancelled. */
export function taskAccountList(rows: AccountRow[] | null | undefined, owners: AccountOwnerView[]): TaskAccount[] {
  const all = rows ?? [];
  const at = (account: string) => { const i = all.findIndex((r) => r.id === account); return i < 0 ? all.length : i; };
  const own = [...owners].sort((a, b) => a.position - b.position || at(a.account) - at(b.account)).map((o) => {
    const row = all.find((r) => r.id === o.account);
    return { account: o.account, name: row?.name || o.name || (o.account === "default" ? "Default" : o.account), row, owner: o };
  });
  return [...own, ...all.filter((r) => !owners.some((o) => o.account === r.id)).map((r) => ({ account: r.id, name: r.name, row: r }))];
}

/** The note over the list. Its relay clause (a task goes on on the next account of its group) is said only for a relay
 *  known to be on: the relay is off unless the deployment sets it, and where it is off, or the page cannot tell
 *  (not read yet, or the read failed), the clause would say a move that is not made. */
const relayNote = (relayOn: boolean | null) => "One row is one account: its sign-in, and what your tasks' relay knows of it. The filled circle marks the "
  + "account new sessions start on. The order is the relay's"
  + (relayOn ? ": when an account runs out of room, a task goes on on the next of its group" : "") + "; drag a row to change it.";

/** Claude Code's accounts as one list, each row one account: its sign-in (AccountLine) and what tasks' relays know of
 *  it, in the relay's order, set by dragging or with Move up and Move down. One not yet confirmed is asked "Is this
 *  account yours?": who it is is read from a frozen start of its claude in the workspace, at a press of Read who it is,
 *  and by itself in a hosted workspace as the effect below says; shown field for field as it came, and confirmed with
 *  the owner's own word (ConfirmIt), made for task-account-own:<its row's id>, which names the report and orgId the row
 *  showed at Yes, it is mine: the app refuses it where the account was read again since, and the row then shows the
 *  report as read now, with the app's words, to be confirmed again; it joins tasks' relays when the wait that follows
 *  ends, which can be cancelled until then, and one whose identity changed since is in no relay until it is confirmed
 *  again. A confirmed one shows its plan, organisation and group, its windows as last recorded, whether it is in tasks'
 *  relays, and Use to the limit, which lifts its weekly reserve. The relay's rows are read when the list opens, after
 *  each change, and once a wait's end has passed, and listed whether or not the store has read the workspace's list of
 *  accounts, so that a wait can be cancelled either way. Risk point 6's text opens over a confirmation (its seat part
 *  for a Team or Enterprise seat), point 2's too where another account is confirmed or waits to join, and point 9's
 *  as Use to the limit is turned on: each every time, the control working with it open. Risk point 16's opens over the
 *  list as the page would first read an account by itself in this browser, and that read waits until the owner has
 *  closed it (termsClosed); a press of Read who it is never waits on it. */
export function ClaudeAccounts() {
  const view = useStore((s) => s.accounts?.claude);
  const signIns = useStore((s) => s.signIns);
  const hostedOwn = useStore((s) => ownSignOutHere(s.ws?.provider));
  const running = useStore((s) => !!s.ws?.running);
  const [owners, setOwners] = useState<AccountOwnerView[] | null>(null);
  const [unread, setUnread] = useState("");
  // the confirmation open, its account's, and what its row showed when the owner pressed Yes, it is mine (`shown`),
  // which it names to the app: the report and orgId as they were then, not as the list holds them when the key
  // is given
  const [confirming, setOpen] = useState<{ id: string; account: string; change: Change; shown: { report: IdentityReport | null; org_id: string | null } } | null>(null);
  // this list's entry in `confirmations`, for the reads by themselves: `alive` while the list is mounted, and `press`
  // the latest press of Yes, it is mine whose ways are still being read; a press a later one, or the list's close,
  // took over does nothing when its ways come
  const [mount] = useState<{ alive: boolean; press: object | null }>(() => ({ alive: true, press: null }));
  const entry = () => {
    const e = confirmations.get(mount) ?? {};
    confirmations.set(mount, e);
    return e;
  };
  // every change of the confirmation goes through here, so that the reads by themselves know which account's is open,
  // and one ending releases the reads it set aside
  const setConfirming = (c: typeof confirming) => {
    setOpen(c);
    if (!mount.alive) return;
    const e = entry();
    if (c) e.open = c.account; else delete e.open;
    released();
  };
  useEffect(() => {
    mount.alive = true;
    return () => { mount.alive = false; mount.press = null; confirmations.delete(mount); released(); };
  }, [mount]);
  // the app's words where it refused a confirmation because the account was read again since its row showed it (409),
  // on that row until its next Yes, it is mine
  const [readSince, setReadSince] = useState<{ id: string; words: string } | null>(null);
  // how many times each account's Use to the limit was turned on here: each time opens risk point 9's text again
  const [lifted, setLifted] = useState<Record<string, number>>({});
  const [dragged, setDragged] = useState<string | null>(null);
  // what the reads by themselves hold for the page (useReads): the account being read, for its row to say so, each
  // account's last failure, the count moved at each read's end, on which the owners are read again, and whether one
  // waits on risk point 16's text
  const reading = useReads((s) => s.reading);
  const waiting = useReads((s) => s.waiting);
  const failed = useReads((s) => s.failed);
  const version = useReads((s) => s.version);
  const asking = useReads((s) => s.asking);
  // whether this deployment has the relay on, from the task list's settings, for the note over the list: unknown (null)
  // until read, and where the read fails; an answer that holds no settings, which the app's route never
  // gives, is read as a relay not on, and throws nothing here
  const [relayOn, setRelayOn] = useState<boolean | null>(null);
  useEffect(() => {
    let live = true;
    Api.tasks().then((r) => { if (live) setRelayOn(r?.settings?.relay === true); }, () => {});
    return () => { live = false; };
  }, []);
  const loading = useRef(false);
  const load = useCallback(async () => {
    loading.current = true;
    try { setOwners((await Api.taskAccounts()).accounts); setUnread(""); } catch (e) { setUnread(message(e)); } finally { loading.current = false; }
  }, []);
  const acts = useAccountActions("claude", load);
  // read at the start, and again after each read by itself, whichever mount of this list made it
  useEffect(() => { void load(); }, [load, version]);
  // a read at the start that failed (the app away for a moment) is tried again at each of the store's own reads of the
  // accounts, until one lands: the reads by themselves wait on it, and a row promises them
  const rows = view?.accounts;
  useEffect(() => { if (owners === null && !loading.current) void load(); }, [rows, owners, load]);
  // a wait takes effect at the app's first read after its end: read again a second after the first one ends, and every
  // 5 seconds while one past its end still waits (the server's clock behind this device's)
  useEffect(() => {
    const ends = (owners ?? []).flatMap((o) => (o.state === "held" && o.hold ? [o.hold.takes_effect_at * 1000] : []));
    if (!ends.length) return;
    const left = Math.min(...ends) - Date.now();
    const t = setTimeout(() => void load(), left > 0 ? left + 1000 : 5000);
    return () => clearTimeout(t);
  }, [owners, load]);
  // read by itself while a hosted workspace runs (the read starts the account's claude there), one after another,
  // each once: a signed-in account the app has no row for, and one neither confirmed nor waiting to join that signed
  // in again since the page saw it signed out. Never a confirmed account or one waiting to join: the read would start
  // its claude to learn what its confirmation holds already (and a read that found nothing writes nothing over such an
  // account, the app answering `kept`), and every start of a task's attempt compares its own report with
  // the confirmed one anyway. This list's copy of the
  // app's rows decides what is queued, and the app's rows read again at the read decide what is read, since the copy
  // can be older than a confirmation. Never one whose confirmation is open on this page, or opening, whose report could
  // change under it: none is queued while this list holds its confirmation open, and one queued before, or while it
  // opens, is set aside when its turn comes and queued again as soon as that confirmation ends, however it ends
  // (queueRead, released); a confirmation given meanwhile names the report the row showed, and the read then queued is
  // not made, the account waiting to join. Its Yes, it is mine waits while this page reads it by itself, or holds its
  // read waiting its turn, but not while that read waits on risk point 16's text,
  // which holds nothing.
  // Nothing once the workspace has stopped; and nothing on the person's own machine (the local provider), where the
  // start would be of their own claude. Reads queued while Connections is open are made from then whether it stays
  // open or not. A read by itself that fails is said on its row, once, until its next read or until the page sees the
  // account signed out, and Read who it is reads at a press. The record of the reads is the page's (useReads), so
  // that closing and opening Connections mid-read starts no second read of the same account, and the owners are read
  // again whichever mount the read ran from; a read that another account's sign-in overtook ends without a word. No
  // read by itself runs until risk point 16's text has opened in this browser and the owner has closed it
  // (termsClosed), the text opening over the list as the first would run
  useEffect(() => {
    if (!rows || owners === null || !running || !hostedOwn) return;
    for (const row of rows) {
      if (row.status?.signed_in === false) {
        seenOut.add(row.id);
        // a read of it waiting its turn, or set aside, is dropped, so that none is made of an account signed out; its
        // sign-in again is looked at as any account's is: not queued while this list holds its confirmation open, but
        // at the look after that ends, of which Connections closing makes none
        readsAsked.delete(row.id);
        queueDrop(row.id);
        setAside.delete(row.id);
        if (useReads.getState().failed[row.id]) useReads.setState((s) => ({ failed: without(s.failed, row.id) }));
        continue;
      }
      if (row.status?.signed_in !== true) continue;
      const o = owners.find((x) => x.account === row.id);
      if (readsAsked.has(row.id)) continue;
      if (o && (o.state === "confirmed" || o.state === "held" || !seenOut.has(row.id) || confirming?.id === o.id)) continue;
      queueRead(row.id, row.name);
    }
  }, [rows, owners, running, hostedOwn, confirming]);
  // nothing to list until either side has an account: the workspace's (the store's) or the app's rows
  if (!view && !owners?.length) return null;
  const { busy, armed, setArmed, error, act, signIn } = acts;

  const ask = async (o: AccountOwnerView, name: string) => {
    setUnread(""); setConfirming(null); setReadSince(null);
    // from the press, while its ways are read: no read of the account by itself meanwhile (confirmations), and a press
    // here before it, still reading its ways, gives way to it, its account's reads released
    const press = {};
    mount.press = press;
    entry().opening = o.account;
    released();
    const shown = { report: o.report, org_id: o.org_id };
    let ways: ConfirmWays;
    try { ways = await confirmWays(); }
    catch (e) {
      if (mount.press !== press) return;              // a later press, or the list's close, took over
      mount.press = null;
      delete entry().opening;
      released();
      setUnread(message(e));
      return;
    }
    if (mount.press !== press) return;
    mount.press = null;
    setConfirming({ id: o.id, account: o.account, shown, change: { what: "task-account-own", id: o.id, label: `Confirming “${name}” as your own`,
      action: "Confirm", subject: `task-account-own:${o.id}`, ways,
      note: "Once confirmed, it waits before it joins your tasks' relay, and you can cancel it until then." } });
    delete entry().opening;
  };
  // a confirmation names what its row showed; where the account was read again since (409), it closes, the list is
  // read again to show the report as read now, and the app's words stand on the row, since trying again from here
  // would name the same report. Any other failure (a confirmation expired, a mismatch) shows in ConfirmIt, which
  // stays open to try again
  const confirmed = (id: string, shown: { report: IdentityReport | null; org_id: string | null }) => async (confirmation?: string) => {
    try { await Api.confirmAccount(id, confirmation, shown); }
    catch (e) {
      if (!(e instanceof ApiError && e.status === 409)) throw e;
      setConfirming(null);
      setReadSince({ id, words: e.message });
      await load();
      return;
    }
    setConfirming(null);
    await load();
  };
  // turned on, its text opens under the switch as it is, every time; turned off, it opens nothing
  const lift = (id: string, on: boolean) => {
    if (on) setLifted((was) => ({ ...was, [id]: (was[id] ?? 0) + 1 }));
    void act(`reserve-${id}`, () => Api.putAccount(id, { reserve_lifted: on }));
  };
  // about to be turned on: its text opens as the pointer comes to the switch or it takes the
  // keyboard, before the press, holding nothing
  const soon = (id: string, isOn: boolean) => () => { if (!isOn) setLifted((was) => ({ ...was, [id]: (was[id] ?? 0) + 1 })); };

  const list = taskAccountList(view?.accounts, owners ?? []);
  const ids = list.flatMap((a) => (a.owner ? [a.owner.id] : []));
  // one change at a time: a drop while an order goes would be made from the list as it was before it
  const order = (id: string, to: number) => {
    const from = ids.indexOf(id);
    if (busy || from < 0 || to < 0 || to >= ids.length || to === from) return;
    const next = ids.filter((x) => x !== id);
    next.splice(to, 0, id);
    void act("order", () => Api.orderAccounts(next));
  };
  // another account confirmed, or waiting to join: this one would be a second in the relay (risk point 2)
  const second = (id: string) => (owners ?? []).some((o) => o.id !== id && (o.state === "confirmed" || o.state === "held"));

  // `item`'s own `asking` is its account's confirmation open; this is risk point 16's text open over the list
  const termsOpen = asking;
  const item = (a: TaskAccount) => {
    const o = a.owner;
    const at = o ? ids.indexOf(o.id) : -1;
    const asking = !!o && confirming?.id === o.id;
    const signedOut = a.row?.status?.signed_in === false;
    const lead = o ? (
      <span draggable aria-hidden="true" title="Drag it to change the order" className="flex-none cursor-grab text-faint hover:text-fg"
        onDragStart={(e) => { setDragged(o.id); e.dataTransfer?.setData(ORDER_TYPE, o.id); if (e.dataTransfer) e.dataTransfer.effectAllowed = "move"; }}
        onDragEnd={() => setDragged(null)}><GripVertical size={13} /></span>
    ) : <span className="w-[13px] flex-none" aria-hidden="true" />;
    const tail = o ? (
      <>
        <button className="icon-btn !h-6 !w-6" disabled={!!busy || at === 0} aria-label={`Move ${a.name} up`} title="Move it up"
          onClick={() => order(o.id, at - 1)}><ArrowUp size={12} /></button>
        <button className="icon-btn !h-6 !w-6" disabled={!!busy || at === ids.length - 1} aria-label={`Move ${a.name} down`} title="Move it down"
          onClick={() => order(o.id, at + 1)}><ArrowDown size={12} /></button>
      </>
    ) : null;
    return (
      <li key={a.account} className={`px-2.5 py-2 ${o && dragged === o.id ? "opacity-50" : ""}`}
        onDragOver={(e) => { if (o && dragged && dragged !== o.id) e.preventDefault(); }}
        onDrop={(e) => { e.preventDefault(); const from = dragged; setDragged(null); if (o && from) order(from, at); }}>
        <AccountLine kind="claude" a={a} active={!!a.row && a.row.id === view?.active} signIns={signIns} hostedOwn={hostedOwn} listed={!!view}
          busy={busy} armed={armed} setArmed={setArmed} act={act} signIn={signIn} lead={lead} tail={tail}>
          {o?.state === "confirmed" ? (
            <div className={`mt-1 pl-[21px] text-[11.5px] text-muted ${TERMS_IN_FLOW}`}>
              <div>{o.report?.plan ?? "No plan reported"} · {o.report?.organization ?? "no organisation"} · {groupWords(o)}</div>
              <div>Windows as last recorded: {windowsWords(o.windows)}</div>
              <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-fg">
                <label className="flex items-center gap-1.5">
                  <input type="checkbox" checked={o.in_relays} disabled={!!busy}
                    onChange={(e) => { const on = e.target.checked; void act(`relays-${o.id}`, () => Api.putAccount(o.id, { in_relays: on })); }} />
                  In tasks' relays
                </label>
                <label className="flex items-center gap-1.5" title="An unattended start keeps off an account whose week is near its limit; this lifts that for this account"
                  onMouseEnter={soon(o.id, o.reserve_lifted)}>
                  <input type="checkbox" checked={o.reserve_lifted} disabled={!!busy} onFocus={soon(o.id, o.reserve_lifted)} onChange={(e) => lift(o.id, e.target.checked)} />
                  Use to the limit
                </label>
              </div>
              {/* under the switches, where the text has the room of the whole account */}
              {(lifted[o.id] ?? 0) > 0 && <RiskTerms key={`limit-${lifted[o.id]}`} point={9} kind="control" open />}
            </div>
          ) : o?.state === "held" && !o.mismatch ? (
            <div className="mt-1 flex items-center gap-2 pl-[21px] text-[12px]">
              <Hourglass size={12} className="flex-none text-wait" />
              <span className="min-w-0 flex-1 text-wait">{o.hold ? joinsWords(o.hold.takes_effect_at) : "It joins your tasks' relay when its wait ends."}</span>
              {o.hold && (
                <button className="btn btn-sm btn-danger flex-none" disabled={!!busy} onClick={() => void act(`cancel-${o.id}`, () => Api.cancelHold(o.hold!.id))}>
                  {busy === `cancel-${o.id}` ? <Loader2 size={12} className="animate-spin" /> : null} Cancel
                </button>
              )}
            </div>
          ) : (
            <div className="mt-1 pl-[21px] text-[12px]">
              {o?.mismatch ? (
                // a held one's wait is said after the mismatch, which decides it (waitEndsWords), as the New task form's
                // pointer does: the two surfaces say the same of the same row
                <div className="text-wait">
                  It cannot be confirmed: {mismatchWords(o.mismatch)}. It is in no relay.
                  {o.state === "held" && ` ${waitEndsWords(o.hold)}`}
                </div>
              ) : (
                <>
                  {o?.state === "changed" && <div className="text-wait">Its identity changed since you confirmed it: it is in no relay until you confirm it again.</div>}
                  <div className="font-medium">{ASKED}</div>
                  {o?.state !== "changed" && <div className="text-muted">Until you confirm it, and its wait after that ends, it is in no relay.</div>}
                </>
              )}
              {o?.report && <Report report={o.report} orgId={o.org_id} />}
              {reading === a.account && (
                <div className="mt-1 flex items-center gap-2 text-muted" role="status"><Loader2 size={12} className="animate-spin" /> Reading who it is from its start…</div>
              )}
              {failed[a.account] && <div className="mt-1 text-wait" role="status">{failed[a.account]}</div>}
              {o && readSince?.id === o.id && <div className="mt-1 text-wait" role="status">{readSince.words}</div>}
              <div className="mt-1.5 flex flex-wrap items-center gap-2">
                {/* waits while this page reads its account by itself, or holds such a read waiting its turn,
                    as it does while a press's read runs (busy): the report it would
                    name may change. Not while risk point 16's text is open: no read by itself is made then, so every
                    read queued waits on it, behind another's or not; the text holds nothing but itself. A confirmation
                    still open when the read's turn comes, the text closed, sets that read aside; one given before then
                    leaves it unmade, its account held */}
                {o?.report && !o.mismatch && (
                  <button className="btn btn-sm btn-primary" disabled={!!busy || asking || reading === a.account || (waiting.includes(a.account) && !termsOpen)} onClick={() => void ask(o, a.name)}>
                    {o.state === "changed" ? "Confirm again" : "Yes, it is mine"}
                  </button>
                )}
                {/* a press reads it at once, so the chain makes no read of it after: none queued, none set aside */}
                <button className="btn btn-sm" disabled={!!busy || signedOut || reading === a.account}
                  onClick={() => { queueDrop(a.account); setAside.delete(a.account); readsAsked.add(a.account); useReads.setState((s) => ({ failed: without(s.failed, a.account) })); void act(`read-${a.account}`, () => Api.reportAccount(a.account)); }}>
                  {busy === `read-${a.account}` ? <Loader2 size={12} className="animate-spin" /> : null} {o?.report ? "Read it again" : "Read who it is"}
                </button>
                {o?.state === "held" && o.hold && (
                  <button className="btn btn-sm btn-danger" disabled={!!busy} onClick={() => void act(`cancel-${o.id}`, () => Api.cancelHold(o.hold!.id))}>
                    {busy === `cancel-${o.id}` ? <Loader2 size={12} className="animate-spin" /> : null} Cancel the wait
                  </button>
                )}
              </div>
              {/* read by itself at its sign-in only where the effect above reads one: in a hosted workspace, a row
                  not waiting to join */}
              {signedOut && <div className="mt-1 text-muted">{hostedOwn && o?.state !== "held"
                ? "It is not signed in: sign it in, and who it is is then read by itself."
                : "It is not signed in: sign it in, then read who it is."}</div>}
              {asking && confirming && (
                // the texts are made with the confirmation, so they open each time it does, and go with it; they stand
                // under it, one under another, so neither covers the other
                <div className={TERMS_IN_FLOW}>
                  <ConfirmIt key={confirming.id} change={confirming.change} onConfirmed={confirmed(confirming.id, confirming.shown)} onCancel={() => setConfirming(null)} />
                  {second(confirming.id) && <RiskTerms point={2} kind="control" open />}
                  <RiskTerms point={6} kind="control" open cases={SEAT_PLANS.includes(o?.report?.plan ?? "") ? ["seat"] : []} />
                </div>
              )}
            </div>
          )}
        </AccountLine>
      </li>
    );
  };

  return (
    <div>
      <div className="mb-1 text-[11.5px] font-medium text-muted">Accounts</div>
      <p className="mb-1.5 text-[11.5px] text-faint">{relayNote(relayOn)}</p>
      {/* risk point 16, over the accounts, in the page's flow: open while a read by itself waits on its close */}
      <div className={`relative ${TERMS_IN_FLOW}`}>
        <RiskTerms point={16} kind="before" open={asking} />
      </div>
      {unread && <div role="status" className="mb-1.5 text-[12px] text-wait">{unread}</div>}
      <ul className="divide-y divide-line rounded-md border border-line" aria-label="Your accounts for tasks, in the relay's order">
        {list.map(item)}
      </ul>
      {view && <AddAccount kind="claude" busy={busy} act={act} signIn={signIn} />}
      <p className="mt-1.5 text-[11.5px] text-faint">{ACCOUNTS_NOTE}</p>
      {error && <div role="alert" className="mt-1.5 text-[12px] text-fail">{error}</div>}
    </div>
  );
}

/** Which accounts did the work, every task of every agent in one place: each task's account record (AgentTask's
 *  AccountRecord, with its Delete where the task is deleted), newest task first, read when this is opened and after
 *  a Delete. The same records as under each agent's Task section, which is where a record is read task by task;
 *  here they answer "which accounts did any of my work" without visiting each agent. */
export function AccountRecords() {
  const [open, setOpen] = useState(false);
  const [records, setRecords] = useState<AccountRecordView[] | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setRecords((await Api.accountRecords()).records); setError(""); } catch (e) { setError(message(e)); }
  }, []);
  useEffect(() => { if (open) void load(); }, [open, load]);
  return (
    <div className="mt-3 border-t border-line pt-3">
      <button className="touch-target flex w-full items-center gap-1 text-left text-[11.5px] font-medium text-muted hover:text-fg" aria-expanded={open}
        onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />} Which accounts did the work, every task
      </button>
      {open && (
        <div className="mt-1" role="group" aria-label="Every task's account record">
          <div className="text-[11.5px] text-faint">Each task's record of the accounts that did its work, every agent's, newest task first; a deleted task's is also listed under its agent.</div>
          {records === null && !error && <div className="mt-1 text-[12px] text-muted">Checking…</div>}
          {records !== null && records.length === 0 && <div className="mt-1 text-[12px] text-muted">No task has a record yet.</div>}
          {(records ?? []).map((r) => <AccountRecord key={r.task_id} record={r} onChanged={load} />)}
          {error && <div role="alert" className="mt-1 text-[12px] text-fail">{error}</div>}
        </div>
      )}
    </div>
  );
}

interface Update {
  enabled?: boolean; updating?: boolean; mode?: string; version?: string | null;
  last?: { ok?: boolean; changed?: boolean; version?: string | null; at?: number; why?: string } | null;
}

/** What Claude Code's keeping current says (the workspace's claude_update.py), in words. */
export function updateWords(u: Update): string {
  if (u.updating) return "Checking for a newer Claude Code…";
  const mode = u.mode === "automatic" ? "Updates: automatic, at each start and once a day." : "Updates: only when you say.";
  const last = u.last;
  if (!last) return mode;
  const when = last.at ? ` (${dayTime(last.at)})` : "";
  if (last.ok && last.changed) return `${mode} Updated to ${last.version}${when}.`;
  if (last.ok) return `${mode} ${last.version || "It"} is the newest${when}.`;
  return `${mode} The update${last.version ? ` to ${last.version}` : ""} did not pass its check${when}: ${last.why}; ${u.version ? `Claude Code ${u.version}` : "the image's Claude Code"} stays in use.`;
}

/** Claude Code kept current in the workspace: automatic or only when asked, and now. A new build's choices reach a
 *  chat's settings when it is done. */
function ClaudeUpdates() {
  const refreshCaps = useStore((s) => s.refreshCaps);
  const [u, setU] = useState<Update | null>(null);
  const [error, setError] = useState("");
  const was = useRef(false);
  const load = async () => {
    try {
      const next = await Api.claudeUpdate();
      if (was.current && !next.updating) void refreshCaps();       // a new build: its choices and its version
      was.current = !!next.updating;
      setU(next);
    } catch { setU(null); }
  };
  useEffect(() => {
    void load();
    const t = setInterval(() => { if (was.current && document.visibilityState === "visible") void load(); }, 3000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (!u || !u.enabled) return null;
  const act = async (body: { now?: boolean; mode?: string }) => {
    setError("");
    try { const next = await Api.claudeUpdate(body); was.current = !!next.updating; setU(next); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <div className="mt-3 border-t border-line pt-3 text-[12px]">
      <div className="text-muted">{updateWords(u)}</div>
      <div className="mt-2 flex flex-wrap gap-2">
        <button className="btn btn-sm" disabled={!!u.updating} onClick={() => act({ now: true })}>
          {u.updating ? <Loader2 size={12} className="animate-spin" /> : null} Check for an update now
        </button>
        <button className="btn btn-sm" disabled={!!u.updating} onClick={() => act({ mode: u.mode === "automatic" ? "manual" : "automatic" })}>
          {u.mode === "automatic" ? "Only when I say" : "Update automatically"}
        </button>
      </div>
      {error && <div className="mt-2 text-fail">{error}</div>}
    </div>
  );
}

/** Linear, for a session's task (the agent's linear.py): the person's own API key, which the workspace keeps and never
 *  sends back; the page learns only whose it is. Read only: nothing is written to Linear. */
function LinearCard() {
  const running = useStore((s) => !!s.ws?.running);
  const [st, setSt] = useState<LinearStatus | null>(null);
  const [editing, setEditing] = useState(false);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!running) return;
    Api.linear().then(setSt, () => setSt(null));
  }, [running]);
  const act = async (fn: () => Promise<LinearStatus>) => {
    setBusy(true); setError("");
    try { setSt(await fn()); setEditing(false); setKey(""); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const who = st?.user ? [st.user.name, st.user.email && `(${st.user.email})`].filter(Boolean).join(" ") : "";
  return (
    <Card icon={<span className="flex h-7 w-7 items-center justify-center rounded-md bg-raised"><ListTodo size={15} /></span>}
      title="Linear" status={!running ? "Start the workspace to change this" : !st ? "Checking…" : !st.set ? "Not connected" : st.error ? "Its key is not taken" : `Connected${who ? ` as ${who}` : ""}`}>
      <p className="text-[12px] text-muted">
        A session can start from a Linear issue, yours or one you search for, as it can from a GitHub issue. With your
        own personal API key: make one in Linear&apos;s settings (Account, Security &amp; access) with the Read permission
        only, and only the teams you want, since nothing here writes to Linear and a key that can only read is all a
        leaked one could do. It is kept in your workspace, never sent back to this page, and the gate keeps it from the
        agents&apos; tools; a program the agent runs, as your workspace&apos;s user, could still read it.
      </p>
      {st?.error && <div className="mt-2 text-[12px] text-fail">{st.error}</div>}
      {editing ? (
        <form className="mt-2 flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (key.trim()) void act(() => Api.setLinearKey(key.trim())); }}>
          <input className="input h-8 min-w-0 flex-1 font-mono text-[12px]" type="password" autoComplete="off" spellCheck={false}
            aria-label="Your Linear API key" placeholder="lin_api_..." value={key} onChange={(e) => setKey(e.target.value)} autoFocus />
          <button className="btn btn-sm btn-primary" disabled={!key.trim() || busy}>{busy ? <Loader2 size={12} className="animate-spin" /> : null} Keep</button>
          <button type="button" className="btn btn-sm" onClick={() => setEditing(false)}>Cancel</button>
        </form>
      ) : (
        <div className="mt-2 flex gap-2">
          <button className="btn btn-sm" disabled={!running || busy} onClick={() => { setEditing(true); setKey(""); }}>{st?.set ? "Use another key" : "Add your key"}</button>
          {st?.set && (
            <button className="btn btn-sm" disabled={busy} onClick={() => void act(() => Api.forgetLinearKey())}>
              {busy ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />} Forget the key
            </button>
          )}
        </div>
      )}
      {error && <div className="mt-2 text-[12px] text-fail">{error}</div>}
    </Card>
  );
}

/** Where the workspace read a managed setting (the agent's core_plugin.managed): the cache of an organisation's
 *  settings is a file in the account's folder, which a session can write too, so the page says so. */
const SOURCES: Record<string, string> = {
  file: "this machine's managed settings",
  preferences: "this Mac's managed preferences",
  organisation: "the organisation's settings as Claude Code keeps them in the account's folder (a file a session can also write)",
};
export function sourceWords(kinds: string[]): string {
  return kinds.map((k) => SOURCES[k] || "managed settings").join(" and ");
}

/** What the page says of Proofpane's MCP server in the workspace's sessions (the agent's core_plugin.py). */
export function mcpWords(st: ProofpaneStatus): string {
  if (st.mcp.given) return "Proofpane's MCP server is given too, with the plugin, run by the Proofpane daemon found on this machine: each call of its tools is first put to your organisation's Proofpane policy for the agent named default, which refuses every tool until your organisation has one, and is audited in your Proofpane; by default its bash, write and edit are then refused until you approve the call there and it is made again, and its ingest_to_rag, which sends a file to your organisation's documents, is not held.";
  if (st.mcp.why === "machine") return "Proofpane's MCP server is not given: a workspace machine has no Proofpane daemon.";
  if (st.mcp.why === "no daemon") return "Proofpane's MCP server is not given: the Proofpane daemon is not found where its installer puts it (/opt/proofpane, /opt/homebrew/bin, /usr/local/bin or ~/.local/bin), and one run from elsewhere, from its Mac app's own folder say, is not looked for.";
  return "Proofpane's MCP server is given once the workspace is paired, run by the Proofpane daemon where it is found here.";
}

/** Proofpane's core (the agent's core_plugin.py): the workspace paired with the owner's Proofpane organisation, as
 *  Proofpane's daemon pairs a computer, after which each Claude Code terminal and chat started there is given the
 *  proofpane-core plugin. The device's token is kept in the workspace and never sent to this page, which shows the
 *  device's name and Proofpane's address alone. */
function ProofpaneCard() {
  const running = useStore((s) => !!s.ws?.running);
  const accounts = useStore((s) => s.accounts);
  const [st, setSt] = useState<ProofpaneStatus | null>(null);
  const [pairing, setPairing] = useState(false);
  const [editingHolds, setEditingHolds] = useState(false);
  const [code, setCode] = useState("");
  const [address, setAddress] = useState("");
  const [holds, setHolds] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!running) return;
    Api.proofpane().then(setSt, () => setSt(null));
  }, [running]);
  const act = async (fn: () => Promise<ProofpaneStatus>) => {
    setBusy(true); setError("");
    try { setSt(await fn()); setPairing(false); setEditingHolds(false); setCode(""); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const lines = (text: string) => text.split("\n").map((l) => l.trim()).filter(Boolean);
  const nameOf = (id: string) => accounts?.claude.accounts.find((a) => a.id === id)?.name || (id === "default" ? "Default" : "another account");
  const status = !running ? "Start the workspace to change this" : !st ? "Checking…" : !st.paired ? "Not paired" : `Paired${st.device_name ? ` as ${st.device_name}` : ""}`;
  return (
    <Card icon={<span className="flex h-7 w-7 items-center justify-center rounded-md bg-raised"><ShieldCheck size={15} /></span>}
      title="Proofpane" status={status}>
      <p className="text-[12px] text-muted">
        Pair your workspace with your Proofpane organisation, as Proofpane&apos;s daemon pairs a computer: make a pairing
        code in Proofpane (Devices) and give it here. Then each Claude Code terminal and chat you start here is given
        Proofpane&apos;s core, the proofpane-core plugin: its skills{st && st.skills.length ? ` (${st.skills.map((n) => `/proofpane-core:${n}`).join(", ")})` : ""},
        and its hooks. They send your Proofpane audit each prompt and reply, and each tool call with what it returned (a
        command&apos;s output, a page fetched, a failed call&apos;s error), but for the few that only read, search or
        list, keep the session&apos;s lists, change its mode or ask you something, each text cut to 4000 characters
        after Proofpane&apos;s six secret patterns, which catch some kinds of key and not others.
        They refuse the commands the Proofpane daemon&apos;s nine fixed patterns name as dangerous and what the
        workspace&apos;s gate refuses, and hold the ones you name below for your approval in Proofpane, each request
        naming the call and the session that asks, whose approval is that call's alone. A command over 64 KB is not
        checked, and Claude Code asks you about it instead; a call whose checks have not ended in 75 seconds is
        refused. Every account&apos;s sessions report to this one pairing. An
        agent&apos;s run and a task&apos;s attempt are not given it. The device&apos;s token is kept in your workspace
        and never sent back to this page. The gate refuses a tool call that names where it is kept, but it is no
        sandbox: a call that reaches the file without naming it, or a program the agent runs, can read it, and use it
        as this device until you revoke the device in Proofpane (Devices). Once it is revoked, paused or given a new
        token there, holds go to Claude Code&apos;s own prompt and nothing is recorded, and this card still says
        paired. A paused device is resumed in Proofpane; a revoked one cannot be restored there, nor an old token
        brought back, so after either, unpair here and pair again with a new code. Unpairing here forgets the token
        for every later start, but stops no session
        already running: its hooks stop sending, refusing and holding, and
        Proofpane&apos;s MCP server, where it was given, runs its tools unheld and unrecorded until that session starts
        again. Unpairing does not revoke the device, and pairing again makes a new one beside it. The hooks are
        cooperative: they record and hold what a session does, they do not stop one that sets out to get round them,
        and a repository&apos;s settings, or your own Claude Code settings, can turn them off.
      </p>
      {st && !st.plugin && <div className="mt-2 text-[12px] text-fail">The plugin is not where this workspace runs from, so nothing of it is given.</div>}
      {st?.paired && (
        <div className="mt-2 space-y-1 text-[12px]">
          <div>Paired with {st.cloud_url}{st.paired_at ? `, ${dayTime(st.paired_at)}` : ""}.</div>
          <div className="text-muted">{mcpWords(st)}</div>
          <div className="text-muted">
            {st.holds && st.holds.length ? `Held for your approval: commands matching ${st.holds.join(", ")}.` : "No command is held for your approval."}
          </div>
        </div>
      )}
      {st && st.accounts.map((a) => (
        <div key={a.account} className="mt-2 text-[12px] text-fail">
          {a.refused.length
            ? `On ${nameOf(a.account)}, ${sourceWords(a.refused)} refuse plugins named for one session (disableSideloadFlags): its sessions start without Proofpane's core.`
            : `On ${nameOf(a.account)}, ${sourceWords(a.hooks_off)} keep plugins' hooks from running (allowManagedHooksOnly or disableAllHooks): the core's skills are given, its hooks do not run, and nothing of those sessions' hooks reaches your Proofpane.`}
        </div>
      ))}
      {pairing ? (
        <form className="mt-2 space-y-2" onSubmit={(e) => { e.preventDefault(); if (code.trim()) void act(() => Api.pairProofpane(code.trim(), address.trim() || null, lines(holds))); }}>
          <input className="input h-8 w-full font-mono text-[12px]" autoComplete="off" spellCheck={false}
            aria-label="The pairing code Proofpane shows" placeholder="ABC-DEF-GHJ" value={code} onChange={(e) => setCode(e.target.value)} autoFocus />
          <input className="input h-8 w-full text-[12px]" autoComplete="off" spellCheck={false}
            aria-label="Proofpane's address" placeholder="https://api.proofpane.com" value={address} onChange={(e) => setAddress(e.target.value)} />
          <textarea className="input w-full font-mono text-[12px]" rows={3} spellCheck={false}
            aria-label="Commands to hold for your approval, one pattern a line" placeholder="Commands to hold for your approval, one pattern a line (none holds nothing)" value={holds} onChange={(e) => setHolds(e.target.value)} />
          <div className="flex gap-2">
            <button className="btn btn-sm btn-primary" disabled={!code.trim() || busy}>{busy ? <Loader2 size={12} className="animate-spin" /> : null} Pair</button>
            <button type="button" className="btn btn-sm" onClick={() => setPairing(false)}>Cancel</button>
          </div>
        </form>
      ) : editingHolds ? (
        <form className="mt-2 space-y-2" onSubmit={(e) => { e.preventDefault(); void act(() => Api.setProofpaneHolds(lines(holds))); }}>
          <textarea className="input w-full font-mono text-[12px]" rows={3} spellCheck={false} autoFocus
            aria-label="Commands to hold for your approval, one pattern a line" value={holds} onChange={(e) => setHolds(e.target.value)} />
          <div className="flex gap-2">
            <button className="btn btn-sm btn-primary" disabled={busy}>{busy ? <Loader2 size={12} className="animate-spin" /> : null} Keep</button>
            <button type="button" className="btn btn-sm" onClick={() => setEditingHolds(false)}>Cancel</button>
          </div>
        </form>
      ) : (
        <div className="mt-2 flex gap-2">
          <button className="btn btn-sm" disabled={!running || busy || !st} onClick={() => { setPairing(true); setCode(""); setAddress(st?.paired && st.cloud_url !== "https://api.proofpane.com" ? st.cloud_url || "" : ""); setHolds((st?.holds || []).join("\n")); }}>
            {st?.paired ? "Pair again" : "Pair with Proofpane"}
          </button>
          {st?.paired && (
            <>
              <button className="btn btn-sm" disabled={busy} onClick={() => { setEditingHolds(true); setHolds((st.holds || []).join("\n")); }}>Change holds</button>
              <button className="btn btn-sm" disabled={busy} onClick={() => void act(() => Api.unpairProofpane())}>
                {busy ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />} Unpair
              </button>
            </>
          )}
        </div>
      )}
      {error && <div className="mt-2 text-[12px] text-fail">{error}</div>}
    </Card>
  );
}

/** Agents that ask for sessions (the agent's asks.py): off until the person turns it on; each agent started after can
 *  read its own repository's sessions and ask for a new one, which starts only when the person starts it. */
function AsksCard() {
  const on = useStore((s) => s.asksOn);
  const running = useStore((s) => !!s.ws?.running);
  const setError = useStore((s) => s.setError);
  const [busy, setBusy] = useState(false);
  const turn = async (to: boolean) => {
    setBusy(true);
    try {
      const v = await Api.setAsks(to);
      useStore.setState({ asksOn: v.on, sessionAsks: v.asks });
    } catch (e) { setError(e); } finally { setBusy(false); }
  };
  return (
    <Card icon={<span className="flex h-7 w-7 items-center justify-center rounded-md bg-raised"><Bot size={15} /></span>}
      title="Programs that ask for sessions" status={!running ? "Start the workspace to change this" : on === null ? "Checking…" : on ? "On" : "Off"}>
      <p className="text-[12px] text-muted">
        When this is on, each agent started from then can run <code className="font-mono">proofpane</code> in its
        terminal: list the sessions on its own repository, read their changes, wait for one, and ask you for a new
        session. What it asks for waits in Approvals, its words shown as they are, and starts only when you start it.
      </p>
      <button className="btn btn-sm mt-2" disabled={busy || !running || on === null} onClick={() => void turn(!on)}>
        {busy ? <Loader2 size={12} className="animate-spin" /> : null} {on ? "Turn off" : "Turn on"}
      </button>
    </Card>
  );
}

/** The person's own agents: any other command-line agent where the workspace runs, with the command it starts with
 *  and how its task reaches it. Kept by the app on the account, never in the workspace. */
export function CustomAgentsCard() {
  const agents = useStore((s) => s.customAgents);
  const refresh = useStore((s) => s.refreshCustomAgents);
  const setError = useStore((s) => s.setError);
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [task, setTask] = useState<"argument" | "typed">("argument");
  const [busy, setBusy] = useState("");
  useEffect(() => { void refresh(); }, [refresh]);
  const act = async (what: string, fn: () => Promise<unknown>) => {
    setBusy(what);
    try { await fn(); await refresh(); return true; } catch (e) { setError(e); return false; } finally { setBusy(""); }
  };
  const add = async () => {
    if (await act("add", () => Api.addCustomAgent({ name: name.trim(), command: command.trim(), task }))) { setName(""); setCommand(""); }
  };
  return (
    <Card icon={<span className="flex h-7 w-7 items-center justify-center rounded-md bg-raised"><SquareTerminal size={15} /></span>}
      title="Your own programs" status={agents === null ? "Checking…" : agents.length ? `${agents.length} of yours` : "None yet"}>
      <p className="text-[12px] text-muted">
        Any other command-line agent installed where the workspace runs: a session starts it in a terminal in its own
        worktree, with its command as you write it here (no shell runs it). The workspace's gate does not see inside it,
        as with a shell; what it changed is checked when you finish.
      </p>
      {agents && agents.length > 0 && (
        <div className="mt-2 space-y-1.5">
          {agents.map((a) => (
            <div key={a.id} className="flex items-center gap-2 text-[12px]">
              <AgentBadge kind="custom" name={a.name} size={18} />
              <span className="font-medium">{a.name}</span>
              <code className="min-w-0 flex-1 truncate font-mono text-muted" title={a.command}>{a.command}</code>
              {a.available === false
                ? <span className="whitespace-nowrap text-wait" title="Its program is not found in this workspace: install it there, or give its full path">not found here</span>
                : <span className="whitespace-nowrap text-faint">{a.task === "typed" ? "task typed" : "task as argument"}</span>}
              <button className="icon-btn !h-6 !w-6" title={`Forget ${a.name}`} disabled={!!busy} onClick={() => void act(a.id, () => Api.forgetCustomAgent(a.id))}>
                {busy === a.id ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-[1fr_2fr]">
        <input className="input" aria-label="Its name" placeholder="Its name, such as Aider" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} />
        <input className="input font-mono text-[12px]" aria-label="Its command" placeholder="aider --model sonnet" value={command} maxLength={500} onChange={(e) => setCommand(e.target.value)} />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <select className="input !w-auto" aria-label="How its task reaches it" value={task} onChange={(e) => setTask(e.target.value as "argument" | "typed")}>
          <option value="argument">The task goes in as its last argument</option>
          <option value="typed">It starts at its prompt, and the task is typed there</option>
        </select>
        <button className="btn btn-sm" disabled={!name.trim() || !command.trim() || !!busy} onClick={() => void add()}>
          {busy === "add" ? <Loader2 size={12} className="animate-spin" /> : null} Add
        </button>
      </div>
    </Card>
  );
}

/** Other providers' models in Claude Code: the person's own key for each, given once to the workspace, which keeps it
 *  and never sends it back; the page only learns whether one is kept. Adding Anthropic's own key is risk point 10: its
 *  text opens over the key's form each time the form opens, and the form works with it open. */
function ModelsCard() {
  const providers = useStore((s) => s.modelProviders);
  const refreshModelProviders = useStore((s) => s.refreshModelProviders);
  const setError = useStore((s) => s.setError);
  const [editing, setEditing] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState("");
  useEffect(() => { void refreshModelProviders(); }, [refreshModelProviders]);

  const act = async (what: string, fn: () => Promise<{ providers: ModelProviderView[] }>) => {
    setBusy(what);
    try {
      useStore.setState({ modelProviders: (await fn()).providers });
      setEditing(null); setKey("");
    } catch (e) { setError(e); } finally { setBusy(""); }
  };

  return (
    <Card icon={<span className="flex h-7 w-7 items-center justify-center rounded-md bg-raised"><KeyRound size={15} /></span>}
      title="Models in Claude Code" status={providers ? `${providers.filter((p) => p.set).length} of ${providers.length} with your key` : "Checking…"}>
      <ul className="space-y-2">
        {(providers || []).map((p) => (
          <li key={p.id} className="rounded-md border border-line px-3 py-2">
            <div className="flex items-center gap-2">
              {/* Anthropic's own key runs Claude's models, and has no mark of its own */}
              <AgentBadge kind={(p.id === "anthropic" ? "claude" : p.id) as never} size={18} />
              <span className="text-[12.5px] font-medium">{p.label}</span>
              <span className="text-[11.5px] text-faint">{p.vendor} · <span className="font-mono">{p.model}</span></span>
              <span className="ml-auto" />
              {p.set ? (
                <>
                  <span className="text-[11.5px] text-done">Key kept</span>
                  <button className="icon-btn" title="Forget this key" disabled={!!busy} onClick={() => act(`rm-${p.id}`, () => Api.removeModelKey(p.id))}>
                    {busy === `rm-${p.id}` ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                  </button>
                </>
              ) : editing !== p.id && (
                <button className="btn btn-sm" onClick={() => { setEditing(p.id); setKey(""); }}>Add your key</button>
              )}
            </div>
            {editing === p.id && (
              <div className={`mt-2 ${TERMS_IN_FLOW}`}>
                <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (key.trim()) void act(`save-${p.id}`, () => Api.saveModelKey(p.id, key.trim())); }}>
                  <input className="input h-8 min-w-0 flex-1 font-mono text-[12px]" type="password" autoComplete="off" spellCheck={false}
                    aria-label={`Your ${p.label} API key`} placeholder={`Your ${p.vendor} API key`} value={key} onChange={(e) => setKey(e.target.value)} autoFocus />
                  <button className="btn btn-sm btn-primary" disabled={!key.trim() || !!busy}>{busy === `save-${p.id}` ? <Loader2 size={12} className="animate-spin" /> : null} Keep</button>
                  <button type="button" className="btn btn-sm" onClick={() => setEditing(null)}>Cancel</button>
                </form>
                <a className="mt-1.5 inline-flex items-center gap-1 text-[11.5px] text-accent hover:underline" href={p.docs} target="_blank" rel="noreferrer noopener">
                  <ExternalLink size={11} /> {p.vendor}&apos;s own instructions for Claude Code
                </a>
                {/* made with the form, so the text opens each time the form does, and goes with it; under the
                    instructions' link, which it leaves in view */}
                {p.id === "anthropic" && <RiskTerms point={10} kind="control" open />}
              </div>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function GithubCard() {
  const github = useStore((s) => s.github);
  const signIns = useStore((s) => s.signIns);
  const refresh = useStore((s) => s.refresh);
  const refreshGithub = useStore((s) => s.refreshGithub);
  const setError = useStore((s) => s.setError);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const running = signIns.filter((r) => r.program === "github" && r.running && r.sign_in === true).pop();
  const code = running?.device_code || "";
  const was = useRef(false);

  useEffect(() => {
    // when the sign-in program ends, read GitHub's state again: it says whom it signed in
    if (running) was.current = true;
    else if (was.current) { was.current = false; void refreshGithub(); }
  }, [running, refreshGithub]);

  const start = async () => {
    setBusy(true);
    try {
      await Api.createSession({ repo: null, program: "github", task: "Sign in to GitHub" });
      await refresh();
    } catch (e) { setError(e); } finally { setBusy(false); }
  };

  const status = !github ? "Checking…"
    : !github.installed ? "gh is not installed in this workspace"
    : github.user ? `Signed in as ${github.user} · ${github.repos.length}${github.capped ? "+" : ""} repositories`
    : "Not signed in: public repositories clone without it";

  return (
    <Card icon={<span className="flex h-7 w-7 items-center justify-center rounded-md bg-raised"><FolderGit2 size={16} /></span>} title="GitHub" status={status}>
      {github?.installed && !github.user && !running && (
        <button className="btn" disabled={busy} onClick={start}>{busy ? <Loader2 size={13} className="animate-spin" /> : null} Sign in to GitHub</button>
      )}
      {running && (
        <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
          {code ? (
            <>
              <span className="text-muted">Your code</span>
              <span className="rounded-md border border-line-strong bg-bg px-2.5 py-1 font-mono text-[14px] tracking-widest">{code}</span>
              <button className="icon-btn" title="Copy" onClick={() => { void navigator.clipboard?.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
                {copied ? <Check size={14} className="text-done" /> : <Copy size={14} />}
              </button>
              <a className="btn btn-sm btn-primary" href={GITHUB_DEVICE_PAGE} target="_blank" rel="noreferrer noopener"><ExternalLink size={12} /> Enter it at GitHub</a>
            </>
          ) : (
            <span className="flex items-center gap-2 text-muted"><Loader2 size={12} className="animate-spin" /> Waiting for GitHub's code…</span>
          )}
        </div>
      )}
    </Card>
  );
}
