// The places the desktop app shows (the manual, "The desktop app"): the workspace on this
// Mac, whose server the app starts and signs in to by itself, and hosted Community workspaces the person adds and
// signs in to. One place at a time fills the window; each keeps its own server address and its own sign-in. The list
// is in the web view's storage; the sign-ins are not, since any program of the same user (a coding agent in the
// workspace on this Mac among them) can read that storage: they are held in this page's memory, and a hosted one is
// kept in the system's keychain by the app's shell, by its server's address (keep, below), so changing a place's
// address in the storage cannot send its sign-in to another server. The browser and the phone app have one server
// each and no places.

/** A place: `server` is an origin (http on this machine's loopback for this Mac, https for a hosted one); `email` is
 *  the account it is signed in as, once it is. */
export interface Place { id: string; server: string; label: string; email?: string }

export const LOCAL = "local";
export const LOCAL_LABEL = "This Mac";

const LIST_KEY = "pp-open-places";
const ACTIVE_KEY = "pp-open-place";
/** Where a sign-in was kept before the keychain: read once, moved, and removed (desktop.ts). */
export const OLD_TOKEN_PREFIX = "pp-open-token@";

const held = new Map<string, string>();       // place id -> its sign-in, in this page's memory only
let keep: ((server: string, token: string | null) => Promise<void>) | null = null;

/** Where a hosted place's sign-in is kept between runs (the desktop app's keychain, through its shell). */
export function keepTokensWith(fn: (server: string, token: string | null) => Promise<void>) {
  keep = fn;
}

function read(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch { /* storage refused: the place lasts as long as the page */ }
}

function isPlace(p: unknown): p is Place {
  if (!p || typeof p !== "object") return false;
  const o = p as Record<string, unknown>;
  return typeof o.id === "string" && !!o.id && typeof o.server === "string" && typeof o.label === "string"
    && (o.email === undefined || typeof o.email === "string");
}

/** Every place, this Mac's first. */
export function places(): Place[] {
  let list: unknown;
  try { list = JSON.parse(read(LIST_KEY) || "[]"); } catch { list = []; }
  const all = Array.isArray(list) ? list.filter(isPlace) : [];
  return [...all.filter((p) => p.id === LOCAL), ...all.filter((p) => p.id !== LOCAL)];
}

export function place(id: string): Place | null {
  return places().find((p) => p.id === id) ?? null;
}

/** Add a place, or replace the one with its id. */
export function savePlace(p: Place) {
  const list = places().filter((q) => q.id !== p.id);
  list.push({ id: p.id, server: p.server, label: p.label, ...(p.email ? { email: p.email } : {}) });
  write(LIST_KEY, JSON.stringify(list));
}

/** Forget a place and its sign-in (this Mac's cannot be forgotten). The window goes back to this Mac if it showed it. */
export function removePlace(id: string) {
  if (id === LOCAL) return;
  const p = place(id);
  write(LIST_KEY, JSON.stringify(places().filter((q) => q.id !== id)));
  held.delete(id);
  if (p && keep) void keep(p.server, null).catch(() => {});
  if (activeId() === id) write(ACTIVE_KEY, LOCAL);
}

/** The place the window shows: this Mac's unless another was chosen and is still there. */
export function activeId(): string {
  const id = read(ACTIVE_KEY);
  return id && place(id) ? id : LOCAL;
}

export function setActive(id: string) {
  write(ACTIVE_KEY, id);
}

export function placeToken(id: string): string | null {
  return held.get(id) ?? null;
}

/** A place's sign-in, held for this page, and for a hosted place kept for the next run (or forgotten, with none). */
export function setPlaceToken(id: string, token: string | null, persist = true) {
  if (token) held.set(id, token);
  else held.delete(id);
  const p = place(id);
  if (persist && p && id !== LOCAL && keep) void keep(p.server, token).catch(() => {});
}

/** A hosted place's name in the title bar: its host, as the address bar would show it. */
export function hostLabel(origin: string): string {
  try { return new URL(origin).host; } catch { return origin; }
}

/** A new hosted place, not signed in yet, at the address given: its id is new; a place already at that address is
 *  the one returned instead. */
export function addHosted(origin: string, newId: () => string = randomId): Place {
  const had = places().find((p) => p.id !== LOCAL && p.server === origin);
  if (had) return had;
  const p: Place = { id: newId(), server: origin, label: hostLabel(origin) };
  savePlace(p);
  return p;
}

/** A hosted place moved to another address while it is not signed in (the person changed it on the sign-in screen).
 *  If another place is at that address already, this one is dropped and that one returned. */
export function moveHosted(id: string, origin: string): Place | null {
  const p = place(id);
  if (!p || id === LOCAL) return p;
  const other = places().find((q) => q.id !== id && q.id !== LOCAL && q.server === origin);
  if (other) {
    removePlace(id);
    return other;
  }
  const moved = { ...p, server: origin, label: hostLabel(origin) };
  savePlace(moved);
  return moved;
}

/** Hosted places nobody signed in to (one added and left on its sign-in screen; one signed out of), dropped when the
 *  window leaves them. Signed in is what the place says (the account it was signed in as), or a sign-in held now. */
export function dropUnsigned(except: string) {
  for (const p of places()) {
    if (p.id !== LOCAL && p.id !== except && !p.email && !placeToken(p.id)) removePlace(p.id);
  }
}

/** A place signed out of: its sign-in and the account it named are forgotten, so leaving it drops it. */
export function signedOut(id: string) {
  const p = place(id);
  setPlaceToken(id, null);
  if (p && p.email) savePlace({ id: p.id, server: p.server, label: p.label });
}

function randomId(): string {
  const bytes = new Uint8Array(9);
  crypto.getRandomValues(bytes);
  return "h" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
