// A signing window: one touch of the owner's passkey signs a delegation to a key this tab makes, for one linked session
// and 5 or 15 minutes; each instruction the owner sends that session in that time is signed here with that key, and no
// passkey is asked. The session verifies both signatures itself, and holds the limits itself: the server only relays.
//
// The key is made with `extractable` false, so its private half never reaches script; it is kept in this module's
// memory alone (never in IndexedDB, localStorage, sessionStorage or a request), one per link, and dropped at the
// window's end by this page's own clock, at End now, at this page's sign-out, on pagehide, and when the server says the
// window ended or the link is gone. A dropped key cannot be wiped by script, but once no reference is left nothing here
// can sign with it.
import { api } from "./api";
import { canonicalJson, signPrepared, stepChallenge, type Prepared } from "./passkey";

/** The two lengths a window has, in seconds, as the server holds them (link_commands.DELEGATION_LIVES). */
export const WINDOW_LIVES = [300, 900] as const;
export type WindowLife = (typeof WINDOW_LIVES)[number];
/** How far this page's clock and the server's may differ for a window to open: a minute, either way. */
export const WINDOW_SKEW = 60;
/** What is left of a window when the last instruction may be sent under it: a session needs a minute to take one. */
export const WINDOW_LAST = 60;
/** What a linked session's client says on each read of its state when it takes instructions under a window. */
export const WINDOW_FEATURE = "delegation-1";
/** A preparation older than this many seconds is made again before its window opens: the window's length counts from
 *  the nonce's time, so one opened later would open that much shorter. It is
 *  well inside the nonce's five minutes. */
export const PREPARED_FRESH = 30;

const DELEGATION_TAG = "proofpane-delegation/1";
const DELEGATED_TAG = "proofpane-delegated/1";

export const CLOCK_WORDS = "This device's clock and the server's differ by more than a minute, so no window opens. "
  + "Check this device's clock: if it is right, the server's time cannot be trusted.";
export const LAST_MINUTE_WORDS = "An instruction needs a minute to be taken: send this one with your passkey.";
export const ENDED_WORDS = "The signing window has ended: an instruction now takes your passkey.";
export const PLACE_WORDS = "Not sent: the window's place had moved on, an earlier send having been stored after its answer "
  + "was lost. Send this one again.";

const hex = (bytes: Uint8Array) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
const b64u = (bytes: Uint8Array) => {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
};
const nowSeconds = () => Math.floor(Date.now() / 1000);
const tagged = (tag: string, payload: Record<string, unknown>) => new TextEncoder().encode(`${tag}\0${canonicalJson(payload)}`);

/** A delegation's name, which each instruction under it carries: the hex sha256 of "proofpane-delegation/1", a zero
 *  byte and its canonical JSON. The server and the session compute the same (link_commands.delegation_digest,
 *  ade_link.delegation_digest). */
export async function delegationDigest(payload: Record<string, unknown>): Promise<string> {
  return hex(new Uint8Array(await crypto.subtle.digest("SHA-256", tagged(DELEGATION_TAG, payload))));
}

/** A window kept in this tab: its digest, the page key's private half, the place of the last instruction the server
 *  stored under it, its start and end as signed, and its end by this page's own clock. */
interface Held { linkId: string; digest: string; key: CryptoKey; n: number; start: number; expires: number; end: number; busy: boolean; since: number }
const held = new Map<string, Held>();
/** Moved by every drop of every key (pagehide, sign-out): an opening under way when it moves keeps nothing. */
let generation = 0;

/** What the page shows of a window open in this tab (never its key). */
export interface WindowState { linkId: string; digest: string; n: number; start: number; expires: number; end: number }

/** A window composed before the click that opens it: the delegation as a step the passkey signs, and the key it
 *  delegates to, so the click reaches the prompt with nothing awaited first. */
export interface PreparedWindow { prepared: Prepared; linkId: string; life: WindowLife; key: CryptoKey; madeAt: number; end: number }

/** This tab's window for a link, while this page's clock is before its end; one past its end is dropped here. */
export function windowOf(linkId: string): WindowState | null {
  const w = held.get(linkId);
  if (!w) return null;
  if (nowSeconds() >= w.end) { held.delete(linkId); return null; }
  return { linkId: w.linkId, digest: w.digest, n: w.n, start: w.start, expires: w.expires, end: w.end };
}

/** Drop this tab's window for a link, and with it the only reference to its key. */
export function dropWindow(linkId: string): void {
  held.delete(linkId);
}

/** Drop every window this tab holds. */
export function dropEveryWindow(): void {
  generation += 1;
  held.clear();
}

/** At each read of the links list: this tab's window for a link the list no longer shows, or shows with another
 *  window, is dropped, whether or not its card is on the screen; one kept here after the read was asked (`askedAt`,
 *  Date.now()) is the list's to say at its next read. */
