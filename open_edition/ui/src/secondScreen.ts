// The second screen on the page. A step the owner
// reviewed here may be parked for a signer: a program on the owner's own Mac (ade_link.py sign) that shows the words
// there, decoded from the exact bytes their security key signs. The page parks the payload it composed, shows the
// request's code (computed here from those bytes, never taken from the server), waits for the assertion, checks that
// the payload it gets back is byte for byte the one it parked, and sends both to the route it would have sent the
// browser's assertion to; that route checks everything again. The page also lists what waits for a signer, cancels it,
// and pairs and revokes signers. What it may say of a stored step is signedWords's: never that a step was signed or
// shown on the Mac, that the owner saw the words, or that anything was verified there.

import { api } from "./api";
import { canonicalJson, stepChallenge, type Signed } from "./passkey";
import { visible } from "./textRules";

/** The client data's "proofpane" member a signer writes: the signing program's own word, which an honest browser never
 *  writes and any other program can. */
export const MARKER = "second-screen/1";
/** How often the page asks whether its request was signed (the relay's own pace). */
export const POLL_EVERY_MS = 2000;

const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

/** RFC 4648 base32 of `bytes`, unpadded. */
export function base32(bytes: Uint8Array): string {
  let bits = 0;
  let value = 0;
  let out = "";
  for (const b of bytes) {
    value = ((value << 8) | b) & 0xffff;
    bits += 8;
    while (bits >= 5) {
      out += B32[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits > 0) out += B32[(value << (5 - bits)) & 31];
  return out;
}

/** A step's request code: the first 5 bytes of its step challenge in base32, as two groups of four, computed here from
 *  the payload the page composed, as the Mac computes it from the bytes it fetched (ade_link.sign_code). It matches a
 *  request to its prompt; 40 bits prove nothing. */
export async function requestCode(payload: Record<string, unknown>): Promise<string> {
  const code = base32((await stepChallenge(payload)).slice(0, 5));
  return `${code.slice(0, 4)}-${code.slice(4)}`;
}

// ---- the server's routes ---------------------------------------------------------------------------------------------

/** A signer as the page lists it: its name is the one it was paired with, the times are the server's. */
export interface SignerView { id: string; name: string; created_at: number; last_seen_at: number | null }
/** A ticket to pair a signer, good for 600 seconds and used once, with the command that pairs it. */
export interface SignerTicket { ticket: string; expires_at: number; url: string; account: string; command: string }
/** A request that has not ended, as the page lists every one of the account's: never its code, which a page shows only
 *  beside a request it parked itself. */
export interface ParkedBrief {
  id: string; purpose: string; to: string | null; created_at: number; expires_at: number; parked_from: string;
  state: "waiting" | "signed";
}
/** What the page keeps of a request it parked: its id, the code it computed itself, and when it ends. */
export interface OnMac { id: string; code: string; expires_at: number }
interface Collected { id: string; state: "waiting" | "signed"; expires_at: number; payload?: string; credential?: unknown; signer?: string | null }

export const SecondScreen = {
  signers: () => api<{ signers: SignerView[] }>("/api/ade/signers"),
  newTicket: (confirmation?: string) =>
    api<SignerTicket>("/api/ade/signers/tickets", { method: "POST", body: confirmation ? { confirmation } : {} }),
  revoke: (id: string) => api<{ revoked_at: number }>(`/api/ade/signers/${encodeURIComponent(id)}`, { method: "DELETE" }),
  park: (payload: Record<string, unknown>, title?: string) =>
    api<{ id: string; code: string; expires_at: number }>("/api/ade/sign-requests",
      { method: "POST", body: title === undefined ? { payload } : { payload, title } }),
  waiting: () => api<{ requests: ParkedBrief[] }>("/api/ade/sign-requests"),
  collect: (id: string) => api<Collected>(`/api/ade/sign-requests/${encodeURIComponent(id)}`),
  cancel: (id: string) => api<{ cancelled: number }>(`/api/ade/sign-requests/${encodeURIComponent(id)}`, { method: "DELETE" }),
  cancelAll: () => api<{ cancelled: number }>("/api/ade/sign-requests", { method: "DELETE" }),
};

/** The requests this page parked in this tab, by id, with the code it computed: the waiting list shows a code beside
 *  these alone, and says of every other that this page did not park it. */
const parkedHere = new Map<string, string>();
export const codeParkedHere = (id: string) => parkedHere.get(id);

export const NOT_PARKED = "The server handed back a payload other than the one this page parked, so nothing was sent.";

/** Park the payload of a reviewed step for a signer (a move's `title` beside it, as the page sends one): the code is
 *  this page's own, from the payload it composed. It takes the payload alone: the step's challenge stays with the
 *  page's own prompt (challengeAudit.test.ts), and the signer computes it from the bytes it fetches. */
export async function parkOnMac(payload: Record<string, unknown>, title?: string): Promise<OnMac> {
  const code = await requestCode(payload);
  const got = await SecondScreen.park(payload, title);
  parkedHere.set(got.id, code);
  return { id: got.id, code, expires_at: got.expires_at };
}

/** The signed step for a request this page parked, once a signer handed its assertion back; null while it waits. The
 *  payload the server holds must be byte for byte the one this page composed (NOT_PARKED otherwise): the page sends
 *  its own payload, never the server's. */
export async function collectFromMac(payload: Record<string, unknown>, parked: OnMac): Promise<Signed | null> {
  const got = await SecondScreen.collect(parked.id);
  if (got.state !== "signed") return null;
  if (got.payload !== canonicalJson(payload)) throw new Error(NOT_PARKED);
  return { payload, credential: got.credential };
}

// ---- what the page may say of a stored step -------------------------------------------------------------------------

/** What the server says signed one of the owner's stored steps: the passkey (its id), whether the stored client data
 *  carries the marker, and the signer it came through. */
export interface SignedFacts { passkey_id?: string | null; marked?: boolean; relayed_by?: string | null }
/** The account's passkeys, as the page lists them (Account): their names and tiers are the server's own records. */
export interface KnownPasskey { id: string; name: string; tier?: string }

/** What the page says of a hardware key's signature whose client data carries the marker, and, a click away, what it
 *  does not mean: never more than the signing program's own word. */
export const MARKED_WORDS = "says it was signed outside a browser";
export const MARKED_MEANS = "An honest browser never says so; a compromised one, or any other program, can. It says nothing of what was shown.";

/** The words beside a stored step: the passkey that signed it ("Signed with" its name, and "(hardware key)" for one),
 *  the signer it was relayed by, and whether to say MARKED_WORDS, for a hardware key's signature alone. A passkey held
 *  in software can be made to write the marker, so for one nothing is said of it. Nothing of a passkey this page does
 *  not know; null where there is nothing to say. */
export function signedWords(facts: SignedFacts, keys: KnownPasskey[]): { line: string; marked: boolean } | null {
  const key = keys.find((k) => k.id === facts.passkey_id);
  const parts: string[] = [];
  if (key) parts.push(`Signed with ${visible(key.name)}${key.tier === "hardware" ? " (hardware key)" : ""}`);
  if (typeof facts.relayed_by === "string" && facts.relayed_by) parts.push(`relayed by your signer ${visible(facts.relayed_by)}`);
  const marked = key?.tier === "hardware" && facts.marked === true;
  if (!parts.length && !marked) return null;
  return { line: parts.join(", "), marked };
}
