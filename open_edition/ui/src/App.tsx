import { Fragment, Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Group, Panel, Separator } from "react-resizable-panels";
import { AppWindow, ArrowRightLeft, Bell, Bot, CalendarDays, CheckCheck, Circle, CopyPlus, Download, Eye, EyeOff, FileCheck2, GitMerge, GitPullRequest, Keyboard, KeyRound, Laptop, Loader2, Network, PanelLeftClose, PanelLeftOpen, PanelRightOpen, Pencil, Play, Plug, Plus, Power, Radio, Scale, Search, Square, SquareKanban, Trash2, UserPlus, WifiOff, X } from "lucide-react";
import { APP, Api, mayOfferSignOut, channelAlerts, chatStream, connectIdOf, DESKTOP, deviceCodeOf, linkWaiting, proposalAlerts, sendToTerminal, signInTakenBy, terminalLink, uploadFiles, whenSignedOut, type HistorySession, type SessionRecord, type WorkspaceState } from "./api";
import { activeId, place, savePlace } from "./places";
import { menuBarSummary, useMenuBar } from "./menuBar";
import { shortRef, useOffline, useStore, toSession, workspaceRuns } from "./store";
import { workspaceWords } from "./workspaceWords";
import { useShellPoll } from "./shellPoll";
import { pageNotify } from "./pageNotices";
import { clock } from "./when";
import { failedWords, startEach } from "./fanout";
import { MODEL_PROVIDERS, type AgentKind, type Tab } from "./types";
import { agentsOffered } from "./agentsOffered";
import { continuable } from "./handover";
import { TitleBar } from "./components/TitleBar";
import { Sidebar } from "./components/Sidebar";
import { SessionView } from "./components/SessionView";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { endedHow } from "./ended";
import { isUnread, useSeen } from "./seen";
import { keepDrafts } from "./drafts";
import { OPEN_BOARD } from "./board";
// The terminal (xterm), the chat (Markdown) and the diff viewer load when first opened, not with the shell; so do the
// sign-in screen, which a signed-in page never shows, and a session's pull request buttons.
const TerminalPane = lazy(() => import("./components/TerminalPane").then((m) => ({ default: m.TerminalPane })));
const SignIn = lazy(() => import("./components/SignIn").then((m) => ({ default: m.SignIn })));
const PullRequestButton = lazy(() => import("./components/PullRequest").then((m) => ({ default: m.PullRequestButton })));
const PullFeedbackButton = lazy(() => import("./components/PullRequest").then((m) => ({ default: m.PullFeedbackButton })));
const PullReviewButton = lazy(() => import("./components/PullRequest").then((m) => ({ default: m.PullReviewButton })));
// design mode is the desktop app's alone: its preview button and pick dialog never load in a browser
const PreviewButton = lazy(() => import("./components/Design").then((m) => ({ default: m.PreviewButton })));
const PickDialog = lazy(() => import("./components/Design").then((m) => ({ default: m.PickDialog })));
const ChatPane = lazy(() => import("./components/ChatPane").then((m) => ({ default: m.ChatPane })));
const DiffPanel = lazy(() => import("./components/DiffPanel").then((m) => ({ default: m.DiffPanel })));
const FilesPanel = lazy(() => import("./components/FilesPanel").then((m) => ({ default: m.FilesPanel })));
const ActivityPanel = lazy(() => import("./components/ActivityPanel").then((m) => ({ default: m.ActivityPanel })));
const ShellPane = lazy(() => import("./components/ShellPane").then((m) => ({ default: m.ShellPane })));
const AccountDialog = lazy(() => import("./components/AccountDialog").then((m) => ({ default: m.AccountDialog })));
const ConnectApp = lazy(() => import("./components/ConnectApp").then((m) => ({ default: m.ConnectApp })));
const Licenses = lazy(() => import("./components/Licenses").then((m) => ({ default: m.Licenses })));
// the dialogs and the palette load when first opened (and, after the page settles, ahead of that: see App)
const NewSession = lazy(() => import("./components/NewSession").then((m) => ({ default: m.NewSession })));
const CommandPalette = lazy(() => import("./components/CommandPalette").then((m) => ({ default: m.CommandPalette })));
const Approvals = lazy(() => import("./components/Approvals").then((m) => ({ default: m.Approvals })));
const Channel = lazy(() => import("./components/Channel").then((m) => ({ default: m.Channel })));
const Agents = lazy(() => import("./components/Agents").then((m) => ({ default: m.Agents })));
// the live progress board and its times' setting
const Board = lazy(() => import("./components/Board").then((m) => ({ default: m.Board })));
const Calendar = lazy(() => import("./components/Calendar").then((m) => ({ default: m.Calendar })));
const TimeSettings = lazy(() => import("./components/TimeSettings").then((m) => ({ default: m.TimeSettings })));
const Connections = lazy(() => import("./components/Connections").then((m) => ({ default: m.Connections })));
const CloneDialog = lazy(() => import("./components/CloneDialog").then((m) => ({ default: m.CloneDialog })));
const ShipDialog = lazy(() => import("./components/ShipDialog").then((m) => ({ default: m.ShipDialog })));
// Continue in a new session: the owner's press alone finishes a session and starts a new one from its commit
const ContinueSession = lazy(() => import("./components/ContinueSession").then((m) => ({ default: m.ContinueSession })));
const Shortcuts = lazy(() => import("./components/Shortcuts").then((m) => ({ default: m.Shortcuts })));
// the control view, the main panel's default where no session is open: its map draws calls with RunTree's
// pieces, which carry the agents' API, so it loads as it is first shown, and is fetched ahead (AHEAD)
const ControlView = lazy(() => import("./components/ControlView").then((m) => ({ default: m.ControlView })));
import type { Again, NewSessionRequest } from "./components/NewSession";
import { useDesignPicks } from "./designPicks";
import { OpenIn, worktreeHere } from "./components/OpenIn";
import { agentLabel, verdictWords, type Decision } from "./components/bits";
import { Sheet } from "./components/Modal";
import { Logo } from "./components/TitleBar";
import { useMedia } from "./useMedia";
export { HIDDEN_EVERY, whileHidden } from "./shellPoll";

// what is fetched ahead, once the page has settled: the code of what a person opens first (the palette, New session,
// Linked sessions, Approvals, a session's pull request buttons), so the first use is instant
const AHEAD = [() => import("./components/CommandPalette"), () => import("./components/NewSession"),
  () => import("./components/Channel"), () => import("./components/Approvals"), () => import("./components/PullRequest"),
  () => import("./components/ControlView")];

// whether the list of sessions is hidden on a computer, kept in this browser; a phone's list is a
// drawer whatever this says
const LIST_HIDDEN = "pp-list-hidden";
function listHiddenKept(): boolean {
  try { return localStorage.getItem(LIST_HIDDEN) === "1"; } catch { return false; }
}
function keepListHidden(hidden: boolean) {
  try { if (hidden) localStorage.setItem(LIST_HIDDEN, "1"); else localStorage.removeItem(LIST_HIDDEN); } catch { /* this page only */ }
}

// each agent's program; Kimi K3, MiniMax M3 and DeepSeek run in claude, on the person's own key for the model
const PROGRAM: Record<AgentKind, string> = { claude: "claude", codex: "codex", gemini: "gemini", opencode: "opencode", grok: "grok", qwen: "qwen",
  kimi: "claude", minimax: "claude", deepseek: "claude", kimicode: "kimi", mcode: "mcode", shell: "shell", chat: "chat",
  custom: "custom" };

export function App() {
  const booted = useStore((s) => s.booted);
  const userId = useStore((s) => s.user?.id ?? null);
  // an invitation arrives as #invite=<token>; it is read once and taken out of the address bar
  const [invite, setInvite] = useState<string | null>(() => {
    const m = /^#invite=([A-Za-z0-9_-]+)$/.exec(location.hash);
    if (m) history.replaceState(null, "", location.pathname);
    return m ? m[1] : null;
  });

  useEffect(() => {
    whenSignedOut(() => useStore.getState().signOut());
    void useStore.getState().boot();
    // another window of the page (a pop-out of the map, another tab) took away the sign-in they share: this one signs
    // out too, rather than go on asking with none
    const taken = (e: StorageEvent) => { if (signInTakenBy(e) && useStore.getState().user) useStore.getState().signOut(); };
    window.addEventListener("storage", taken);
    return () => window.removeEventListener("storage", taken);
  }, []);

  if (!booted) return <Splash />;
  if (invite || !userId) {
    return <Suspense fallback={<Splash />}><SignIn invite={invite} onInviteDone={() => setInvite(null)} /></Suspense>;
  }
  // the signed-in shell, made anew for each account that signs in: what one had open on this page (a linked session it
  // was reading, a task to try again, a request to connect an app) is never shown to the next
  return <Shell key={userId} />;
}

