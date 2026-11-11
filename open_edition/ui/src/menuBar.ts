// The desktop app's own menu-bar icon (desktop/src-tauri/src/menu_bar.rs): the page says what needs the person in the
// place the window shows (an agent waiting for them, approvals asked), the menu lists it and counts it beside the icon,
// and choosing one there opens it here. Names only, as a notification says them: never a command.
import { useEffect, useRef } from "react";
import { DESKTOP, linkWaiting, type LinkSessions } from "./api";
import { tauriInvoke, type Invoke } from "./desktop";
import { head } from "./text";
import { useStore } from "./store";
import type { Session } from "./types";

export const MENU_BAR_MOST = 10;     // sessions listed in the menu; the count beside the icon is of all of them

export interface MenuBarSummary {
  place: string;
  waiting: { session: string; title: string; note: string }[];
  approvals: number;
  count: number;
}

/** Nothing for the person: what the menu bar shows when no one is signed in, so a session's name never stays there. */
export const MENU_BAR_EMPTY: MenuBarSummary = { place: "", waiting: [], approvals: 0, count: 0 };

/** What the menu bar shows of the place the window shows: the sessions that wait for the person, newest first, at most
 *  MENU_BAR_MOST, each with what it waits for by name; and how many approvals are asked, the linked sessions' open
 *  prompts, proposals and questions to the owner among them (`links`), which Approvals holds. The app holds no keys,
 *  so they are counted here and read and answered in the browser tab Approvals opens. */
export function menuBarSummary(place: string, sessions: Session[], approvals: number, links?: LinkSessions | null): MenuBarSummary {
  const waiting = sessions.filter((s) => s.state === "waiting")
    .sort((a, b) => (b.updatedAt || "").localeCompare(a.updatedAt || ""));
  const asked = approvals + linkWaiting(links);
  return {
    place: head(place, 60),
    waiting: waiting.slice(0, MENU_BAR_MOST).map((s) => ({ session: s.id, title: head(s.title, 80), note: head(s.alert || "", 80) })),
    approvals: asked,
    count: waiting.length + asked,
  };
}

export type MenuBarOpen = { session?: string; approvals?: boolean };

/** In the desktop app, keep the menu bar's menu as `summary` says (emptied when it is null: signed out), and open what
 *  is chosen there with `onOpen`. Elsewhere, nothing. */
export function useMenuBar(summary: MenuBarSummary | null, onOpen: (what: MenuBarOpen) => void, invoke: Invoke = tauriInvoke, on = DESKTOP) {
  const said = useRef("");
  const open = useRef(onOpen);
  open.current = onOpen;
  const shown = summary ?? MENU_BAR_EMPTY;
  const key = JSON.stringify(shown);
  useEffect(() => {
    if (!on || key === said.current) return;
    said.current = key;
    invoke("menu_bar", { summary: shown }).catch(() => { said.current = ""; });       // an older shell has no menu bar
  }, [on, key]);       // eslint-disable-line react-hooks/exhaustive-deps
  // what the desktop shell runs while the window is closed to the menu bar (menu_bar.rs, keep_reading), so the page
  // reads again whatever WebKit does with a hidden page's timers
  useEffect(() => {
    if (!on) return;
    const w = window as unknown as { __ppMenuBarTick?: () => void };
    w.__ppMenuBarTick = () => {
      const st = useStore.getState();
      void st.refresh(); void st.refreshApprovals(); void st.refreshLinks();
    };
    return () => { delete w.__ppMenuBarTick; };
  }, [on]);
  useEffect(() => {
    if (!on) return;
    let gone = false;
    let off: (() => void) | undefined;
    void import("@tauri-apps/api/event").then(({ listen }) =>
      listen<MenuBarOpen>("menu-bar-open", (e) => open.current(e.payload || {})).then((u) => { if (gone) u(); else off = u; }));
    return () => { gone = true; off?.(); };
  }, [on]);
}
