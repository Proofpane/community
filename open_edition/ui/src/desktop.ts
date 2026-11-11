// The desktop app's start (the manual, "The desktop app"): its shell starts this Mac's server and,
// once it listens, answers the page's `local_server` with where and with what sign-in (proofpane_open/desktop.py, its
// first line). The page keeps them as this Mac's place (places.ts), whatever place the window shows; the sign-in only
// in its memory. A hosted place's sign-in is kept in the system's keychain by the shell (token_get, token_set).
import { serverAddress } from "./api";
import { activeId, keepTokensWith, LOCAL, LOCAL_LABEL, OLD_TOKEN_PREFIX, place, places, savePlace, setPlaceToken } from "./places";

export interface LocalServer { server: string; token: string }

export type Invoke = <T>(cmd: string, args?: Record<string, unknown>) => Promise<T>;

export const tauriInvoke: Invoke = async <T,>(cmd: string, args?: Record<string, unknown>) =>
  (await import("@tauri-apps/api/core")).invoke<T>(cmd, args);

/** This Mac's server, as the shell says it is: an http address on this machine's loopback, and a sign-in. */
export function checkLocal(said: unknown): LocalServer {
  const s = said as Partial<LocalServer> | null;
  const origin = s && typeof s.server === "string" ? serverAddress(s.server) : null;
  if (!origin || !origin.startsWith("http://127.0.0.1:")) throw new Error("The app named a server that is not this Mac's.");
  if (!s || typeof s.token !== "string" || !/^[A-Za-z0-9_-]{1,2000}\.[A-Za-z0-9_-]{1,200}$/.test(s.token)) {
    throw new Error("The app gave no sign-in for this Mac's workspace.");
  }
  return { server: origin, token: s.token };
}

function message(e: unknown): string {
  return typeof e === "string" ? e : e instanceof Error ? e.message : String(e);
}

/** Hosted places' sign-ins are kept by the shell from now on; any a build before the keychain left in the web view's
 *  storage are moved there and removed, and this Mac's (made again at each start) is removed. Then the sign-in of the
 *  hosted place the window shows, if it shows one, is read back into this page's memory. */
export async function loadHosted(invoke: Invoke = tauriInvoke): Promise<void> {
  keepTokensWith(async (server, token) => { await invoke("token_set", { server, token }); });
  try {
    for (const key of Object.keys(localStorage)) {
      if (!key.startsWith(OLD_TOKEN_PREFIX)) continue;
      const id = key.slice(OLD_TOKEN_PREFIX.length);
      const p = places().find((q) => q.id === id && q.id !== LOCAL);
      const token = localStorage.getItem(key);
      if (p && token) await invoke("token_set", { server: p.server, token }).catch(() => {});
      localStorage.removeItem(key);
    }
  } catch { /* storage refused: nothing to move */ }
  const id = activeId();
  const p = id === LOCAL ? null : place(id);
  if (p) {
    const token = await invoke<string | null>("token_get", { server: p.server }).catch(() => null);
    if (token) setPlaceToken(id, token, false);
  }
}

/** Wait for this Mac's server and keep it as this Mac's place. The shell answers once the server listens, or with why
 *  it did not start. */
export async function startDesktop(invoke: Invoke = tauriInvoke): Promise<LocalServer> {
  let said: unknown;
  try {
    said = await invoke<unknown>("local_server");
  } catch (e) {
    throw new Error(message(e));
  }
  const local = checkLocal(said);
  const had = place(LOCAL);
  savePlace({ id: LOCAL, server: local.server, label: LOCAL_LABEL, ...(had?.email ? { email: had.email } : {}) });
  setPlaceToken(LOCAL, local.token, false);
  return local;
}