function Shell() {
  const st = useStore();
  // design mode, in the desktop app: an element picked in a session's preview window
  const [designPick, clearPick] = useDesignPicks();
  useEffect(() => {
    if (designPick && useStore.getState().records.some((r) => r.id === designPick.session)) useStore.getState().select(designPick.session);
  }, [designPick]);
  const [newFor, setNewFor] = useState<string | null | undefined>(undefined);   // undefined: closed; null: no repo chosen
  const [again, setAgain] = useState<Again | null>(null);      // New session on another session's task
  // the palette: to go anywhere, or to choose a session to open beside the ones shown
  const [palette, setPalette] = useState<false | "go" | "beside">(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [approvalsOpen, setApprovalsOpen] = useState(false);
  // the linked sessions' item a link opened in Approvals (#item=<seq>), until Approvals closes
  const [itemShown, setItemShown] = useState<number | null>(null);
  const [channelOpen, setChannelOpen] = useState(false);
  // each linked machine's Claude Code sessions (the store's history), in the list beside the workspace's, whether or
  // not the workspace runs; the one chosen opens in the panel
  const [machineRead, setMachineRead] = useState<HistorySession | null>(null);
  // "Link your Mac" opens Linked sessions on linking a session: its name and where it runs asked first
  const [channelLinking, setChannelLinking] = useState(false);
  const openMachine = useCallback((s: HistorySession) => { setMachineRead(s); setChannelLinking(false); setChannelOpen(true); }, []);
  const openChannel = useCallback(() => { setMachineRead(null); setChannelLinking(false); setChannelOpen(true); }, []);
  const linkMac = useCallback(() => { setMachineRead(null); setChannelLinking(true); setChannelOpen(true); }, []);
  // the agents (agents.ts): open, with a chat to keep as one when Keep as an agent opened them; how many of their runs
  // wait for the owner comes with the shell's summary (the store's agentsWaiting)
  const [agentsOpen, setAgentsOpen] = useState<false | { keep?: string }>(false);
  // the calendar of the agents' schedules: opened from the palette, the agents'
  // header or #calendar, where the deployment gives the account a workspace, as the agents are
  const [calendarOpen, setCalendarOpen] = useState(false);
  // the live progress board: opened from the title bar, the palette, or anywhere else on the page by board.openBoard();
  // and its times' zone and format, from the account menu
  const [boardOpen, setBoardOpen] = useState(false);
  const [timesOpen, setTimesOpen] = useState(false);
  useEffect(() => {
    // as its button is: only where the deployment gives the account a workspace
    const openIt = () => { const ws = useStore.getState().ws; if (ws?.enabled && ws.allowed !== false) setBoardOpen(true); };
    window.addEventListener(OPEN_BOARD, openIt);
    return () => window.removeEventListener(OPEN_BOARD, openIt);
  }, []);
  // a board open when the workspace stops being offered is closed, so it does not open again by itself when it is
  // offered again
  const boardOffered = !!st.ws?.enabled && st.ws.allowed !== false;
  useEffect(() => { if (!boardOffered) setBoardOpen(false); }, [boardOffered]);
  // a calendar asked for by a link where the account has no workspace is closed once that is known, so it never opens
  // by itself when one is offered later
  useEffect(() => { if (st.ws && !boardOffered) setCalendarOpen(false); }, [st.ws, boardOffered]);
  // the agents and the calendar are one window at a time: opening one closes the other, so one Escape closes what shows
  const openAgents = useCallback((was: { keep?: string } = {}) => { setCalendarOpen(false); setAgentsOpen(was); }, []);
  const openCalendar = useCallback(() => { setAgentsOpen(false); setCalendarOpen(true); }, []);
  const [tabs, setTabs] = useState<Record<string, Tab>>({});
  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;
  const phone = useMedia("(max-width: 767px)");
  const phoneRef = useRef(phone);
  phoneRef.current = phone;
  // a session split: its agent on the left, and this tab beside it (never the agent's own). A phone shows no split,
  // so there a session's tabs are its own again; the split is back when the window is wide again
  const [splits, setSplits] = useState<Record<string, Tab>>({});
  const splitsRef = useRef(splits);
  splitsRef.current = splits;
  const besideOf = useCallback((id: string): Tab | undefined => (phoneRef.current ? undefined : splitsRef.current[id]), []);
  /** Show one of a session's tabs: beside its agent when it is split (the agent itself then closes the split). */
  const show = useCallback((id: string, t: Tab) => {
    if (besideOf(id)) {
      if (t !== "agent") { setSplits((cur) => ({ ...cur, [id]: t })); return; }
      setSplits((cur) => { const next = { ...cur }; delete next[id]; return next; });
    }
    setTabs((cur) => ({ ...cur, [id]: t }));
  }, [besideOf]);
  const toggleSplit = useCallback((id: string) => {
    if (besideOf(id)) { show(id, "agent"); focusPane(id); return; }
    setSplits((cur) => ({ ...cur, [id]: tabsRef.current[id] && tabsRef.current[id] !== "agent" ? tabsRef.current[id] : "changes" }));
  }, [show, besideOf]);
  // in the store, so that a form anywhere (a task's, an agent's) can open Connections for the sign-in or the account it needs
  const connections = useStore((s) => s.connectionsOpen);
  const setConnections = useStore((s) => s.setConnectionsOpen);
  const [cloneOpen, setCloneOpen] = useState(false);
  const [shipping, setShipping] = useState<string | null>(null);
  const [continuing, setContinuing] = useState<string | null>(null);
  const [account, setAccount] = useState<"account" | "storage" | "encryption" | "notifications" | "people" | "verify" | "device" | "apps" | null>(null);
  // a request to connect an app through the Claude app connector (#connect=ID, where /oauth/authorize sends it)
  const [connectId, setConnectId] = useState<string | null>(null);
  // a device's code that came in a link (#device=CODE), for the device pane to show and look up
  const [deviceCode, setDeviceCode] = useState<string | null>(null);
  const [keys, setKeys] = useState(false);
  // the licenses of what the app carries (the account menu's Licenses, the palette's)
  const [licenses, setLicenses] = useState(false);
  const [drawer, setDrawer] = useState(false);
  // the list of sessions hidden on a computer, as this browser keeps it
  const [listHidden, setListHidden] = useState(listHiddenKept);
  const hideList = useCallback((hidden: boolean) => {
    setListHidden(hidden);
    keepListHidden(hidden);
    // the keyboard goes to the button that undoes it, never lost with the one pressed
    setTimeout(() => document.querySelector<HTMLElement>(hidden ? 'button[aria-label="Show the list of sessions"]'
      : 'aside button[aria-label="Hide the list of sessions"]')?.focus(), 0);
  }, []);
  // the control view: no session open, which is where the main panel shows it
  const toControl = useCallback(() => useStore.setState({ selected: null, panes: [] }), []);

  // Polling for what has no live stream (shellPoll.ts: the server's summary, and the lists whose stamps moved); the chat
  // stream below carries chats.
  const signedIn = !!st.user;
  useShellPoll(signedIn);

  // in the desktop app, the place the window shows says whom it is signed in as, for its menu
  const email = st.user?.email;
  useEffect(() => {
    if (!DESKTOP || !email) return;
    const p = place(activeId());
    if (p && p.email !== email) savePlace({ ...p, email });
  }, [email]);
  const running = workspaceRuns(st.ws);

  // once the page has settled, fetch the code of what is opened first (AHEAD), so the first use is instant
  useEffect(() => {
    if (!signedIn) return;
    const t = setTimeout(() => { for (const load of AHEAD) void load(); }, 3000);
    return () => clearTimeout(t);
  }, [signedIn]);
  // what is read only while the workspace runs: its programs, what its Claude Code offers a chat, and the usage windows
  useEffect(() => {
    if (!signedIn || !running) return;
    void useStore.getState().refreshPrograms();
    void useStore.getState().refreshCaps();
    // a new workspace's checklist says whether GitHub is connected; with repositories already, Connections asks
    const g = setTimeout(() => { const st = useStore.getState(); if (!st.github && !st.repos.length) void st.refreshGithub(); }, 1500);
    const q = () => document.visibilityState === "visible" && void useStore.getState().refreshQuota();
    const c = setInterval(q, 60000);
    const first = setTimeout(q, 2500);
    return () => { clearTimeout(g); clearTimeout(first); clearInterval(c); };
  }, [signedIn, running]);

  useEffect(() => {
    if (!signedIn || !running) return;
    let soon: ReturnType<typeof setTimeout> | null = null;
    const close = chatStream(
      (session, ev) => {
        useStore.getState().addChat(session, ev);
        if (ev.kind === "status" || ev.kind === "ask" || ev.kind === "answered" || ev.kind === "result") {
          if (soon) clearTimeout(soon);
          soon = setTimeout(() => void useStore.getState().refresh(), 250);
        }
      },
      () => {
        useStore.getState().dropLive();          // what was live may have gone on without this page: the log says
        for (const id of Object.keys(useStore.getState().chat)) void useStore.getState().loadChat(id);
      },
      (session, d) => useStore.getState().addDelta(session, d),
    );
    return () => { close(); if (soon) clearTimeout(soon); };
  }, [signedIn, running]);

  // which other providers' models have a key, each time New session opens (Connections may have changed it)
  useEffect(() => {
    if (signedIn && running && (newFor !== undefined || again)) void useStore.getState().refreshModelProviders();
  }, [signedIn, running, newFor, again]);

  // the runs waiting for the owner, for the Agents button's count, come with the summary; read again as the Agents
  // panel closes, so what the owner answered there is off the count at once
  const agentsClosed = useRef(false);
  useEffect(() => {
    if (agentsOpen) agentsClosed.current = true;
    else if (agentsClosed.current) { agentsClosed.current = false; void useStore.getState().readSummary(); }
  }, [agentsOpen]);

  // A link into the app (a push notification's): #session=<id> opens that session, #approvals the approvals,
  // #channel the linked sessions, #account the account (the older page's link); #device=<code> (the tray's, or another
  // device's) the device pane with that code; #connect=<id> a request to connect an app, #apps Connected apps, and
  // #proposals a connected app's proposals (in Approvals); #agents the agents, #calendar their schedules' calendar;
  // #item=<seq> one item of the linked sessions' inbox, in Approvals (the apps' "Open it in your browser")
  useEffect(() => {
    if (!signedIn) return;
    const follow = () => {
      const h = location.hash;
      const m = /^#session=([0-9a-f]{12})$/.exec(h);
      if (m) {
        const st = useStore.getState();
        if (!st.records.some((r) => r.id === m[1])) return;      // not loaded yet: the next refresh tries again
        st.select(m[1]);
        history.replaceState(null, "", location.pathname + location.search);
      } else if (h === "#approvals") {
        setApprovalsOpen(true);
        history.replaceState(null, "", location.pathname + location.search);
      } else if (h === "#channel" || h === "#history") {
        setChannelOpen(true);                                   // the History list is in it: one list
        history.replaceState(null, "", location.pathname + location.search);
      } else if (h === "#account") {
        setAccount("account");
        history.replaceState(null, "", location.pathname + location.search);
      } else if (h === "#apps") {
        setAccount("apps");
        history.replaceState(null, "", location.pathname + location.search);
      } else if (/^#item=[0-9]{1,15}$/.test(h)) {
        setItemShown(Number(h.slice(6)));
        setApprovalsOpen(true);
        history.replaceState(null, "", location.pathname + location.search);
      } else if (h === "#proposals") {
        setApprovalsOpen(true);                                 // a connected app's proposals are in Approvals
        history.replaceState(null, "", location.pathname + location.search);
      } else if (connectIdOf(h)) {
        setConnectId(connectIdOf(h));
        history.replaceState(null, "", location.pathname + location.search);
      } else if (h === "#agents") {
        openAgents();
        history.replaceState(null, "", location.pathname + location.search);
      } else if (h === "#calendar") {
        openCalendar();
        history.replaceState(null, "", location.pathname + location.search);
      } else {
        const d = deviceCodeOf(h);
        if (d) {
          setDeviceCode(d);
          setAccount("device");
          history.replaceState(null, "", location.pathname + location.search);
        }
      }
    };
    follow();
    window.addEventListener("hashchange", follow);
    const unsub = useStore.subscribe((st, prev) => { if (st.records !== prev.records) follow(); });
    return () => { window.removeEventListener("hashchange", follow); unsub(); };
  }, [signedIn]);

  // Keyboard: ⌥1–9 the session in that row of the sidebar, ⌥↑/⌥↓ the one above or below. Caught before the page's
  // own handlers, so a terminal that has the focus does not take them; e.code, since ⌥ turns a digit into a sign.
  const order = useRef<string[]>([]);
  const setOrder = useCallback((ids: string[]) => { order.current = ids; }, []);
  // the list hidden on a computer has no rows to number or move through: the keys do nothing until it shows again,
  // never moving through the rows it had when it was hidden
  useEffect(() => { if (listHidden && !phone) order.current = []; }, [listHidden, phone]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.altKey || e.metaKey || e.ctrlKey || e.shiftKey) return;
      const ids = order.current;
      const at = ids.indexOf(useStore.getState().selected || "");
      let id: string | undefined;
      const digit = /^Digit([1-9])$/.exec(e.code);
      if (digit) id = ids[Number(digit[1]) - 1];
      else if (e.code === "ArrowDown") id = ids[at < 0 ? 0 : Math.min(ids.length - 1, at + 1)];
      else if (e.code === "ArrowUp") id = ids[at < 0 ? 0 : Math.max(0, at - 1)];
      else return;
      e.preventDefault();
      e.stopPropagation();
      if (id) useStore.getState().select(id);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);

  // Ctrl+`: the selected session's shell, or back to its agent (VS Code's key for its terminal); caught before a
  // terminal that has the focus, and what the tab opens to is given the focus
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.ctrlKey || e.metaKey || e.altKey || e.shiftKey || e.code !== "Backquote") return;
      const id = useStore.getState().selected;
      if (!id) return;
      e.preventDefault();
      e.stopPropagation();
      const beside = besideOf(id);
      if (beside) {
        // split: the shell beside the agent, then from one to the other
        if (beside !== "shell") { show(id, "shell"); focusPane(id, "beside"); return; }
        const there = !!(document.activeElement as HTMLElement | null)?.closest(`[data-pane="${id}"] [data-side="beside"]`);
        focusPane(id, there ? "agent" : "beside");
        return;
      }
      show(id, tabsRef.current[id] === "shell" ? "agent" : "shell");
      focusPane(id);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [show, besideOf]);

  // "?" lists the shortcuts, unless something is being typed (a field, the terminal)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "?" || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.closest("input, textarea, select, [contenteditable=true], .xterm"))) return;
      e.preventDefault();
      setKeys(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Keyboard: ⌘K palette, ⌘N new session.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return;
      if (e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((p) => (p ? false : "go")); }
      if (e.key.toLowerCase() === "n" && running) { e.preventDefault(); setNewFor(null); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [running]);

  const listed = useMemo(() => st.records.map((r) => toSession(r, st.accounts)), [st.records, st.accounts]);
  const byId = useMemo(() => new Map(st.records.map((r) => [r.id, r])), [st.records]);
  const pendingAsks = st.records.reduce((n, r) => n + (r.chat?.asks?.length || 0) + (r.running && r.activity?.state === "waiting" ? 1 : 0), 0);
  const spent = st.records.reduce((n, r) => n + (r.chat?.cost || 0), 0);
  // a phone shows one session at a time; the list is a drawer
  const panes = phone ? (st.selected ? [st.selected] : []) : st.panes.length ? st.panes : st.selected ? [st.selected] : [];
  // what is open on the page is seen; a session that did something while it was not is unread until it is
  const openKey = panes.join(",");
  const looked = useSeen((x) => x.seen);
  useEffect(() => {
    if (!st.records.length) return;      // not listed yet: nothing is forgotten on that account
    const open = new Set(openKey.split(","));
    useSeen.getState().observe(listed.map((x) => ({ id: x.id, updatedAt: x.updatedAt, open: open.has(x.id) })));
  }, [listed, openKey, st.records.length]);
  useEffect(() => {
    if (st.selected) useSeen.getState().chosen(st.selected, listed.find((x) => x.id === st.selected)?.updatedAt);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st.selected]);
  // the person's own agents, read again whenever New session (or Try with another agent) opens: one added a moment
  // ago is offered
  useEffect(() => {
    if (newFor === undefined && !again) return;
    void useStore.getState().refreshCustomAgents();
    if (useStore.getState().ws?.running) void useStore.getState().refreshAccounts();     // which account, and signed in
  }, [newFor, again]);
  // a removed session's unsaved edits go with it
  useEffect(() => { if (st.records.length) keepDrafts(st.records.map((r) => r.id)); }, [st.records]);
  const sessions = useMemo(() => {
    const open = new Set(openKey.split(","));
    return listed.map((x) => ({ ...x, unread: isUnread(looked, x.id, x.updatedAt, open.has(x.id)) ? 1 : 0 }));
  }, [listed, looked, openKey]);
  // the repositories added from a folder on this machine, and taking one out (its link only; the workspace refuses
  // while it has sessions, and says so)
  const linked = useMemo(() => Object.fromEntries(st.repos.filter((r) => r.linked).map((r) => [r.name, r.linked as string])), [st.repos]);
  const unlink = useCallback((name: string) => {
    void Api.unlinkRepo(name).then(() => useStore.getState().refresh()).catch((e) => useStore.getState().setError(e));
  }, []);

  // what connected apps proposed, as the badges count it (Proposals.tsx shows them, at the top of Approvals)
  const proposalsWaiting = proposalAlerts(st.proposals);
  // what needs the owner among the linked sessions: each silent one, each open question to the owner or to all, and
  // each request to be linked
  const channelWaiting = channelAlerts(st.links);
  // what the linked sessions put in Approvals, the one list: their open prompts (those to look at too), their proposals
  // and their questions to the owner; and of those, what channelWaiting does not count already. Their asks for more in
  // a task's grant wait there too, counted with every grant ask in st.grantWidenings, not here
  const linkedWaiting = linkWaiting(st.links);
  const promptsAndProposals = (st.links?.prompts?.length ?? 0) + (st.links?.proposals?.length ?? 0);

  // The tab's title counts what waits for the person, as the title bar's badges do: the sessions that need them, the
  // approvals and the asks, what connected apps proposed, the linked sessions' questions, prompts, proposals and
  // requests to be linked, and the agents' runs waiting. A session that starts to need them, or a chat whose turn ends, is announced while the
  // page is in the background (once notifications are allowed), in fixed words (pageNotices.ts): which session it is,
  // and what it waits for, stay on the page.
  const seen = useRef<Map<string, string>>(new Map());
  useEffect(() => {
    const waiting = sessions.filter((s) => s.state === "waiting").length + st.approvals.length + st.sessionAsks.length
      + st.grantWidenings.length + proposalsWaiting + channelWaiting + promptsAndProposals + st.agentsWaiting;
    document.title = waiting ? `(${waiting}) Proofpane Community` : "Proofpane Community";
    const notify = typeof Notification !== "undefined" && Notification.permission === "granted" && document.visibilityState === "hidden";
    for (const s of sessions) {
      const before = seen.current.get(s.id);
      if (notify && before && before !== s.state) {
        if (s.state === "waiting") pageNotify("session-waits", `pp-${s.id}`);
        else if (before === "working" && (s.state === "idle" || s.state === "done")) pageNotify("session-finished", `pp-${s.id}`);
      }
      seen.current.set(s.id, s.state);
    }
  }, [sessions, st.approvals.length, st.sessionAsks.length, st.grantWidenings.length, proposalsWaiting, channelWaiting, promptsAndProposals, st.agentsWaiting]);

  // a new session an agent asks for, announced in fixed words as well, never the asking session's title, the
  // repository or the agent's words; not for the asks already waiting when the page opened
  const seenAsks = useRef<Set<string> | null>(null);
  useEffect(() => {
    const first = seenAsks.current === null;
    const seenNow: Set<string> = seenAsks.current ?? new Set();
    const notify = typeof Notification !== "undefined" && Notification.permission === "granted" && document.visibilityState === "hidden";
    for (const a of st.sessionAsks) {
      if (!first && notify && !seenNow.has(a.id)) pageNotify("session-ask", `pp-ask-${a.id}`);
      seenNow.add(a.id);
    }
    seenAsks.current = seenNow;
  }, [st.sessionAsks]);

  // the desktop app's own menu-bar icon: what needs the person in the place the window shows, and from its menu the
  // session chosen, or the approvals (menuBar.ts)
  const menuBar = useMemo(() => (DESKTOP && signedIn ? menuBarSummary(place(activeId())?.label || "This Mac", sessions, st.approvals.length + st.sessionAsks.length + st.grantWidenings.length + proposalsWaiting, st.links) : null),
    [signedIn, sessions, st.approvals.length, st.sessionAsks.length, st.grantWidenings.length, proposalsWaiting, st.links]);
  useMenuBar(menuBar, (what) => {
    if (what.session && useStore.getState().records.some((r) => r.id === what.session)) useStore.getState().select(what.session);
    else if (what.approvals) setApprovalsOpen(true);
  });

  const createSessions = useCallback(async (req: NewSessionRequest) => {
    // several agents on one task are a group, so they can be compared and the best one kept; so are the agents started
    // on another session's task, with it, in its group or in a new one it joins
    const fresh = () => (Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => (b % 36).toString(36)).join("") + Date.now().toString(36)).slice(0, 32);
    const customs = req.customs || [];
    const group = req.again ? req.again.group || fresh() : req.agents.length + customs.length > 1 ? fresh() : undefined;
    const names = new Map((useStore.getState().customAgents || []).map((a) => [a.id, a.name]));
    type One = { custom: string } | { kind: AgentKind };
    const each: One[] = [...customs.map((custom) => ({ custom })), ...req.agents.map((kind) => ({ kind }))];
    // each in turn, and every result kept: one that cannot start neither stops the others nor, the dialog left open,
    // has the person start the others twice
    const started = await startEach(each, (one) => ("custom" in one ? names.get(one.custom) || "Your agent" : agentLabel(one.kind)),
      (one) => "custom" in one
        ? Api.createSession({
          repo: req.repo === "Workspace" ? null : req.repo, program: "custom", custom: one.custom, task: req.task,
          base: req.pull ? null : req.base || null, pull: req.pull, cols: 120, rows: 32, group,
        })
        : Api.createSession({
          repo: req.repo === "Workspace" ? null : req.repo,
          program: PROGRAM[one.kind],
          task: req.task || (one.kind === "shell" ? "Shell" : ""),
          base: req.pull ? null : req.base || null,
          pull: req.pull,
          cols: 120,
          rows: 32,
          group,
          chat_settings: one.kind === "chat" && req.chatSettings && Object.keys(req.chatSettings).length ? req.chatSettings : undefined,
          model_provider: (MODEL_PROVIDERS as readonly string[]).includes(one.kind) ? one.kind : undefined,
          account: one.kind === "claude" || one.kind === "chat" ? req.accounts?.claude : one.kind === "codex" ? req.accounts?.codex : undefined,
          teleport: one.kind === "claude" && req.teleport !== undefined ? req.teleport : undefined,
        }));
    const created: SessionRecord[] = started.created;
    if (!created.length) throw new Error(failedWords(started));       // none started: the dialog stays, to change it
    if (started.failed.length) useStore.getState().setError(new Error(failedWords(started)));
    if (req.again && !req.again.group && group) await Api.joinGroup(req.again.id, group);
    await useStore.getState().refresh();
    showStarted(created, req.again);
  }, []);

  // A fan-out's sessions side by side, each on its changes, to keep the best one.
  const compare = (rec: SessionRecord) => {
    const members = st.records.filter((r) => r.group === rec.group).slice(0, 4);
    useStore.setState({ panes: members.map((r) => r.id), selected: rec.id });
    setTabs((cur) => ({ ...cur, ...Object.fromEntries(members.map((r) => [r.id, "changes" as Tab])) }));
    setSplits((cur) => Object.fromEntries(Object.entries(cur).filter(([k]) => !members.some((r) => r.id === k))));
  };

  if (!st.user) return null;           // signed out: App shows the sign-in screen in this shell's place

  // the workspace given to this account (a deployment may give none); whether a Mac of the owner's is linked, which the
  // empty states offer to do when none is: null until the linked sessions are read, so it is never offered to an owner
  // whose Mac is linked before the page knows it
  const offered = !!st.ws?.enabled && st.ws.allowed !== false;
  const macLinked = st.links ? st.links.links.some((l) => l.kind === "local") : null;
  // the session chosen, while it is listed (a stopped workspace's are not)
  const chosen = st.selected && byId.has(st.selected) ? st.selected : null;
  // The list: the workspace's sessions while it runs, and every linked machine's sessions always, beside them on a wide
  // screen and in the drawer on a phone.
  const sidebar = (inDrawer: boolean) => {
    const away = (fn: () => void) => () => { if (inDrawer) setDrawer(false); fn(); };
    return (
      <Sidebar
        workspace={running}
        loading={!st.ws || st.history === null || st.links === null}
        repos={st.repos.map((r) => r.name)}
        local={st.ws?.provider === "local"}
        linked={linked}
        missing={st.repos.filter((r) => r.missing).map((r) => r.name)}
        onUnlink={unlink}
        sessions={sessions}
        selected={st.selected}
        onSelect={(id) => { st.select(id); if (inDrawer) setDrawer(false); }}
        onCloseSession={(id) => { void st.closeSession(id); focusList(); }}
        onNew={(repo) => { if (inDrawer) setDrawer(false); setNewFor(repo ?? null); }}
        onClone={away(() => setCloneOpen(true))}
        onSettings={away(() => setConnections(true))}
        onOrder={inDrawer ? undefined : setOrder}
        machine={st.history ?? undefined}
        onMachine={(m) => { if (inDrawer) setDrawer(false); openMachine(m); }}
        macLinked={!!macLinked}
        onLink={away(linkMac)}
        onClose={inDrawer ? () => setDrawer(false) : undefined}
        onHide={inDrawer ? undefined : () => hideList(true)}
        onControl={running ? away(toControl) : undefined}
        control={running && panes.length === 0}
      />
    );
  };
  const main = !st.ws ? <Splash />
    : !offered ? <NoWorkspace detail={st.ws.detail} macLinked={macLinked} onLink={linkMac} />
    : !running ? <WorkspaceOff ws={st.ws} onStart={st.startWorkspace} macLinked={macLinked} onLink={linkMac} />
    : null;

  return (
    <div className="flex h-full flex-col">
      <TitleBar
        workspace={!st.ws ? null : !offered ? false : { provider: (st.ws.provider as "fly" | "local") || "fly",
          running: !!st.ws.running && !st.ws.starting, made: st.ws.made, starting: !!st.ws.starting, region: st.ws.region }}
        pendingApprovals={pendingAsks + st.approvals.length + st.sessionAsks.length + st.grantWidenings.length + proposalsWaiting + linkedWaiting}
        spentTodayUsd={spent > 0 ? spent : undefined}   /* the chats' own estimates, summed */
        account={st.user.email}
        onSearch={() => setPalette("go")}
        onApprovals={() => setApprovalsOpen(true)}
        channelAlerts={channelWaiting}
        onChannel={openChannel}
        agentsWaiting={st.agentsWaiting}
        onAgents={offered ? () => openAgents() : undefined}
        onBoard={offered ? () => setBoardOpen(true) : undefined}
        onTimes={offered ? () => setTimesOpen(true) : undefined}
        onMenu={phone ? () => setDrawer(true) : undefined}
        menuOpen={drawer}
        quotas={st.quotas}
        accounts={st.accounts}
        onChooseAccount={(kind, id) => {
          void (async () => {
            try { await Api.chooseAccount(kind, id); } catch (e) { st.setError(e); }
            await Promise.all([useStore.getState().refreshQuota(), useStore.getState().refreshClaude()]);
          })();
        }}
        onAccounts={() => setConnections(true)}
        admin={!!st.user.admin}
        onAccount={setAccount}
        onLicenses={() => setLicenses(true)}
        onSignOut={mayOfferSignOut() ? st.signOut : undefined}   /* never the desktop app's window onto this Mac */
      />
      <Offline />
      {running && st.claude?.installed && st.claude.signed_in === false && (
        <div className="flex h-9 flex-none items-center gap-3 border-b border-wait/30 bg-wait/[0.07] px-4 text-[12.5px]">
          <span className="dot" style={{ background: "var(--color-wait)" }} />
          <span className="text-fg-dim">{(() => {
            const v = st.accounts?.claude;
            const on = v && v.active !== "default" ? v.accounts.find((a) => a.id === v.active)?.name : undefined;
            return on ? `The Claude account new sessions start on, ${on}, is not signed in, so they cannot start on it.`
              : "Claude Code is not signed in in this workspace, so Claude sessions and chats cannot start.";
          })()}</span>
          <button className="btn btn-sm ml-auto" onClick={() => setConnections(true)}>Sign in to Claude</button>
        </div>
      )}
      <div className="flex min-h-0 flex-1">
      {/* the list hidden on a computer: a narrow edge with the button that shows it again */}
      {!phone && listHidden && (
        <div className="flex w-10 flex-none flex-col items-center border-r border-line bg-panel pt-2">
          <button className="icon-btn" onClick={() => hideList(false)} aria-label="Show the list of sessions" title="Show the list of sessions">
            <PanelLeftOpen size={15} />
          </button>
        </div>
      )}
      <Group orientation="horizontal" className="min-h-0 flex-1">
        {!phone && !listHidden && (
          <>
            <Panel id="sessions" defaultSize="280px" minSize="210px" maxSize="440px">{sidebar(false)}</Panel>
            <Separator aria-label="Resize the list of sessions" className="w-px bg-line hover:bg-accent data-[resize-handle-active]:bg-accent" />
          </>
        )}
        {/* the main panel: a state of the workspace while it does not run (the list beside it still shows the linked
            machines' sessions), else the control view where no session is open (its map, or the first steps where
            there is nothing to draw yet), or the sessions open */}
        <Panel id="main" minSize="30%" role="main">
          {main ?? (panes.length === 0 ? (
            <ErrorBoundary what="The control view">
              <Suspense fallback={<Splash />}>
                <ControlView
                  onOpenSession={(id) => st.select(id)}
                  onApprovals={() => setApprovalsOpen(true)}
                  onNew={() => setNewFor(null)}
                  empty={(
                    <Empty
                      local={st.ws?.provider === "local"}
                      hasRepos={st.repos.length > 0}
                      hasSessions={st.records.length > 0}
                      claude={st.claude?.signed_in ?? null}
                      claudeInstalled={st.claude ? st.claude.installed : null}
                      agents={st.programs ? Object.values(st.programs).filter(Boolean).length : null}
                      github={st.github ? !!st.github.user : null}
                      macLinked={macLinked}
                      onNew={() => setNewFor(null)}
                      onClone={() => setCloneOpen(true)}
                      onConnections={() => setConnections(true)}
                      onLink={linkMac}
                    />
                  )}
                />
              </Suspense>
            </ErrorBoundary>
          ) : (
            <Group orientation="horizontal" className="h-full">
              {panes.map((id, i) => {
                const rec = byId.get(id);
                if (!rec) return null;
                const tab = tabs[id] || "agent";
                const beside = phone ? undefined : splits[id];
                return (
                  <Fragment key={id}>
                    {i > 0 && <Separator aria-label="Resize the sessions side by side" className="w-px bg-line hover:bg-accent data-[resize-handle-active]:bg-accent" />}
                    <Panel minSize="18%">
                      <div data-pane={id} className={`h-full ${panes.length > 1 && st.selected === id ? "ring-1 ring-inset ring-accent/40" : ""}`} onMouseDown={() => st.selected !== id && useStore.setState({ selected: id })}>
                        <SessionView
                          session={toSession(rec, st.accounts)}
                          tab={beside || tab}
                          onTab={(t) => show(id, t)}
                          split={!!beside}
                          onSplit={phone ? undefined : () => toggleSplit(id)}
                          onBeside={!phone && panes.length < 4 ? () => setPalette("beside") : undefined}
                          groupSize={rec.group ? st.records.filter((r) => r.group === rec.group).length : 0}
                          onCompare={rec.group ? () => compare(rec) : undefined}
                          onClose={panes.length > 1 ? () => st.closePane(id) : undefined}
                          onRename={async (t) => { await Api.renameSession(id, t); await st.refresh(); }}
                          renaming={renaming === id}
                          onRenamed={() => setRenaming(null)}
                          actions={<SessionActions rec={rec} grouped={!!rec.group && st.records.filter((r) => r.group === rec.group).length > 1} onShip={() => setShipping(rec.id)}
                            onAgain={triable(rec) ? () => setAgain(againOf(rec)) : undefined}
                            onContinue={continuable(rec) ? () => setContinuing(rec.id) : undefined}
                            onKeep={rec.program === "chat" && rec.repo ? () => openAgents({ keep: rec.id }) : undefined} />}
                        >
                          {beside ? <SplitBody rec={rec} beside={beside} /> : <TabBody rec={rec} tab={tab} />}
                        </SessionView>
                      </div>
                    </Panel>
                  </Fragment>
                );
              })}
            </Group>
          ))}
        </Panel>
      </Group>
      </div>

      {/* a phone's list of sessions: a dialog over the page, the keyboard kept in it until it closes (Modal's Sheet) */}
      {phone && drawer && (
        <Sheet label="Sessions" onClose={() => setDrawer(false)} first='[aria-current="true"]'>{sidebar(true)}</Sheet>
      )}
      <ErrorBoundary what="A window" inline>
      <Suspense fallback={null}>
      {(newFor !== undefined || again) && (
        <NewSession
          again={again || undefined}
          repos={st.repos.map((r) => r.name)}
          githubRepos={st.repos.filter((r) => r.label && !r.label.startsWith("workspace/")).map((r) => r.name)}
          customAgents={st.customAgents || []}
          accounts={st.accounts}
          // the session chosen's repository, else the newest listed's, as when the page chose that one itself
          defaultRepo={newFor || (st.records.find((r) => r.id === st.selected) ?? st.records.filter((r) => !r.closed).slice(-1)[0])?.repo || undefined}
          provider={st.ws?.provider}
          held={st.held}
          agents={agentsOffered(st)}
          onStart={createSessions}
          onClose={() => { setNewFor(undefined); setAgain(null); }}
          onClone={() => { setNewFor(undefined); setCloneOpen(true); }}
          onConnections={() => { setNewFor(undefined); setAgain(null); setConnections(true); }}
        />
      )}
      {channelOpen && (
        <Suspense fallback={null}>
          <Channel key={machineRead ? `${machineRead.link_id}/${machineRead.id}` : channelLinking ? "linking" : "list"}
            reading={machineRead} linking={channelLinking}
            onClose={() => { setChannelOpen(false); setMachineRead(null); setChannelLinking(false); }} />
        </Suspense>
      )}
      {agentsOpen && <Agents keep={agentsOpen.keep ?? null} onClose={() => setAgentsOpen(false)}
        onCalendar={offered ? openCalendar : undefined} />}
      {calendarOpen && offered && (
        <Calendar onClose={() => setCalendarOpen(false)} onAgents={() => openAgents()} />
      )}
      {boardOpen && offered && (
        // its own boundary: the board failing on what it read is closed alone, the other windows kept
        <ErrorBoundary what="The progress board" inline onClose={() => setBoardOpen(false)}>
          <Board onClose={() => setBoardOpen(false)} onOpenSession={(id) => { setBoardOpen(false); st.select(id); }} />
        </ErrorBoundary>
      )}
      {timesOpen && <TimeSettings onClose={() => setTimesOpen(false)} />}
      {approvalsOpen && (
        <Approvals records={st.records} requests={st.approvals} sessionAsks={st.sessionAsks} proposals={st.proposals} grantWidenings={st.grantWidenings} item={itemShown ?? undefined} onClose={() => { setApprovalsOpen(false); setItemShown(null); }} onOpen={(id) => { setApprovalsOpen(false); setItemShown(null); st.select(id); }} />
      )}
      {connections && <Connections onClose={() => setConnections(false)} />}
      {shipping && byId.get(shipping) && (
        <ShipDialog
          rec={byId.get(shipping)!}
          others={byId.get(shipping)!.group ? st.records.filter((r) => r.group === byId.get(shipping)!.group && r.id !== shipping) : []}
          onClose={() => setShipping(null)}
          onConnections={() => { setShipping(null); setConnections(true); }}
        />
      )}
      {continuing && byId.get(continuing) && <ContinueSession rec={byId.get(continuing)!} onClose={() => setContinuing(null)} />}
      {account && <AccountDialog tab={account} deviceCode={deviceCode ?? undefined} onClose={() => { setAccount(null); setDeviceCode(null); }} />}
      {connectId && <ConnectApp id={connectId} onClose={() => setConnectId(null)} onApps={() => { setConnectId(null); setAccount("apps"); }} />}
      {designPick && <PickDialog event={designPick} rec={st.records.find((r) => r.id === designPick.session)} onClose={clearPick} />}
      {keys && <Shortcuts onClose={() => setKeys(false)} />}
      {licenses && <Licenses onClose={() => setLicenses(false)} />}
      {cloneOpen && <CloneDialog onClose={() => setCloneOpen(false)} onConnections={() => { setCloneOpen(false); setConnections(true); }}
        onCloned={(name) => { setCloneOpen(false); setNewFor(name); }} />}
      {/* last: the palette opens over any dialog, and its Escape is the top layer's (Modal.useEscape) */}
      {palette && (
        <CommandPalette
          sessions={palette === "beside" ? sessions.filter((x) => !panes.includes(x.id)) : sessions}
          beside={palette === "beside"}
          onPick={(id) => {
            // a closed session, opened here, is put back in the list
            if (byId.get(id)?.closed) void st.reopenSession(id);
            if (palette === "beside" || st.panes.length > 1) st.openBeside(id);
            else st.select(id);
          }}
          onClose={() => setPalette(false)}
          filesOf={(() => { const r = st.selected ? byId.get(st.selected) : undefined; return r?.repo ? { id: r.id, title: toSession(r).title } : null; })()}
          onFile={(id, path) => { st.select(id); show(id, "files"); st.openFile(id, path); }}
          actions={[
            // what needs the workspace is offered while it runs; while it does not, starting it comes first
            ...(running ? [{ id: "new", label: "New session", icon: <Plus size={14} />, hint: "⌘N", run: () => setNewFor(null) }]
              : offered && !st.ws?.starting ? [{ id: "start-ws", label: "Start the workspace", icon: <Power size={14} />, run: () => void st.startWorkspace() }] : []),
            ...(running && panes.length > 0 ? [{ id: "control", label: "Control view: every session and its agents, on a map", icon: <Network size={14} />, run: toControl }] : []),
            ...(!phone ? [{ id: "list", label: listHidden ? "Show the list of sessions" : "Hide the list of sessions",
              icon: listHidden ? <PanelLeftOpen size={14} /> : <PanelLeftClose size={14} />, run: () => hideList(!listHidden) }] : []),
            { id: "channel", label: "Linked sessions: your machines and their Claude Code sessions, by repository, and the sessions linked to talk", icon: <Radio size={14} />, run: openChannel },
            ...(offered ? [{ id: "agents", label: "Agents: chats kept under a name and run again", icon: <Bot size={14} />, run: () => openAgents() }] : []),
            ...(offered ? [{ id: "calendar", label: "Calendar: your agents' schedules, their coming times and what came of their firings", icon: <CalendarDays size={14} />, run: openCalendar }] : []),
            ...(offered ? [{ id: "board", label: "Progress board: what was done, what runs now, and what is next", icon: <SquareKanban size={14} />, run: () => setBoardOpen(true) }] : []),
            ...(running ? [{ id: "clone", label: st.ws?.provider === "local" ? "Add a repository, from this Mac or cloned" : "Clone a repository", icon: <Download size={14} />, run: () => setCloneOpen(true) }] : []),
            ...(chosen ? [{ id: "rename", label: "Rename this session", icon: <Pencil size={14} />, run: () => setRenaming(chosen) }] : []),
            ...(chosen ? [{ id: "unread", label: "Mark this session unread, until it is chosen again", icon: <Circle size={14} />, run: () => useSeen.getState().markUnread(chosen) }] : []),
            ...(chosen && byId.get(chosen)?.repo ? [{
              id: "search-files", label: "Search the text of this session's files", icon: <Search size={14} />,
              withQuery: (q: string) => `Search this session's files for “${q}”`,
              run: (q: string) => { show(chosen, "files"); st.openSearch(chosen, q); },
            }] : []),
            ...(chosen && triable(byId.get(chosen)!) ? [{
              id: "again", label: "Try this session's task with another agent too, to compare", icon: <CopyPlus size={14} />,
              run: () => setAgain(againOf(byId.get(chosen)!)),
            }] : []),
            ...(chosen && !phone ? [{
              id: "split", label: splits[chosen] ? "Close the split: the agent alone" : "Split this session: its agent, and beside it its changes, files or shell",
              icon: <PanelRightOpen size={14} />, run: () => toggleSplit(chosen),
            }] : []),
            ...(running ? [{ id: "connections", label: "Connections: Claude, GitHub, model keys, Linear, programs that ask", icon: <Plug size={14} />, run: () => setConnections(true) }] : []),
            { id: "notify", label: "Notifications: on this page and on this device", icon: <Bell size={14} />, run: () => setAccount("notifications") },
            ...(running ? [{ id: "stop-ws", label: "Stop the workspace", icon: <Power size={14} />, run: () => void st.stopWorkspace() }] : []),
            { id: "account", label: "Account and passkeys", icon: <KeyRound size={14} />, run: () => setAccount("account") },
            { id: "encryption", label: "Encryption: what linked sessions send, readable only where you unlock it", icon: <KeyRound size={14} />, run: () => setAccount("encryption") },
            { id: "apps", label: "Connected apps: the apps you let act for you, such as Claude", icon: <AppWindow size={14} />, run: () => setAccount("apps") },
            ...(st.user.admin ? [{ id: "invite", label: "Invite someone", icon: <UserPlus size={14} />, run: () => setAccount("people") }] : []),
            { id: "check", label: "Check a signed record", icon: <FileCheck2 size={14} />, run: () => setAccount("verify") },
            { id: "keys", label: "Keyboard shortcuts", icon: <Keyboard size={14} />, hint: "?", run: () => setKeys(true) },
            { id: "licenses", label: "Licenses: what this app carries, and under whose license", icon: <Scale size={14} />, run: () => setLicenses(true) },
            ...(APP ? [] : [{ id: "old", label: "Verify a pasted change (opens the verifier page)", icon: <Logo size={14} />, run: () => { location.href = "/#verify"; } }]),
            // this Mac's account in the desktop app signs in only by the app: there is no signing in again
            ...(mayOfferSignOut() ? [{ id: "out", label: "Sign out", icon: <Power size={14} />, run: () => st.signOut() }] : []),
          ]}
        />
      )}
      </Suspense>
      </ErrorBoundary>
      {st.error && (
        <div role="alert" className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 rounded-lg border border-fail/50 bg-raised px-4 py-2.5 text-[12.5px] text-fg shadow-xl">{st.error}</div>
      )}
      {st.notice && !st.error && (
        <div role="status" className={`fixed bottom-4 left-1/2 z-50 -translate-x-1/2 rounded-lg border bg-raised px-4 py-2.5 text-[12.5px] text-fg shadow-xl ${
          st.notice.tone === "done" ? "border-done/50" : st.notice.tone === "wait" ? "border-wait/50" : "border-fail/50"}`}>{st.notice.text}</div>
      )}
    </div>
  );

}

