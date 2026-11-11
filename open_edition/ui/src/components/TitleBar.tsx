import { useRef, useState, type ReactNode } from "react";
import { AppWindow, Bell, Bot, Check, ChevronDown, Clock, Cloud, FileCheck2, KeyRound, Laptop, LockKeyhole, LogOut, Menu, Plus, Radio, Scale, Search, ShieldCheck, Smartphone, SquareKanban, UserPlus } from "lucide-react";
import { useTheme, type ThemePref } from "../theme";
import { workspaceWord, type WorkspaceStatus } from "../types";
import { DESKTOP, type AccountKind, type AccountsView, type Quotas, type Window } from "../api";
import { activeId, addHosted, dropUnsigned, LOCAL, places, setActive, type Place } from "../places";
import { Kbd, usd, useMenu } from "./bits";
import { clock, weekdayTime } from "../when";
import type { AccountTab } from "./AccountDialog";

/** A pane of the account's window, as the title bar's account menu opens it. */
export type AccountPane = AccountTab;

export const DEFAULT_SERVER = "https://proofpane-oss.fly.dev";

/** The desktop app's window onto another place: that place's state comes from its own server, so the window loads
 *  again. Hosted places nobody signed in to are dropped on the way out. */
export function switchPlace(id: string) {
  dropUnsigned(id);
  setActive(id);
  location.reload();
}

/** A hosted workspace, added and shown, its sign-in screen first. */
export function addHostedPlace(origin: string = DEFAULT_SERVER) {
  switchPlace(addHosted(origin).id);
}

/** Proofpane's logo, the P in the Pane (v8, as web/favicon.svg draws it): the outer pane, its upper half split, and
 *  the P one closed shape with its counter cut out. Drawn in the theme's accent, which on the dark theme is the logo's
 *  own cyan and on the light one a darker cyan that reads on white. */
export function Logo({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="2.5" y="2.5" width="19" height="19" rx="1.8" stroke="var(--color-accent)" strokeWidth="1.8" />
      <line x1="2.5" y1="11" x2="21.5" y2="11" stroke="var(--color-accent)" strokeWidth="1.5" />
      <line x1="12" y1="2.5" x2="12" y2="11" stroke="var(--color-accent)" strokeWidth="1.5" />
      <path fillRule="evenodd" fill="var(--color-accent)"
        d="M14 12.75L18.5 12.75A1 1 0 0 1 19.5 13.75L19.5 16.85A1 1 0 0 1 18.5 17.85L15.5 17.85L15.5 19.75L14 19.75Z
           M15.5 14.25L17.65 14.25A0.35 0.35 0 0 1 18 14.6L18 16A0.35 0.35 0 0 1 17.65 16.35L15.5 16.35Z" />
    </svg>
  );
}

