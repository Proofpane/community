import { useEffect } from "react";
import { api } from "./api";
import { useStore, workspaceRuns } from "./store";

// The page's shell keeps its title bar, its list of sessions and the tab's title current by reading the server's
// summary (store.readSummary), which reads a list again only when its stamp moved: an idle page asks 14 times a minute
// where it asked 89 with the workspace running, and 13 where it asked 51 with it stopped (the
// manual says how). A server from before the summary is read list by list, as before.

/** How often the shell reads the summary while the page is shown (milliseconds). */
export const EVERY = 5000;
/** ...while the workspace starts, which the person watches: it runs within seconds. */
export const STARTING_EVERY = 2000;
/** How often, at most, the page reads its sessions and what waits for the person while it is hidden (milliseconds). */
export const HIDDEN_EVERY = 15000;
/** The longest wait between two tries while the server cannot be reached. */
export const OFFLINE_MOST = 30000;

/** How long the shell waits before its next read: offline, twice as long after each try that failed, from EVERY up to
 *  OFFLINE_MOST; hidden, HIDDEN_EVERY; while the workspace starts, STARTING_EVERY; else EVERY. */
export function pollDelay(s: { offline: boolean; failures: number; hidden: boolean; starting: boolean }): number {
  if (s.offline) return Math.min(EVERY * 2 ** Math.max(0, s.failures - 1), OFFLINE_MOST);
  if (s.hidden) return HIDDEN_EVERY;
  return s.starting ? STARTING_EVERY : EVERY;
}

/** A check for a poll: true each time while the page is visible, and while it is hidden at most once each `every`
 *  milliseconds. */
export function whileHidden(every: number, now: () => number = Date.now, visible: () => boolean = () => document.visibilityState === "visible") {
  let last = 0;
  return () => {
    if (visible()) return true;
    if (now() - last < every) return false;
    last = now();
    return true;
  };
}

/** The shell's reads while signed in: the summary at pollDelay's pace, and at once when the window gains the focus,
 *  comes back online or is shown again; from a server without the summary, each list on its own, as before. */
export function useShellPoll(signedIn: boolean) {
  const off = useStore((s) => s.summaryOff);
  const running = useStore((s) => workspaceRuns(s.ws));
  useEffect(() => {
    if (!signedIn || off) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    let busy = false;                         // a read under way: a second asked meanwhile is that one
    const tick = async () => {
      if (busy) return;
      busy = true;
      clearTimeout(timer);
      try {
        await useStore.getState().readSummary();
      } finally {
        busy = false;
      }
      if (!alive) return;
      const st = useStore.getState();
      if (st.summaryOff) return;              // the reads below take over
      failures = st.offline ? failures + 1 : 0;
      timer = setTimeout(tick, pollDelay({ offline: st.offline, failures, hidden: document.visibilityState === "hidden",
        starting: !!st.ws?.starting }));
    };
    const now = () => { failures = 0; void tick(); };
    const shown = () => { if (document.visibilityState === "visible") now(); };
    void tick();
    window.addEventListener("focus", now);
    window.addEventListener("online", now);
    document.addEventListener("visibilitychange", shown);
    return () => {
      alive = false;
      clearTimeout(timer);
      window.removeEventListener("focus", now);
      window.removeEventListener("online", now);
      document.removeEventListener("visibilitychange", shown);
    };
  }, [signedIn, off]);

  // a server from before the summary: each list read on its own, as the page did before it
  useEffect(() => {
    if (!signedIn || !off) return;
    const store = useStore.getState;
    const sessionsDue = whileHidden(HIDDEN_EVERY);
    const approvalsDue = whileHidden(HIDDEN_EVERY);
    const tick = () => sessionsDue() && void store().refresh();
    const ap = () => approvalsDue() && void store().refreshApprovals();
    const agents = () => api<{ waiting: unknown[] }>("/api/ade/agents")
      .then((t) => useStore.setState({ agentsWaiting: t.waiting.length })).catch(() => {});
    const ln = () => {
      if (document.visibilityState !== "visible") return;
      void store().refreshLinks();
      void store().refreshHistory();
      const ws = store().ws;
      if (ws?.enabled && ws.allowed !== false) void agents();
    };
    void store().refreshApprovals();
    ln();
    const a = setInterval(tick, running ? 4000 : 3000);
    const b = setInterval(ap, 5000);
    const d = setInterval(ln, 30000);
    window.addEventListener("focus", tick);
    return () => { clearInterval(a); clearInterval(b); clearInterval(d); window.removeEventListener("focus", tick); };
  }, [signedIn, off, running]);
}