/** Give the focus to what a session's pane shows once it is there: its terminal, else its chat's message box (a tab
 *  loads its code the first time it opens, so it is looked for a few times); in a split, on the side named. */
/** Sessions just started, shown: one selected; several side by side; started to try another's task, beside it. What is
 *  typed next goes to the first of them: its terminal, or its chat's box, once it shows (up to 3 s). */
export function showStarted(created: { id: string }[], again?: { id: string }) {
  if (again) {
    useStore.setState({ panes: [again.id, ...created.map((c) => c.id)].slice(0, 4), selected: created[0]?.id ?? again.id });
  } else if (created.length > 1) {
    useStore.setState({ panes: created.slice(0, 4).map((c) => c.id), selected: created[0].id });
  } else if (created[0]) {
    useStore.getState().select(created[0].id);
  }
  if (created[0]) focusPane(created[0].id, undefined, 60);
}

export function focusPane(id: string, side?: "agent" | "beside", tries = 20) {
  setTimeout(() => {
    const pane = document.querySelector(`[data-pane="${id}"]`);
    const root = side ? pane?.querySelector(`[data-side="${side}"]`) || pane : pane;
    const el = root?.querySelector<HTMLElement>(".xterm-helper-textarea") || root?.querySelector<HTMLElement>("textarea");
    if (el) el.focus();
    else if (tries > 0) focusPane(id, side, tries - 1);
  }, 50);
}