export function TitleBar(props: {
  /** the workspace's state; null while it is read, false where the deployment gives the account none */
  workspace: WorkspaceStatus | null | false;
  pendingApprovals: number;
  spentTodayUsd?: number;
  account?: string;
  onSearch: () => void;
  onApprovals: () => void;
  /** the session channel: what needs the owner there (silent sessions, open questions, requests to link), and opening
   *  it: shown always, so a Mac's sessions are a click away before anything is linked too */
  channelAlerts?: number;
  onChannel?: () => void;
  /** the agents (agents.ts): opening them, and how many of their runs wait for the owner */
  onAgents?: () => void;
  /** the live progress board (board.ts): opening it, and its times' zone and format, kept on this device */
  onBoard?: () => void;
  onTimes?: () => void;
  agentsWaiting?: number;
  /** a phone's list of sessions, a drawer: opening it, and whether it is open */
  onMenu?: () => void;
  menuOpen?: boolean;
  quotas?: Quotas;
  /** Claude Code's and Codex's accounts in the workspace, to switch the one new sessions start on from the windows */
  accounts?: AccountsView | null;
  onChooseAccount?: (kind: AccountKind, account: string) => void;
  onAccounts?: () => void;
  admin?: boolean;
  onAccount?: (tab: AccountPane) => void;
  /** the licenses of what the app carries (Licenses.tsx) */
  onLicenses?: () => void;
  onSignOut?: () => void;
}) {
  const { workspace, pendingApprovals } = props;
  // On a touch screen each of the bar's controls is a 44-pixel target (touch-target), and the bar grows to hold them
  // below whatever the phone's own status bar covers; its name and the edition give way to them on a narrow one.
  return (
    <header className="box-content flex h-11 flex-none items-center gap-2 border-b border-line bg-panel px-2 sm:gap-3 sm:px-3" style={{ paddingTop: "env(safe-area-inset-top, 0px)" }}>
      {props.onMenu && (
        <button className="icon-btn touch-target -ml-1 flex-none" onClick={props.onMenu} title="Sessions" aria-label="Sessions"
          aria-haspopup="dialog" aria-expanded={!!props.menuOpen}>
          <Menu size={17} />
        </button>
      )}
      <div className="flex flex-none items-center gap-2 sm:pr-2">
        <Logo />
        <span className="hidden text-[13.5px] font-semibold tracking-tight sm:inline">Proofpane</span>
        <span className="hidden rounded border border-line-strong px-1.5 text-[10.5px] font-medium text-muted sm:inline">Community</span>
      </div>

      {DESKTOP ? <PlaceMenu workspace={workspace || null} />
        : workspace !== false && <span className="hidden sm:inline-flex"><WorkspacePill workspace={workspace} /></span>}

      <button
        onClick={props.onSearch}
        className="touch-target mx-auto hidden h-7 w-full max-w-[460px] items-center gap-2 rounded-md border border-line bg-bg px-2.5 text-left text-[12.5px] text-muted hover:border-line-strong md:flex"
      >
        <Search size={14} />
        <span className="flex-1 truncate">Search sessions, files, commands</span>
        <Kbd>⌘K</Kbd>
      </button>

      <div className="ml-auto flex items-center sm:gap-1 md:ml-0">
        <button className="icon-btn touch-target flex-none md:hidden" onClick={props.onSearch} title="Search" aria-label="Search"><Search size={16} /></button>
        <Quota quotas={props.quotas ?? {}} spentTodayUsd={props.spentTodayUsd} accounts={props.accounts} onChoose={props.onChooseAccount} onManage={props.onAccounts} />
        {props.onChannel && (
          <button className="icon-btn touch-target relative flex-none" onClick={props.onChannel} title="Linked sessions"
            aria-label={counted("Linked sessions", props.channelAlerts, "needs you", "need you")}>
            <Radio size={16} />
            <Badge n={props.channelAlerts} />
          </button>
        )}
        {props.onBoard && (
          <button className="icon-btn touch-target relative flex-none" onClick={props.onBoard} title="Progress board" aria-label="Progress board">
            <SquareKanban size={16} />
          </button>
        )}
        {props.onAgents && (
          <button className="icon-btn touch-target relative flex-none" onClick={props.onAgents} title="Agents"
            aria-label={counted("Agents", props.agentsWaiting, "waiting for you", "waiting for you")}>
            <Bot size={16} />
            <Badge n={props.agentsWaiting} />
          </button>
        )}
        <button className="icon-btn touch-target relative flex-none" onClick={props.onApprovals} title="Approvals"
          aria-label={counted("Approvals", pendingApprovals, "waiting", "waiting")}>
          {pendingApprovals > 0 ? <Bell size={16} /> : <ShieldCheck size={16} />}
          <Badge n={pendingApprovals} />
        </button>
        {props.account && <AccountMenu email={props.account} admin={!!props.admin} onAccount={props.onAccount} onChannel={props.onChannel} onTimes={props.onTimes} onLicenses={props.onLicenses} onSignOut={props.onSignOut} />}
      </div>
    </header>
  );
}

/** A title-bar button's name with what waits there counted in it ("Approvals, 3 waiting"), so a screen reader reads
 *  the name and the count together; the badge itself is hidden from it. */
export function counted(name: string, n: number | undefined, one: string, many: string): string {
  return n ? `${name}, ${n} ${n === 1 ? one : many}` : name;
}

/** The count on a title-bar button, for the eye: the button's name says it to a screen reader. */
function Badge({ n }: { n?: number }) {
  if (!n) return null;
  return (
    <span aria-hidden className="absolute right-1 top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-wait px-1 text-[10px] font-bold text-bg [@media(pointer:fine)]:-right-0.5 [@media(pointer:fine)]:-top-0.5">
      {n}
    </span>
  );
}

const ACCOUNTS: [AccountKind, string, string][] = [["claude", "Claude", "Claude Code's status line"], ["codex", "Codex", "Codex's own files"]];