export function settleWindows(links: { id: string; delegation?: { digest: string } | null }[], askedAt: number): void {
  for (const w of [...held.values()]) {
    if (w.since >= askedAt) continue;
    const there = links.find((l) => l.id === w.linkId);
    if (!there || there.delegation?.digest !== w.digest) held.delete(w.linkId);
  }
}

if (typeof window !== "undefined") {
  // a page put away (a tab closed, a page left for another, or one kept in the back-forward cache) keeps no key: one
  // brought back finds no window, and says the window ended in this tab
  window.addEventListener("pagehide", dropEveryWindow);
}

/** Make the page key, ask a nonce for the delegation and compose it, refusing a server time more than WINDOW_SKEW
 *  from this page's clock either way. `fingerprint` is the link's key's, as the links list gives it: the session
 *  takes under a delegation only for its own key. */
export async function prepareWindow(linkId: string, fingerprint: string, life: WindowLife): Promise<PreparedWindow> {
  if (!WINDOW_LIVES.includes(life)) throw new Error("A signing window lasts 5 or 15 minutes.");
  if (!/^[0-9a-f]{32}$/.test(linkId) || !/^[0-9a-f]{32}$/.test(fingerprint)) throw new Error("This link cannot take a window.");
  const pair = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]) as CryptoKeyPair;
  if (pair.privateKey.extractable !== false) throw new Error("This browser made a key that could be copied out of it, so no window opens.");
  const spki = new Uint8Array(await crypto.subtle.exportKey("spki", pair.publicKey));
  const n = await api<{ nonce: string; rp_id: string; deployment: string; account: string; server_time: number; credentials?: string[] }>(
    "/api/ade/link/nonce", { method: "POST", body: { purpose: "delegation" } });
  const mine = nowSeconds();
  if (!Number.isSafeInteger(n.server_time) || Math.abs(n.server_time - mine) > WINDOW_SKEW) throw new Error(CLOCK_WORDS);
  const random = new Uint8Array(16);
  crypto.getRandomValues(random);
  const payload: Record<string, unknown> = {
    v: 2, deployment: n.deployment, purpose: "delegation", account: n.account, nonce: n.nonce, id: hex(random),
    delegate: { kind: "page-key", alg: -7, key: b64u(spki) },
    sessions: [{ id: linkId, fingerprint, kinds: ["instruction"] }],
    created_at: n.server_time, expires_at: n.server_time + life,
  };
  const allow = (n.credentials ?? []).filter((c) => typeof c === "string");
  return { prepared: { payload, challenge: await stepChallenge(payload), rpId: n.rp_id, words: "", allow }, linkId, life,
    key: pair.privateKey, madeAt: Date.now(), end: mine + life };
}

/** Whether a prepared window is too old to open: one made more than PREPARED_FRESH seconds ago would open shorter
 *  than the length its words give. */
export const preparedStale = (p: PreparedWindow) => Date.now() - p.madeAt > PREPARED_FRESH * 1000;

/** Open a prepared window: the passkey's prompt first, nothing awaited before it; then the delegation to the server,
 *  which ends this link's window before it. Kept here only once the server answers with the same digest. */
export async function openWindow(p: PreparedWindow): Promise<WindowState> {
  const asOf = generation;
  const signed = await signPrepared(p.prepared);
  const answer = await api<{ digest: string; expires_at: number }>("/api/ade/link/delegations", { method: "POST", body: signed });
  const digest = await delegationDigest(p.prepared.payload);
  if (answer.digest !== digest) throw new Error("The server named another window than the one signed, so this tab keeps none.");
  if (asOf !== generation) {
    // the page was put away (or signed out) while the window opened: no key is kept, and its end is posted; after a
    // sign-out that post has no sign-in and is refused, and the window, with no key left to sign under it, ends at
    // its own end or at End now on another device
    void api(`/api/ade/link/delegations/${encodeURIComponent(digest)}/end`, { method: "POST" }).catch(() => undefined);
    throw new Error("This page was put away while the window opened, so this tab keeps none; its end was posted.");
  }
  dropWindow(p.linkId);
  held.set(p.linkId, { linkId: p.linkId, digest, key: p.key, n: 0, start: Number(p.prepared.payload.created_at),
    expires: Number(p.prepared.payload.expires_at), end: p.end, busy: false, since: Date.now() });
  return windowOf(p.linkId)!;
}

/** Why an instruction cannot be sent under this tab's window for a link now, or null: none open here, its last
 *  minute by this page's clock, or one already on its way. */
export function sendRefusal(linkId: string): string | null {
  const w = held.get(linkId);
  if (!w || nowSeconds() >= w.end) return ENDED_WORDS;
  if (w.end - nowSeconds() < WINDOW_LAST) return LAST_MINUTE_WORDS;
  if (w.busy) return "An instruction is on its way under the window: wait for it.";
  return null;
}