/** One of a session's tabs, loaded when first shown, and kept apart from the rest of the page if it fails. */
function TabBody({ rec, tab }: { rec: SessionRecord; tab: Tab }) {
  return (
    <ErrorBoundary key={tab} what={`The ${tab === "agent" ? (rec.program === "chat" ? "chat" : "terminal") : tab} tab`}>
      <Suspense fallback={<Note text="Loading…" />}>
        <SessionBody rec={rec} tab={tab} />
      </Suspense>
    </ErrorBoundary>
  );
}

const SPLIT_ACROSS = 880;     // a pane at least this wide splits left and right; a narrower one, top and bottom

/** A session split: its agent, and beside it another of its tabs, each side resizable; side by side in a wide pane,
 *  one above the other in a narrow one. */
function SplitBody({ rec, beside }: { rec: SessionRecord; beside: Tab }) {
  const ref = useRef<HTMLDivElement>(null);
  const [across, setAcross] = useState(true);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setAcross(e.contentRect.width >= SPLIT_ACROSS));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return (
    <div ref={ref} className="h-full">
      <Group orientation={across ? "horizontal" : "vertical"} className="h-full">
        <Panel minSize="25%"><div data-side="agent" className="h-full"><TabBody rec={rec} tab="agent" /></div></Panel>
        <Separator aria-label="Resize the split" className={`${across ? "w-px" : "h-px"} bg-line hover:bg-accent data-[resize-handle-active]:bg-accent`} />
        <Panel minSize="20%"><div data-side="beside" className="h-full border-line"><TabBody rec={rec} tab={beside} /></div></Panel>
      </Group>
    </div>
  );
}

