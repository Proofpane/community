// What the person has seen of each session, on this device: a session that did something since (a turn ended, it
// asked something, its program exited) is unread until it is open on the page again, and one can be marked unread,
// to come back to, until it is next chosen. Kept in this browser only; the workspace knows nothing of it.
import { create } from "zustand";

const KEY = "pp-seen";
const MARK = "unread";
type Seen = Record<string, string>;     // session -> the last activity seen (an ISO time), or MARK

function load(): Seen {
  try {
    const got = JSON.parse(localStorage.getItem(KEY) || "{}");
    return got && typeof got === "object" && !Array.isArray(got) ? got : {};
  } catch { return {}; }
}
function save(seen: Seen) {
  try { localStorage.setItem(KEY, JSON.stringify(seen)); } catch { /* this device keeps nothing */ }
}

/** Whether a session has done something the person has not seen: it is not open, and its last activity is later than
 *  what they saw of it, or they marked it unread. A session never seen before is not unread: it is taken as seen as
 *  it is (`useSeen.observe`). */
export function isUnread(seen: Seen, id: string, updatedAt: string | undefined, open: boolean): boolean {
  const was = seen[id];
  if (was === MARK) return true;
  if (open || was === undefined || !updatedAt) return false;
  return updatedAt > was;
}

export const useSeen = create<{
  seen: Seen;
  /** Sessions open on the page, and every session not seen before: seen as they are now. A mark stays. */
  observe(now: { id: string; updatedAt?: string; open: boolean }[]): void;
  markUnread(id: string): void;
  /** Chosen: a mark is cleared, and what it has done until now is seen. */
  chosen(id: string, updatedAt?: string): void;
}>((set, get) => ({
  seen: load(),
  observe(now) {
    const seen = get().seen;
    const next: Seen = {};
    let changed = false;
    for (const s of now) {
      const was = seen[s.id];
      const at = s.updatedAt || "";
      const value = was === MARK ? MARK : s.open || was === undefined ? (was && was > at ? was : at) : was;
      next[s.id] = value;
      if (value !== was) changed = true;
    }
    if (Object.keys(seen).length !== now.length) changed = true;     // a removed session is forgotten
    if (changed) { set({ seen: next }); save(next); }
  },
  markUnread(id) {
    const next = { ...get().seen, [id]: MARK };
    set({ seen: next });
    save(next);
  },
  chosen(id, updatedAt) {
    const seen = get().seen;
    if (seen[id] !== MARK) return;
    const next = { ...seen, [id]: updatedAt || new Date().toISOString() };
    set({ seen: next });
    save(next);
  },
}));