/** The server's answer to a send that did not come back as stored. */
export class WindowSendError extends Error {
  constructor(message: string, public ended: boolean) { super(message); }
}

/** Send one instruction under this tab's window for a link: a nonce, the instruction with the window's digest and its
 *  place, signed here with the page key over "proofpane-delegated/1", a zero byte and its canonical JSON. Its place
 *  moves only on the server's 2xx; an answer that did not arrive is settled by `settle` (the window's last_n from the
 *  links list, and the ids of the link's commands it lists). A 401, 404 or 410 drops the window. */
/** `settle`: the link's window as the links list now has it ({digest, last_n}) and the ids of the commands it lists for
 *  the link (`ids`), read only when an answer did not come: stored when this instruction's own id is among them. */
export async function sendInWindow(linkId: string, text: string,
  settle?: () => Promise<{ digest: string; last_n: number; ids: string[] } | null>): Promise<{ seq: number; n: number }> {
  const refusal = sendRefusal(linkId);
  if (refusal) throw new WindowSendError(refusal, refusal === ENDED_WORDS);
  const w = held.get(linkId)!;
  w.busy = true;
  try {
    const n = await api<{ nonce: string; deployment: string; account: string; server_time: number }>(
      "/api/ade/link/nonce", { method: "POST", body: { purpose: "instruction" } });
    if (held.get(linkId) !== w || nowSeconds() >= w.end) throw new WindowSendError(ENDED_WORDS, true);
    const at = n.server_time;
    if (!Number.isSafeInteger(at) || Math.abs(at - nowSeconds()) > WINDOW_SKEW) throw new WindowSendError(CLOCK_WORDS, false);
    if (at < w.start || at > w.expires - WINDOW_LAST) throw new WindowSendError(LAST_MINUTE_WORDS, false);
    const random = new Uint8Array(16);
    crypto.getRandomValues(random);
    const payload: Record<string, unknown> = {
      v: 2, deployment: n.deployment, purpose: "instruction", account: n.account, nonce: n.nonce, id: hex(random),
      to: linkId, text, created_at: at, expires_at: w.expires, delegation: w.digest, n: w.n + 1,
    };
    const sig = new Uint8Array(await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, w.key, tagged(DELEGATED_TAG, payload)));
    if (held.get(linkId) !== w) throw new WindowSendError(ENDED_WORDS, true);   // dropped while it was being signed
    let stored: { seq: number };
    try {
      stored = await api<{ seq: number }>("/api/ade/link/commands", { method: "POST", body: { payload, delegated: { digest: w.digest, sig: b64u(sig) } } });
    } catch (e) {
      const status = (e as { status?: number }).status ?? 0;
      if (status === 401 || status === 404 || status === 410) {
        if (held.get(linkId) === w) dropWindow(linkId);
        throw new WindowSendError(status === 410 ? ENDED_WORDS : (e as Error).message, true);
      }
      // the place the server holds, which an earlier send stored after its answer was lost had moved: never sent, and
      // the next send follows the server's place
      const data = (e as { data?: { why?: unknown; last_n?: unknown } }).data;
      if (status === 409 && data?.why === "delegation_n" && Number.isSafeInteger(data.last_n)) {
        w.n = data.last_n as number;
        throw new WindowSendError(PLACE_WORDS, false);
      }
      // an answer that did not come, and only that: the links list says whether this instruction was stored by its
      // own id among the link's commands, never by the place alone (a place one behind after an earlier lost answer
      // reads that send's place as this one's), and this window's place there is the next send's
      if (status === 0 && settle) {
        const got = await settle().catch(() => null);
        if (got && got.digest === w.digest && Number.isSafeInteger(got.last_n) && Array.isArray(got.ids)) {
          const took = w.n + 1;
          w.n = got.last_n;
          if (got.ids.includes(payload.id as string)) return { seq: 0, n: took };
        }
      }
      throw e;
    }
    w.n += 1;
    return { seq: stored.seq, n: w.n };
  } finally {
    w.busy = false;
  }
}

/** End this tab's window, or another's by its digest (one opened on another device): the key dropped first, then
 *  the end posted, which needs only the sign-in, since an end only refuses more. */
export async function endWindow(linkId: string, digest: string): Promise<void> {
  if (held.get(linkId)?.digest === digest) dropWindow(linkId);
  await api<{ ended_at: number }>(`/api/ade/link/delegations/${encodeURIComponent(digest)}/end`, { method: "POST" });
}

/** At this page's sign-out: every window this tab holds ended, each key dropped first and each end posted while the
 *  sign-in still holds (the request takes it as it starts); what fails to arrive ends with the window's own end. */
export function endEveryWindow(): void {
  const all = [...held.values()];
  dropEveryWindow();
  for (const w of all) {
    void api(`/api/ade/link/delegations/${encodeURIComponent(w.digest)}/end`, { method: "POST" }).catch(() => undefined);
  }
}