function SessionBody({ rec, tab }: { rec: SessionRecord; tab: Tab }) {
  const link = useMemo(() => terminalLink(rec.id), [rec.id]);
  if (tab === "changes") {
    const reviewTo = rec.program === "chat" ? "chat" : rec.running && (rec.program === "claude" || rec.program === "codex") ? "terminal" : null;
    return rec.repo ? <DiffPanel session={rec.id} reviewTo={reviewTo} /> : <Note text="A session without a repository has no changes to review." />;
  }
  if (tab === "files") return rec.repo ? <FilesPanel session={rec.id} /> : <Note text="A session without a repository has no worktree to browse." />;
  if (tab === "activity") return <ActivityPanel rec={rec} />;
  if (tab === "shell") return rec.repo ? <ShellPane rec={rec} /> : <Note text="A session without a repository has no worktree to open a shell in." />;
  if (rec.program === "chat") return <ChatPane record={rec} />;
  if (!rec.running) return <Stopped rec={rec} />;
  const terminal = <TerminalPane key={rec.id} link={link} session={rec.id} onFiles={rec.repo ? (files) => uploadFiles(rec.id, files) : undefined} />;
  if (!(rec.program in UNPROMPTED) && !(rec.program === "custom" && rec.custom?.task === "typed")) return terminal;
  return (
    <div className="flex h-full flex-col">
      <TaskBar rec={rec} />
      <div className="min-h-0 flex-1">{terminal}</div>
    </div>
  );
}

