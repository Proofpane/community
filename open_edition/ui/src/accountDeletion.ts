import { api } from "./api";

/** The deletion of the account this browser placed (account_delete.py): the id of the hold it waits on, and the
 *  account's id, kept in this browser alone, so that the next time the page opens it can say the deletion is done, from
 *  the server's record of that id (GET /api/account/deletion/<id>, which takes no sign-in and holds the time alone),
 *  since the phone app shows no notifications. The account's id says whose it is, so signing in as another account
 *  forgets nothing of it. */
const KEY = "proofpane.deletion";
const HOLD = /^[0-9a-f]{32}$/;

export interface Kept { hold: string; account: string }

export function keepDeletion(hold: string, account: string) {
  try { localStorage.setItem(KEY, JSON.stringify({ hold, account })); } catch { /* this page only: nothing is said later */ }
}

export function keptDeletion(): Kept | null {
  try {
    const kept = JSON.parse(localStorage.getItem(KEY) ?? "null") as Partial<Kept> | null;
    return kept && typeof kept.hold === "string" && HOLD.test(kept.hold) && typeof kept.account === "string"
      ? { hold: kept.hold, account: kept.account } : null;
  } catch {
    return null;
  }
}

export function forgetDeletion(account?: string) {
  try {
    if (account === undefined || keptDeletion()?.account === account) localStorage.removeItem(KEY);
  } catch { /* nothing kept */ }
}

/** When the deletion this browser placed took effect, by the server's record of its hold's id, the id then forgotten;
 *  or null: none kept, none done yet (the id kept to ask again), or the server not reached. */
export async function deletionDone(): Promise<number | null> {
  const kept = keptDeletion();
  if (!kept) return null;
  try {
    const r = await api<{ deleted_at?: unknown }>(`/api/account/deletion/${kept.hold}`);
    if (typeof r?.deleted_at !== "number") return null;
    forgetDeletion();
    return r.deleted_at;
  } catch {
    return null;
  }
}