const SPEND = "Spend today, as the agents report it";

/** What the agents use, in one place: each program's usage windows (Claude's, Codex's) on the account new sessions
 *  start on, as its programs last recorded them, the second program's only on a wide screen; and the spend today. With
 *  more than one account of a program, it is the switcher too: each account with its windows, the one new sessions start
 *  on chosen here (accounts.py in the workspace). */
export function Quota({ quotas, spentTodayUsd, accounts, onChoose, onManage }: { quotas: Quotas; spentTodayUsd?: number; accounts?: AccountsView | null; onChoose?: (kind: AccountKind, id: string) => void; onManage?: () => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useMenu(open, () => setOpen(false), box);
  const when = (w: Window) => (w.resets_at ? weekdayTime(w.resets_at) : "unknown");
  const groups = ACCOUNTS.flatMap(([key, name, source]) => {
    const q = quotas[key];
    const parts: [string, Window][] = [];
    if (q?.five_hour) parts.push(["5h", q.five_hour]);
    if (q?.seven_day) parts.push(["7d", q.seven_day]);
    const several = (accounts?.[key].accounts.length ?? 0) > 1;
    const active = accounts?.[key].accounts.find((a) => a.id === accounts[key].active);
    const label = q?.name ? `${name} · ${q.name}` : several && active && active.id !== "default" ? `${name} · ${active.name}` : name;
    if (!parts.length && !several) return [];
    const title = !parts.length ? `${label}: no windows recorded yet`
      : parts.map(([n, w]) => `${label}, ${n} window: ${w.used_percentage.toFixed(0)}% used, resets ${when(w)}`).join("\n") +
        (q ? `\nAs ${source} last recorded it, ${clock(q.at)}` : "");
    return [{ key, label, parts, title }];
  });
  const spend = spentTodayUsd != null ? usd(spentTodayUsd) : null;
  if (!groups.length) return spend ? <span className="pill hidden font-mono sm:inline-flex" title={SPEND}>{spend}</span> : null;
  const switcher = !!accounts && ACCOUNTS.some(([k]) => accounts[k].accounts.length > 1);
  const pill = (
    <>
      {groups.map((g, i) => (
        <span key={g.key} className={`items-center gap-2 ${i > 0 ? "hidden border-l border-line pl-2.5 xl:flex" : spend ? "hidden lg:flex" : "flex"}`}>
          <span className="text-[10.5px] font-medium text-muted">{g.label}</span>
          {g.parts.map(([name, w]) => {
            const pct = Math.min(100, w.used_percentage);
            const color = pct >= 95 ? "var(--color-fail)" : pct >= 80 ? "var(--color-wait)" : "var(--color-done)";
            return (
              <span key={name} className="flex items-center gap-1.5 text-[11px] text-fg-dim">
                <span className="text-faint">{name}</span>
                <span className="relative h-1.5 w-8 overflow-hidden rounded-full bg-active">
                  <span className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${pct}%`, background: color }} />
                </span>
                <span className="font-mono">{pct.toFixed(0)}%</span>
              </span>
            );
          })}
        </span>
      ))}
      {spend && <span className="font-mono text-[11px] text-fg-dim lg:border-l lg:border-line lg:pl-2.5" title={SPEND}>{spend}</span>}
    </>
  );
  // the windows from a wide screen on; the spend, inside the same control, from a narrow one, as it always showed
  const pillClass = `hidden items-center gap-2.5 rounded-full border border-line-strong px-2.5 py-0.5 ${spend ? "sm:inline-flex" : "lg:inline-flex"}`;
  if (!switcher) return <span className={pillClass} title={groups.map((g) => g.title).join("\n\n")}>{pill}</span>;
  return (
    <div className={`relative hidden ${spend ? "sm:block" : "lg:block"}`} ref={box}>
      <button className={`${pillClass} touch-target flex-none hover:border-faint`} aria-haspopup="menu" aria-expanded={open}
        title={groups.map((g) => g.title).join("\n\n") + "\n\nSwitch the account new sessions start on"} onClick={() => setOpen((o) => !o)}>
        {pill}
        <ChevronDown size={11} className="text-muted" />
      </button>
      {open && (
        <div role="menu" aria-label="Accounts" className="absolute right-0 top-8 z-50 w-80 rounded-lg border border-line-strong bg-raised p-1 shadow-2xl shadow-black/60">
          {ACCOUNTS.map(([kind, name]) => accounts![kind].accounts.length > 1 && (
            <div key={kind} className="py-1">
              <div className="px-2.5 pb-1 text-[10.5px] font-medium uppercase tracking-wide text-faint">{name}: new sessions start on</div>
              {accounts![kind].accounts.map((a) => {
                const on = a.id === accounts![kind].active;
                const w = [a.usage?.five_hour && `5h ${Math.min(100, a.usage.five_hour.used_percentage).toFixed(0)}%`,
                  a.usage?.seven_day && `7d ${Math.min(100, a.usage.seven_day.used_percentage).toFixed(0)}%`].filter(Boolean).join(" · ");
                return (
                  <button key={a.id} role="menuitemradio" aria-checked={on} className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-left hover:bg-hover [@media(pointer:coarse)]:min-h-11"
                    onClick={() => { setOpen(false); if (!on) onChoose?.(kind, a.id); }}>
                    <span className="w-3.5 flex-none text-accent">{on ? <Check size={13} /> : null}</span>
                    <span className="min-w-0 flex-1 truncate text-[12.5px]">{a.name}</span>
                    <span className="flex-none font-mono text-[11px] text-muted">{w || "no windows yet"}</span>
                  </button>
                );
              })}
            </div>
          ))}
          {onManage && (
            <button role="menuitem" className="mt-0.5 flex w-full items-center gap-2 rounded-md border-t border-line px-2.5 py-1.5 text-left text-[12px] text-fg-dim hover:bg-hover [@media(pointer:coarse)]:min-h-11"
              onClick={() => { setOpen(false); onManage(); }}>Manage accounts…</button>
          )}
        </div>
      )}
    </div>
  );
}

/** The desktop app's places (places.ts): which one the window shows, whether its workspace runs, the others, and adding
 *  a hosted one. */
export function PlaceMenu({ workspace }: { workspace?: WorkspaceStatus | null }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useMenu(open, () => setOpen(false), box);
  const all = places();
  const here = activeId();
  const current = all.find((p) => p.id === here);
  const icon = (p: Place | undefined, size: number) => (p?.id === LOCAL || !p ? <Laptop size={size} /> : <Cloud size={size} />);
  return (
    <div className="relative" ref={box}>
      <button className="flex h-7 max-w-[240px] items-center gap-1.5 rounded-md border border-line px-2 text-[12px] text-fg-dim hover:border-line-strong hover:text-fg"
        aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}
        title={`The workspace this window shows: ${!workspace ? "connecting" : workspaceWord(workspace)}`}>
        <span className="dot" style={{ background: workspace?.running ? "var(--color-done)" : "var(--color-faint)" }} />
        {icon(current, 13)}
        <span className="truncate">{current?.label ?? "This Mac"}</span>
        <ChevronDown size={12} className="flex-none text-muted" />
      </button>
      {open && (
        <div role="menu" aria-label="Workspaces" className="absolute left-0 top-9 z-50 w-72 rounded-lg border border-line-strong bg-raised p-1 shadow-2xl shadow-black/60">
          {all.map((p) => (
            <button key={p.id} role="menuitemradio" aria-checked={p.id === here}
              className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-left hover:bg-hover"
              onClick={() => { setOpen(false); if (p.id !== here) switchPlace(p.id); }}>
              <span className="text-muted">{icon(p, 14)}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[12.5px] text-fg">{p.label}</span>
                <span className="block truncate text-[11px] text-muted">{p.id === LOCAL ? "Agents run on this Mac" : p.email ?? "Not signed in"}</span>
              </span>
              {p.id === here && <Check size={14} className="flex-none text-accent" />}
            </button>
          ))}
          <div className="my-1 h-px bg-line" />
          <button role="menuitem" className="flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-left text-[12.5px] text-fg-dim hover:bg-hover hover:text-fg"
            onClick={() => { setOpen(false); addHostedPlace(); }}>
            <Plus size={14} /> Add a hosted workspace
          </button>
        </div>
      )}
    </div>
  );
}

/** The workspace's state, beside the product's name: running, starting, stopped, or not started yet (never called
 *  stopped: one never started keeps nothing). */
export function WorkspacePill({ workspace }: { workspace: WorkspaceStatus | null }) {
  if (!workspace) return <span className="pill text-muted">Connecting…</span>;
  const word = workspaceWord(workspace);
  const on = word === "running";
  return (
    <span className="pill" title={`Your workspace is ${word}`}>
      <span className="dot" style={{ background: on ? "var(--color-done)" : word === "starting" ? "var(--color-run)" : "var(--color-faint)" }} />
      {on ? "Workspace" : `Workspace ${word}`}
      {workspace.region && <span className="text-faint">· {workspace.region}</span>}
    </span>
  );
}

/** The avatar, and what the account can do from it: its own panes (its passkeys, encryption, the apps connected to it,
 *  notifications), its linked sessions, the page's own settings on this device (its theme, the progress board's times),
 *  the licenses of what the app carries, and signing out. */
function AccountMenu({ email, admin, onAccount, onChannel, onTimes, onLicenses, onSignOut }: { email: string; admin: boolean; onAccount?: (tab: AccountPane) => void; onChannel?: () => void; onTimes?: () => void; onLicenses?: () => void; onSignOut?: () => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useMenu(open, () => setOpen(false), box);
  const item = (icon: ReactNode, label: string, run?: () => void) => (
    <button role="menuitem" className="flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-left text-[12.5px] text-fg-dim hover:bg-hover hover:text-fg [@media(pointer:coarse)]:h-11" onClick={() => { setOpen(false); run?.(); }}>
      {icon} {label}
    </button>
  );
  return (
    <div className="relative sm:ml-1" ref={box}>
      {/* the circle is the eye's; on a touch screen the button around it is a finger's 44 pixels */}
      <button
        className="group touch-target flex h-7 w-7 flex-none items-center justify-center rounded-full"
        title={email}
        aria-label={`Your account, ${email}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span aria-hidden className="flex h-7 w-7 items-center justify-center rounded-full bg-active text-[11.5px] font-semibold uppercase text-fg-dim group-hover:ring-1 group-hover:ring-line-strong">
          {Array.from(email)[0]}
        </span>
      </button>
      {open && (
        <div role="menu" className="absolute right-0 top-full z-50 mt-1 w-64 rounded-lg border border-line-strong bg-raised p-1 shadow-2xl shadow-black/60">
          <div className="truncate px-2.5 pb-2 pt-1.5 text-[11.5px] text-muted">{email}</div>
          {item(<KeyRound size={14} />, "Account and passkeys", () => onAccount?.("account"))}
          {item(<LockKeyhole size={14} />, "Encryption", () => onAccount?.("encryption"))}
          {item(<AppWindow size={14} />, "Connected apps", () => onAccount?.("apps"))}
          {onChannel && item(<Radio size={14} />, "Linked sessions", onChannel)}
          {item(<Bell size={14} />, "Notifications", () => onAccount?.("notifications"))}
          {admin && item(<UserPlus size={14} />, "Invite someone", () => onAccount?.("people"))}
          {item(<Smartphone size={14} />, "Sign in a device", () => onAccount?.("device"))}
          {item(<FileCheck2 size={14} />, "Check a signed record", () => onAccount?.("verify"))}
          <div className="my-1 h-px bg-line" />
          <ThemeChoice />
          {onTimes && item(<Clock size={14} />, "Times on the board", onTimes)}
          {onLicenses && item(<Scale size={14} />, "Licenses", onLicenses)}
          {onSignOut && <div className="my-1 h-px bg-line" />}
          {onSignOut && item(<LogOut size={14} />, "Sign out", onSignOut)}
        </div>
      )}
    </div>
  );
}

/** Dark, light, or the system's, for this device. */
function ThemeChoice() {
  const { pref, set } = useTheme();
  const options: [ThemePref, string][] = [["dark", "Dark"], ["light", "Light"], ["system", "System"]];
  return (
    <div className="flex items-center gap-2 px-2.5 py-1.5 text-[12.5px] text-fg-dim">
      <span className="flex-1">Theme</span>
      <span className="flex rounded-md border border-line p-0.5" role="group" aria-label="Theme">
        {options.map(([value, label]) => (
          <button key={value} className={`touch-target rounded px-1.5 py-0.5 text-[11.5px] ${pref === value ? "bg-active text-fg" : "text-muted hover:text-fg"}`} aria-pressed={pref === value} onClick={() => set(value)}>{label}</button>
        ))}
      </span>
    </div>
  );
}