// agents that cannot be started with their task, and what comes before their prompt. Kimi Code 2.1.1: its only prompt
// on the command line (-p) runs once and exits, and it asks first whether to trust the folder, which an Enter answers
// (trusting it; "Don't trust" ends it): so the task is typed without one, for the person to send
const UNPROMPTED: Record<string, string> = {
  kimi: "Kimi Code asks first whether to trust this folder (trusting starts the MCP servers the repository lists). Answer it, then",
};

/** Over the terminal of an agent that cannot be started with its task: a button that types the task at its prompt,
 *  without sending it, so nothing it asks first is answered by it. It stays until it is put away; this device remembers. */
function TaskBar({ rec }: { rec: SessionRecord }) {
  const setError = useStore((s) => s.setError);
  const key = `pp-task-typed-${rec.id}`;
  const [done, setDone] = useState(() => { try { return localStorage.getItem(key) === "1"; } catch { return false; } });
  const [typed, setTyped] = useState(false);
  const [busy, setBusy] = useState(false);
  if (done) return null;
  const finish = () => { setDone(true); try { localStorage.setItem(key, "1"); } catch { /* this device keeps nothing */ } };
  return (
    <div className="flex flex-none items-center gap-2 border-b border-line bg-panel px-3 py-1.5 text-[12px] text-fg-dim">
      <span className="min-w-0 flex-1 truncate" title={rec.task}>
        {typed ? "The task is at its prompt: press Enter in the terminal to send it."
          : rec.program === "custom" ? `${rec.custom?.name || "Your agent"} takes its task at its prompt:` : `${UNPROMPTED[rec.program]}:`}
      </span>
      <button className="btn btn-sm btn-outline" disabled={busy} title="Type the session's task at its prompt, as if pasted, without sending it"
        onClick={async () => { setBusy(true); try { await sendToTerminal(rec.id, rec.task, false); setTyped(true); } catch (e) { setError(e); } finally { setBusy(false); } }}>
        {busy ? <Loader2 size={12} className="animate-spin" /> : null} {typed ? "Type it again" : "Type the task"}
      </button>
      <button className="icon-btn !h-6 !w-6" title="Put this away" onClick={finish}><X size={13} /></button>
    </div>
  );
}

/** Whether more agents can try a session's task: an agent's or a chat's, in a repository, from a commit it knows. */
function triable(rec: SessionRecord) {
  return !!rec.repo && !!rec.base && !["shell", "github", "claude-sign-in"].includes(rec.program);
}

function againOf(rec: SessionRecord): Again {
  return { id: rec.id, title: toSession(rec).title, repo: rec.repo!, task: rec.task, base: rec.base!, group: rec.group || undefined };
}

/** A session an agent asked for, which the person started (the agent's asks.py): the session that asked, to open. */
function AskedBy({ id }: { id: string }) {
  const by = useStore((s) => s.records.find((r) => r.id === id));
  const select = useStore((s) => s.select);
  return (
    <button className="btn btn-sm" disabled={!by} onClick={() => select(id)}
      title={by ? `The agent in "${toSession(by).title}" asked for this session, and you started it` : "An agent in a session since removed asked for this session, and you started it"}>
      <Bot size={13} /><span className="btn-label"> asked for</span>
    </button>
  );
}

/** What a finish did, in a sentence: committed or not, and what the verdict means for the person. */
export function finishWords(r: Decision & { committed?: boolean; files?: number | null; status?: string; reason?: string | null }, branch?: string | null): { text: string; tone: "done" | "wait" | "fail" } {
  // the files changed since the session started, not those of the commit just made, which may be fewer
  const files = typeof r.files === "number" ? `; ${r.files} ${r.files === 1 ? "file" : "files"} changed since it started` : "";
  const done = `${r.committed ? `Committed${branch ? ` to ${branch}` : ""}` : "Nothing new to commit"}${files}`;
  if (r.status === "skipped") return { text: `${done}; not verified: ${r.reason || "skipped"}.`, tone: "wait" };
  if (r.status === "error") return { text: `${done}; not verified: ${r.reason || "the check could not run"}.`, tone: "fail" };
  const said = verdictWords(r);
  return { text: `${done}. ${said.text}.`, tone: said.tone };
}

/** The keyboard put in the list once a session is gone from the page (removed, or closed), never lost with its button:
 *  on the session chosen, else on New session; with the list hidden on a computer, on the button that shows it. */
function focusList() {
  setTimeout(() => (document.querySelector<HTMLElement>('aside button[aria-current="true"]')
    || document.querySelector<HTMLElement>('aside button[title^="New session"]')
    || document.querySelector<HTMLElement>('button[aria-label="Show the list of sessions"]'))?.focus(), 0);
}

export function SessionActions({ rec, grouped, onShip, onAgain, onKeep, onContinue }: { rec: SessionRecord; grouped: boolean; onShip: () => void; onAgain?: () => void; onKeep?: () => void; onContinue?: () => void }) {
  const setError = useStore((s) => s.setError);
  const say = useStore((s) => s.say);
  const refresh = useStore((s) => s.refresh);
  const closeSession = useStore((s) => s.closeSession);
  const reopenSession = useStore((s) => s.reopenSession);
  // a repository with no remote to push to, or one elsewhere than GitHub (where Ship opens its pull request), is not
  // shipped: Finish (commit, verify, sign) is what it is offered
  const repo = useStore((s) => s.repos.find((r) => r.name === rec.repo));
  const shippable = repo?.origin !== false && repo?.github !== false;
  // a repository added from this machine is merged here instead, into the branch the session started from
  const mergeable = !shippable && !!repo?.linked;
  const into = shortRef(rec.base_ref) || repo?.branch || "its branch";
  // and it is the screen's one filled action only when it is the next step, with the agent not at work on it: Finish,
  // while something is left to commit; Ship, while something changed that is not shipped yet
  const working = toSession(rec).state === "working";
  const size = rec.diff;
  const toCommit = !!size && (size.uncommitted ?? size.files > 0);
  const finishReady = !working && toCommit;
  const shipReady = !working && !!size && size.files > 0 && (toCommit || !rec.shipped);
  // merged once nothing is left to commit, and not again for the same finish
  const merged = !!rec.landed && rec.landed.head === rec.finished?.head;
  const mergeReady = !working && !!size && size.files > 0 && !toCommit && !merged;
  const [busy, setBusy] = useState("");
  const [removing, setRemoving] = useState(false);       // the first click of two: a dialog box does not show everywhere
  const run = async (what: string, fn: () => Promise<unknown>) => {
    setBusy(what);
    try { await fn(); await refresh(); } catch (e) { setError(e); } finally { setBusy(""); }
  };
  const isChat = rec.program === "chat";
  return (
    <>
      {rec.asked_by && <AskedBy id={rec.asked_by} />}
      {/* their code is fetched ahead once the page settles (AHEAD); until it is there, they are not shown */}
      <Suspense fallback={null}>
        {rec.shipped?.url ? <PullRequestButton rec={rec} /> : <><PullFeedbackButton rec={rec} /><PullReviewButton rec={rec} /></>}
      </Suspense>
      {rec.repo && DESKTOP && <Suspense fallback={null}><PreviewButton rec={rec} /></Suspense>}
      {rec.repo && worktreeHere() && <OpenIn session={rec.id} />}
      {onKeep && (
        <button className="btn btn-sm" onClick={onKeep} title="Keep this chat as an agent: a name, its task as its brief, and runs of its own, each a fresh chat in a worktree that lasts">
          <Bot size={13} /><span className="btn-label"> Keep as an agent</span>
        </button>
      )}
      {rec.repo && (
        <button className={`btn btn-sm ${!shippable && finishReady ? "btn-primary" : ""}`} disabled={!!busy}
          aria-label={rec.finished ? "Finish again" : "Finish"}
          onClick={() => run("finish", async () => {
            const r = finishWords(await Api.finish(rec.id), rec.branch);
            // where it is not shipped, the person brings the branch in where they work: the command, said
            // where it is not shipped, the way on from here: Merge (a repository of this machine's), a push of its
            // own (a remote elsewhere than GitHub), or git's own merge
            say(shippable || !rec.branch ? r.text
              : mergeable ? `${r.text} Then merge it into ${into}, with Merge.`
              : repo?.origin ? `${r.text} To share it, push it from a shell in the session: git push origin ${rec.branch}`
              : `${r.text} To bring it into your branch: git merge ${rec.branch}`,
              r.tone, shippable ? 6000 : 15000);
          })}
          title={shippable ? "Commit the worktree and have this deployment verify and sign the change"
            : "Commit the worktree and have this deployment verify and sign the change. Its repository has no remote on GitHub, so its branch is merged where you work, by you"}>
          {busy === "finish" ? <Loader2 size={13} className="animate-spin" /> : <CheckCheck size={13} />}{" "}
          {/* the screen's main action keeps its word on a narrow screen too */}
          <span className={shippable ? "btn-label" : ""}>{rec.finished ? "Finish again" : "Finish"}</span>
        </button>
      )}
      {rec.repo && mergeable && rec.finished && (
        <button className={`btn btn-sm ${mergeReady ? "btn-primary" : ""}`} disabled={!!busy || merged || working}
          title={merged ? `Merged into ${into}` : working ? "Its agent is at work: merge once it stops, and what the last Finish verified is what goes"
            : `Merge what the last Finish committed and verified into ${into}, as a fast-forward: no merge commit, and only once ${into} has nothing the session does not`}
          onClick={() => run("land", async () => {
            const r = await Api.land(rec.id);
            say(r.commits ? `Merged ${rec.branch} into ${r.into}: ${r.commits} ${r.commits === 1 ? "commit" : "commits"}, as a fast-forward.` : `${r.into} has it already.`);
          })}>
          {busy === "land" ? <Loader2 size={13} className="animate-spin" /> : <GitMerge size={13} />} {merged ? `Merged into ${into}` : `Merge into ${into}`}
        </button>
      )}
      {rec.repo && shippable && (
        <button className={`btn btn-sm ${shipReady ? "btn-primary" : ""}`} disabled={!!busy} onClick={onShip} title={grouped ? "Ship this one: commit, verify, push and open its pull request; then the others of its task can go" : "Commit, verify, push and open a pull request"}>
          <GitPullRequest size={13} /> {grouped ? <>Ship<span className="btn-label"> this one</span></> : rec.shipped ? <>Ship<span className="btn-label"> again</span></> : "Ship"}
        </button>
      )}
      {onAgain && (
        <button className="icon-btn" onClick={onAgain} title="Try this task with another agent too, from the same commit, to compare">
          <CopyPlus size={14} />
        </button>
      )}
      {onContinue && (
        <button className="icon-btn" disabled={!!busy} onClick={onContinue} aria-label="Continue in a new session"
          title="Continue in a new session: Finish this one, then start a new session from its commit, on Claude Code or Codex and any of your accounts, with your task and what changed, never the conversation">
          <ArrowRightLeft size={14} />
        </button>
      )}
      {!isChat && (rec.running ? (
        <button className="icon-btn" disabled={!!busy} onClick={() => run("stop", () => Api.stopSession(rec.id))} title="Stop the program"><Square size={14} /></button>
      ) : (
        <button className="icon-btn" disabled={!!busy} onClick={() => run("start", () => startAgain(rec.id, say))}
          title="Start the program again"><Play size={14} /></button>
      ))}
      {/* Close: stopped and taken out of the list, nothing of it removed. One closed, open here all the
          same (search opens it back in the list; this is for one opened otherwise), is put back with a click */}
      {rec.closed ? (
        <button className="icon-btn" disabled={!!busy} onClick={() => run("open", () => reopenSession(rec.id))}
          title="It is closed, so not in the list: put it back" aria-label="Put the session back in the list"><Eye size={14} /></button>
      ) : (
        <button className="icon-btn" disabled={!!busy} onClick={() => { void closeSession(rec.id); focusList(); }} aria-label="Close the session"
          title="Close: stop it and take it out of the list. Nothing of it is removed: search (⌘K) finds it, and opening it there puts it back">
          <EyeOff size={14} />
        </button>
      )}
      <button
        className={removing || busy === "remove" ? "btn btn-sm btn-ghost !text-fail" : "icon-btn"}
        disabled={!!busy}
        title={removing ? "Click again to remove it and its worktree (its branch stays in the repository if it holds commits)"
          : "Remove the session and its worktree (a branch with commits stays)"}
        aria-label={removing ? "Click again to remove the session" : "Remove the session"}
        onClick={async () => {
          if (!removing) { setRemoving(true); setTimeout(() => setRemoving(false), 6000); return; }
          setRemoving(false);
          setBusy("remove");
          try {
            const r = await Api.removeSession(rec.id);
            await refresh();
            // what became of its branch, said; and the focus to the list once it shows without the session, never
            // lost with the session's screen
            say(r.branch_kept ? `Removed. Its branch ${r.branch} stays in ${rec.repo}.`
              : r.branch ? `Removed, with its branch ${r.branch}, which held nothing of its own.` : "Removed.");
            focusList();
          } catch (e) { setError(e); } finally { setBusy(""); }
        }}
      >
        {busy === "remove" ? <><Loader2 size={14} className="animate-spin" /> Removing…</> : <><Trash2 size={14} />{removing && " Click again to remove"}</>}
      </button>
    </>
  );
}

/** A session's program started again, and what the start says of an effort it did not give said to the person:
 *  a Claude Code session an agent asked for starts at its default when the installed Claude Code
 *  no longer offers the effort it was started with, and the workspace says so in the start's answer. */
async function startAgain(id: string, say: (text: string, tone?: "done" | "wait" | "fail", ms?: number) => void) {
  const started = await Api.startSession(id, 120, 32);
  if (started.effort_notice) say(started.effort_notice, "wait", 15000);
}

/** A terminal session whose program has ended: what it printed last, kept by the workspace until it restarts, and
 *  a way to start it again. */
export function Stopped({ rec }: { rec: SessionRecord }) {
  const refresh = useStore((s) => s.refresh);
  const setError = useStore((s) => s.setError);
  const say = useStore((s) => s.say);
  const link = useMemo(() => terminalLink(rec.id), [rec.id]);
  const [busy, setBusy] = useState(false);
  const start = async () => {
    setBusy(true);
    try { await startAgain(rec.id, say); await refresh(); } catch (e) { setError(e); } finally { setBusy(false); }
  };
  // the workspace keeps a program's last output until it starts again, but not across its own restart
  const ended = rec.ended_at != null;
  return (
    <div className="flex h-full flex-col">
      {ended ? (
        <div className="min-h-0 flex-1 opacity-75"><TerminalPane link={link} readOnly /></div>
      ) : (
        <Note text="Not run since the workspace last started, so there is no output to show." />
      )}
      <div className="flex flex-none items-center gap-3 border-t border-line bg-panel px-4 py-2">
        <span className="text-[12.5px] text-fg-dim">
          {endedHow(rec.exit_code, "The program")}
          {ended && <>{" "}<span className="ml-1 text-faint">Its last output, above.</span></>}
        </span>
        <button className="btn btn-sm btn-outline ml-auto" disabled={busy} onClick={start}>
          {busy ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} Start it again
        </button>
      </div>
    </div>
  );
}

function Note({ text }: { text: string }) {
  return <div className="flex h-full items-center justify-center px-6 text-center text-[12.5px] text-muted">{text}</div>;
}

/** Nothing open: the steps from a new workspace to a first session, each ticked off as it is done, and what to do
 *  next. Claude's and GitHub's sign-ins are optional: Codex, Gemini CLI and OpenCode sign in their own way, and a
 *  public repository clones without GitHub. */
export function Empty(props: {
  hasRepos: boolean;
  hasSessions: boolean;
  claude: boolean | null;
  /** Whether Claude Code is installed where the workspace runs; null until it says. */
  claudeInstalled?: boolean | null;
  /** How many of the terminal agents are installed there; null until it says. */
  agents?: number | null;
  github: boolean | null;
  onNew: () => void;
  onClone: () => void;
  onConnections: () => void;
  /** The workspace is on the person's own machine (the local provider): a repository may be added from a folder. */
  local?: boolean;
  /** Whether a Mac of the owner's is linked (null until the linked sessions are read), and linking one. */
  macLinked?: boolean | null;
  onLink?: () => void;
}) {
  // `aside`: on no path to a first session (GitHub, for private repositories and pull requests; linking a Mac, whose
  // sessions are listed beside the workspace's): offered, never the next step the screen fills in
  type Step = { done: boolean | null; title: string; body: string; action: string | null; run: () => void; optional?: boolean; aside?: boolean };
  // a workspace on the person's own machine runs the agents they installed; it may have none yet, or not Claude Code
  const agentSteps: Step[] = props.agents === 0 ? [
    { done: false, title: "Install a coding agent", body: "None is installed where this workspace runs. Claude Code: npm install -g @anthropic-ai/claude-code; Codex: npm install -g @openai/codex; the others install their own way. Then reload this page.", action: "Reload", run: () => location.reload() },
  ] : props.claudeInstalled === false ? [
    { done: null, title: "Claude Code is not installed", body: "For Claude Code sessions and chats: npm install -g @anthropic-ai/claude-code, then reload. The other agents work without it.", action: null, run: () => {}, optional: true },
  ] : [
    { done: props.claude, title: "Sign in to Claude", body: "For Claude Code and chats, with your Claude account. Other agents sign in their own way.", action: "Sign in", run: props.onConnections, optional: true },
  ];
  const steps: Step[] = [
    ...agentSteps,
    { done: props.github, title: "Connect GitHub", body: "To clone your private repositories, open pull requests, and start from issues.", action: "Connect", run: props.onConnections, optional: true, aside: true },
    props.local
      ? { done: props.hasRepos, title: "Add a repository", body: "From a folder on this Mac, used where it is, or cloned from GitHub. Each session works in its own worktree of it, on its own branch.", action: "Add", run: props.onClone }
      : { done: props.hasRepos, title: "Clone a repository", body: "Each session works in its own worktree of it, on its own branch.", action: "Clone", run: props.onClone },
    { done: props.hasSessions, title: "Start a session", body: "Give an agent a task; start several on the same one to compare and keep the best.", action: "New session", run: props.onNew },
    ...(props.onLink ? [{ done: props.macLinked ?? null, title: "Link your Mac", body: "Its Claude Code sessions are listed here beside the workspace's, to read and steer from this page.",
      action: "Link", run: props.onLink, optional: true, aside: true }] : []),
  ];
  const next = steps.findIndex((x) => !x.done && !x.aside && !(x.optional && x.done === null));
  if (props.hasRepos && props.hasSessions) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 overflow-y-auto px-6 py-8 text-center">
        <Logo size={34} />
        <div>
          <div className="text-[15px] font-semibold">Pick a session, or start one</div>
          <div className="mt-1 max-w-[420px] text-[12.5px] text-muted">⌥1–9 opens a session from the list; ⌘K finds one, a file in it, or a command.</div>
        </div>
        <button className="btn btn-primary" onClick={props.onNew}><Plus size={14} /> New session</button>
        {props.macLinked === false && props.onLink && <LinkMac onLink={props.onLink} />}
      </div>
    );
  }
  return (
    <div className="flex h-full items-center justify-center overflow-y-auto px-6 py-8">
      <div className="w-full max-w-[520px]">
        <div className="mb-5 flex items-center gap-3">
          <Logo size={30} />
          <div>
            <div className="text-[15px] font-semibold">Get started</div>
            <div className="text-[12.5px] text-muted">Your workspace is running. A few steps to a first session.</div>
          </div>
        </div>
        <ol className="space-y-2">
          {steps.map((x, i) => (
            <li key={x.title} className={`flex items-center gap-3 rounded-lg border px-3.5 py-3 ${i === next ? "border-accent/50 bg-accent/[0.05]" : "border-line"}`}>
              <span className={`flex h-6 w-6 flex-none items-center justify-center rounded-full text-[11.5px] font-semibold ${x.done ? "bg-done/15 text-done" : i === next ? "bg-primary text-primary-ink" : "bg-active text-muted"}`}>
                {x.done ? <CheckCheck size={13} /> : i + 1}
              </span>
              <div className="min-w-0 flex-1">
                <div className={`text-[13px] font-medium ${x.done ? "text-muted line-through decoration-faint" : "text-fg"}`}>
                  {x.done && <span className="sr-only">Done: </span>}
                  {x.title}{x.optional && !x.done && <>{" "}<span className="ml-1 text-[11px] font-normal text-faint">optional</span></>}
                </div>
                <div className="text-[12px] text-muted">{x.body}</div>
              </div>
              {!x.done && x.action && <button className={`btn btn-sm flex-none ${i === next ? "btn-primary" : ""}`} onClick={x.run}>{x.action}</button>}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

export { workspaceWords };

/** The workspace given to the account, not running: a state of the main panel, beside the list, which still shows each
 *  linked machine's sessions. */
function WorkspaceOff({ ws, onStart, macLinked, onLink }: { ws: WorkspaceState; onStart: () => void; macLinked: boolean | null; onLink: () => void }) {
  const starting = !!ws.starting;
  const said = workspaceWords(ws);
  const error = ws.start_error || ws.error;
  // a start needs the server: while it cannot be reached, the line under the title bar says so
  const offline = useOffline();
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 overflow-y-auto px-6 py-8 text-center">
      <Logo size={34} />
      <div>
        <h2 className="text-[15px] font-semibold">{said.title}</h2>
        <p className="mx-auto mt-1 max-w-[440px] text-[12.5px] text-muted">{said.body}</p>
        {error && !starting && <p role="alert" className="mx-auto mt-2 max-w-[440px] text-[12.5px] text-fail">{error}</p>}
      </div>
      <button className="btn btn-primary touch-target" disabled={starting || offline} onClick={onStart}>
        {starting ? <Loader2 size={14} className="animate-spin" /> : <Power size={14} />} {starting ? "Starting" : "Start workspace"}
      </button>
      {macLinked === false && <LinkMac onLink={onLink} />}
    </div>
  );
}

/** A deployment that gives this account no workspace: the linked sessions, which need none, are still here. */
function NoWorkspace({ detail, macLinked, onLink }: { detail?: string; macLinked: boolean | null; onLink: () => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 overflow-y-auto px-6 py-8 text-center">
      <h2 className="text-[15px] font-semibold">No workspace on this deployment</h2>
      <p className="max-w-[460px] text-[12.5px] text-muted">
        {detail || "This deployment does not offer a workspace to this account."} Linked sessions work without one.
      </p>
      {macLinked === false && <LinkMac onLink={onLink} />}
    </div>
  );
}

/** Linking a Mac, offered where none is linked yet: its Claude Code sessions are then listed beside the workspace's,
 *  whether or not the workspace runs, and are read and steered from this page. */
function LinkMac({ onLink }: { onLink: () => void }) {
  // the button beside the words on a wide screen, under them on a phone's
  return (
    <div className="mt-2 flex w-full max-w-[440px] flex-col gap-3 rounded-lg border border-line px-3.5 py-3 text-left sm:flex-row sm:items-center">
      <div className="flex min-w-0 flex-1 gap-3">
        <Laptop size={18} className="mt-0.5 flex-none text-fg-dim" aria-hidden />
        <div className="min-w-0">
          <div className="text-[13px] font-medium">Your Mac's sessions, here too</div>
          <div className="text-[12px] text-muted">Linked, your Mac's Claude Code sessions are listed beside the workspace's, whether or not it runs, to read and steer from this page.</div>
        </div>
      </div>
      <button className="btn btn-sm touch-target flex-none justify-center self-stretch sm:self-auto" onClick={onLink}>Link your Mac</button>
    </div>
  );
}

/** "Offline — last read 03:21, reconnecting…": the page's last read of its server, in the device's time. */
export function offlineWords(lastRead: number | null): string {
  return `Offline — ${lastRead ? `last read ${clock(new Date(lastRead))}, ` : ""}reconnecting…`;
}

/** The page could not reach its server at its last try: one steady line under the title bar, saying when it last read
 *  it, until a read gets through. A screen reader says it once: the live region is on the page all along, empty while
 *  the page is online, and its words do not change while the page is offline. */
export function Offline() {
  const offline = useOffline();
  const lastRead = useStore((s) => s.lastRead);
  return (
    <div role="status" className={offline ? "flex min-h-9 flex-none items-center gap-2.5 border-b border-wait/30 bg-wait/[0.07] px-4 py-1.5 text-[12.5px] text-fg" : "sr-only"}>
      {offline && <><WifiOff size={14} className="flex-none text-wait" aria-hidden />{offlineWords(lastRead)}</>}
    </div>
  );
}

function Splash() {
  return (
    <div className="flex h-full flex-1 items-center justify-center">
      <Loader2 size={20} className="animate-spin text-muted" />
    </div>
  );
}
