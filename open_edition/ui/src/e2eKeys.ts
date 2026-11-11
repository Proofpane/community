// The account's end-to-end encryption keys on the page: what the server keeps of them (GET
// /api/ade/e2e), unlocking them with one touch of a passkey's PRF or the recovery code and checking that they lead back
// to the key's own anchor, and the changes the owner makes: turning it on, adding a passkey, rotating the content key,
// a revocation (removing a passkey, turning high security on, a new recovery code), renewing anchors, turning it off,
// and the recovery code as a confirmation. The server keeps only what these seal; the page alone opens them.
import { api, ApiError } from "./api";
import { assertionWith, canonicalJson, confirmCeremony, datedNow, isSecurityKey, pageChallenge, stepChallenge, type AssertionAnswer, type Ceremony, type Challenge } from "./passkey";
import * as C from "./e2eCrypto";

// ---- what the server keeps, as GET /api/ade/e2e serves it ------------------------------------------------------------

/** A statement of the account's keys, as stored: its payload in full (its canonical text), without its signature. */
export interface E2EStatement {
  seq: number; id: string; purpose: "e2e-key" | "e2e-off"; key_id?: string | null; public_key?: string | null;
  retires?: "all" | "grace" | null; payload: Record<string, unknown> | string; passkey_id?: string | null;
  created_at: number; retired_at?: number | null;
}
/** An enrolled credential: its PRF salt, its key pair's public half, and its own wrap of the private half. */
export interface E2ECredential {
  passkey_id: string; credential_id: string; salt: string; public_key: string; wrapped: string; created_at?: number;
  /** a post-quantum credential's anchor, stored beside its wrap and bound by its AAD (an mk_id); none for a classical
   *  one, whose anchor is inside its wrap */
  anchor?: string | null;
}
export interface E2ERecovery { public_key: string; anchor: string; created_at?: number }
/** A master key sealed to a credential's key or the recovery key (`to`: the credential id, or "recovery"). */
export interface E2EGrant { mk_id: string; to: string; sealed: string; created_at?: number }
/** A link of the chain: `previous`, the master key `mk_id` replaced, wrapped under the chain subkey of `mk_id`. */
export interface E2EChainLink { mk_id: string; previous: string; wrapped: string; created_at?: number }
export interface E2ERosterRow { mk_id: string; sealed: string; created_at?: number }
export interface E2EContentKey { key_id: string; public_key: string; mk_id: string; wrapped: string; created_at?: number }
/** One of the account's passkeys, as encryption sees it: whether the browser said it gives PRF as it was made, whether
 *  it is enrolled, and whether it may be (not yet, and while high security is on only a hardware key). */
export interface E2EPasskey {
  passkey_id: string; credential_id: string; name: string; created_at: number; prf?: boolean; tier?: string;
  enrolled?: boolean; can_enrol?: boolean;
  /** what the server says it is (passkeys.kind_of) and, for a hardware key, its model: absent from a server before */
  kind?: string; maker?: string | null;
}
/** A linked session as encryption sees it: the key it last sealed to, when the server last refused it a text in the
 *  clear, the fingerprints of the owner keys it says it pinned (null until it has reported them, told from one that
 *  pins none, [], as the server serves them), and the envelope forms it says it can seal (`seals`: null until it has
 *  said; its word, guidance for the owner, never a check). */
export interface E2ELink { link_id: string; name: string; key_id: string | null; last_required_at: number | null; pins: string[] | null; seals?: number[] | null }
/** A change that waits on a hold before it is made: the hold, which change it is, and who may cancel it, as the server
 *  decides it (e2e_keys.cancellers, the second version: the recovery code is the root), which the page offers exactly:
 *  `cancel_by`, the passkeys whose touch cancels it (none for a change the recovery code made or whose proof came with
 *  it, which only the code cancels; else those it does not remove, and those it removes too when a passkey signed it and
 *  it leaves no passkey but that one; while high security is on, hardware keys alone), and `cancel_by_code`, whether the
 *  recovery code in force does (always, once the account has one, a change that replaces it included). The sign-in
 *  alone never does. And what made it (`made_by`: the passkey that signed or confirmed it, "recovery" for the recovery
 *  code, null for a linked session's), whether it is code-backed (`by_code`: made by the code, or with its proof), the
 *  passkeys it removes (`removes`), whether it replaces the recovery code (`replaces_code`), and what was asked
 *  (`body`: public keys, wraps and statements, nothing secret), as the server says. */
export interface E2EPending {
  id: string; change: string; takes_effect_at: number; summary?: string; kind?: string; cancel_by: string[];
  cancel_by_code: boolean; made_by: string | null; by_code: boolean; removes: string[]; replaces_code: boolean;
  body: Record<string, unknown> | null;
  /** how many devices its notice was sent toward, as its hold keeps it (-1: it could not be sent), when kept */
  told?: unknown;
}
/** What the last sealing in place sealed, and when; and, at a post-quantum turn-on, how many conversations it deleted
 *  (`dropped`, given where any was: e2e_keys.seal_in_place seals a conversation whole, so one that held a message
 *  sealed already, to an earlier key, goes, and its link sends it again under the new key). */
export interface E2ESealed { events?: number; left?: number; rows?: number; talks?: number; dropped?: number; at?: number }
export interface E2EView {
  on: boolean;                      // the newest statement is e2e-key
  /** whether the account is post-quantum (ade_e2e_state.pq): its keys made post-quantum, by a first turn-on,
   *  starting again or the upgrade; it then takes no new classical key material */
  pq: boolean;
  /** whether the server can use ML-KEM (its start-up probe): without it, it refuses a post-quantum turn-on, the upgrade
   *  and the hybrid proof; false only where the server says so */
  mlkem: boolean;
  /** for a post-quantum account, how many texts sealed in form 1 (to a classical key) the server still stores, by where
   *  (its events, its History rows listed or not, its conversations holding one; the
   *  connector's proposals hold none) and in all; null for a classical account, or where the server does not say */
  classical_texts: { events: number; rows: number; talks: number; total: number } | null;
  mk_id: string | null;             // the master key the server says is current
  /** each statement's purpose, key_id, public key and retires as its signed payload says them (asSigned) */
  statements: E2EStatement[];
  /** the seqs of statements served with a column other than their signed payload says, or with a payload that is no
   *  statement's: no change is made from such keys (STATED_OTHERWISE) */
  misstated: number[];
  credentials: E2ECredential[];
  recovery: E2ERecovery | null;
  grants: E2EGrant[];
  chain: E2EChainLink[];
  roster: E2ERosterRow[];
  content_keys: E2EContentKey[];
  passkeys: E2EPasskey[];
  links: E2ELink[];
  pending: E2EPending | null;
  high_security: boolean;
  /** the server's word that a new master key is due before encryption is turned on again: a passkey that unlocked it
   *  was removed, or left out by high security, while it was off (never while it is on) */
  rekey_due: boolean;
  /** the database may still hold, in its free space or its log, what the last sealing in place replaced */
  compaction_due: boolean;
  /** what the last sealing in place sealed, and when */
  sealed: E2ESealed | null;
  /** what turning it on would seal in place: texts links sent in the clear (`left`: events that cannot be sealed) */
  to_seal: { events?: number; left?: number; rows?: number; talks?: number } | null;
  /** how long a change waits, in seconds: confirmed by a passkey; by the recovery code; and one that replaces the keys,
   *  the recovery code or every other credential */
  holds: { change?: number; recovery?: number; long?: number } | null;
}

const list = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
const objectOr = <T>(v: unknown): T | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as T) : null);

/** A statement as its payload was signed: its purpose, key_id, public key and retires read from the payload, which is
 *  what links obey, never from the columns the server serves beside it; and whether the columns served say the same
 *  (`agrees`: seq and id too; one left out says nothing). A payload that is no statement's (an e2e-key with its key, a
 *  key_id of 32 lowercase hex and what it retires, or an e2e-off with none of them) leaves the statement as served,
 *  and does not agree. */
function asSigned(s: E2EStatement): { statement: E2EStatement; agrees: boolean } {
  let p: Record<string, unknown> | null;
  try {
    p = objectOr<Record<string, unknown>>(payloadOf(s));
  } catch {
    return { statement: s, agrees: false };
  }
  if (!p) return { statement: s, agrees: false };
  const key = p.purpose === "e2e-key";
  if (key ? typeof p.key !== "string" || typeof p.key_id !== "string" || !/^[0-9a-f]{32}$/.test(p.key_id) || (p.retires !== "all" && p.retires !== "grace")
    : p.purpose !== "e2e-off" || p.key !== undefined || p.key_id !== undefined || p.retires !== undefined) return { statement: s, agrees: false };
  const signed = { purpose: p.purpose as E2EStatement["purpose"], key_id: key ? p.key_id as string : null,
    public_key: key ? p.key as string : null, retires: key ? p.retires as "all" | "grace" : null };
  const same = (column: unknown, value: unknown) => column === undefined || (column ?? null) === (value ?? null);
  const agrees = same(s.seq, p.seq) && same(s.id, p.id) && same(s.purpose, signed.purpose) && same(s.key_id, signed.key_id)
    && same(s.public_key, signed.public_key) && same(s.retires, signed.retires);
  return { statement: { ...s, ...signed }, agrees };
}

/** The view as served, with every list a list and a missing field what it means: nothing. Each statement is read as
 *  its payload was signed (asSigned), and one served otherwise is named in `misstated`. */
export function readView(raw: unknown): E2EView {
  const v = objectOr<Record<string, unknown>>(raw) ?? {};
  const misstated: number[] = [];
  const statements = list<E2EStatement>(v.statements).slice().sort((a, b) => a.seq - b.seq).map((s) => {
    const { statement, agrees } = asSigned(s);
    if (!agrees) misstated.push(s.seq);
    return statement;
  });
  const newest = statements[statements.length - 1];
  // what links obey is the newest statement as it was signed, so that is what says whether encryption is on
  const on = newest?.purpose === "e2e-key";
  const roster = Array.isArray(v.roster) ? list<E2ERosterRow>(v.roster) : objectOr<E2ERosterRow>(v.roster) ? [v.roster as E2ERosterRow] : [];
  const pending = objectOr<E2EPending>(v.pending);
  return {
    on,
    pq: v.pq === true,
    mlkem: v.mlkem !== false,
    classical_texts: classicalTextsOf(v.classical_texts),
    mk_id: typeof v.mk_id === "string" ? v.mk_id : null,
    statements,
    misstated,
    credentials: list(v.credentials),
    recovery: objectOr<E2ERecovery>(v.recovery),
    grants: list(v.grants),
    chain: list(v.chain),
    roster,
    content_keys: list(v.content_keys),
    passkeys: list<E2EPasskey & { id?: string }>(v.passkeys).map((p) => ({ ...p, passkey_id: p.passkey_id ?? p.id ?? "" })),
    links: list<E2ELink>(v.links).map((l) => ({ ...l, pins: l.pins == null ? null : list<string>(l.pins),
      seals: l.seals == null ? null : list<unknown>(l.seals).filter((f): f is number => typeof f === "number") })),
    // who may cancel it, exactly as listed: no passkey, and not the code, where the server names none; what made it,
    // whether it is code-backed (one the recovery code made is, whatever else is said), what it removes, and what was
    // asked, as said
    pending: pending && { ...pending, cancel_by: list<unknown>(pending.cancel_by).filter((id): id is string => typeof id === "string"),
      cancel_by_code: pending.cancel_by_code === true, made_by: typeof pending.made_by === "string" ? pending.made_by : null,
      by_code: pending.by_code === true || pending.made_by === RECOVERY,
      removes: list<unknown>(pending.removes).filter((id): id is string => typeof id === "string"), replaces_code: pending.replaces_code === true,
      body: objectOr<Record<string, unknown>>(pending.body) },
    high_security: v.high_security === true,
    // a new master key is due only for turning it on again: never while it is on
    rekey_due: v.rekey_due === true && !on,
    compaction_due: v.compaction_due === true,
    sealed: objectOr<E2ESealed>(v.sealed),
    to_seal: objectOr(v.to_seal),
    // each hold's length in seconds, a number the server gave, or left out (what the page then says without a length)
    holds: holdsOf(v.holds),
  };
}

const count = (n: unknown) => (typeof n === "number" && Number.isInteger(n) && n >= 0 ? n : 0);
/** The server's count of texts still sealed in form 1, as served: each place's count a whole number or nothing, and the
 *  total the server's where it gives one, else the places' sum. */
function classicalTextsOf(raw: unknown): E2EView["classical_texts"] {
  const c = objectOr<Record<string, unknown>>(raw);
  if (!c) return null;
  const events = count(c.events), rows = count(c.rows), talks = count(c.talks);
  return { events, rows, talks, total: typeof c.total === "number" ? count(c.total) : events + rows + talks };
}

/** How many texts sealed in form 1 the server says it still stores, everywhere; null where it does not say. */
export const classicalTextCount = (v: E2EView | null) => v?.classical_texts?.total ?? null;

const seconds = (n: unknown) => (typeof n === "number" && Number.isFinite(n) && n >= 0 ? n : undefined);
function holdsOf(raw: unknown): E2EView["holds"] {
  const h = objectOr<Record<string, unknown>>(raw);
  return h && { change: seconds(h.change), recovery: seconds(h.recovery), long: seconds(h.long) };
}

/** Whether the account holds keys at all: then what was sealed can be unlocked, even after encryption was turned off. */
export const hasKeys = (v: E2EView | null) => !!v && (!!v.mk_id || v.credentials.length > 0);

// ---- the server's routes -------------------------------------------------------------------------------------------

/** What a change's request answers: the hold it waits on, when it waits. */
export interface Answer { hold?: { id: string; takes_effect_at: number } | null; [k: string]: unknown }
interface Nonce { nonce: string; rp_id: string; deployment: string; account: string; server_time: number; credentials?: string[] }
export interface ContentKeyRow { key_id: string; public_key: string; mk_id: string; wrapped: string }
export interface Signed { payload: Record<string, unknown>; credential: AssertionAnswer }
/** The recovery code's proof, given beside a change in place of a passkey's confirmation. */
export interface RecoveryProof { challenge_id: string; proof: string }
/** What the recovery code confirms, by the change, or cancelling the change that waits; turn-on, the fold that keeps
 *  the code (Fold). */
export type RecoveryPurpose = "rotate" | "revoke" | "credentials" | "off" | "anchors" | "passkey" | "cancel"
  | "turn-on" | "reseal";

const post = <T = Answer>(path: string, body: unknown) => api<T>(path, { method: "POST", body });

export const E2EApi = {
  /** The account's keys as the server holds them, each credential's salt hashed ahead of an unlock's touch
   *  (primeUnlock). */
  view: async () => {
    const view = readView(await api<unknown>("/api/ade/e2e"));
    await primeUnlock(view);
    return view;
  },
  turnOn: (body: Record<string, unknown>) => post("/api/ade/e2e/turn-on", body),
  rotate: (body: Record<string, unknown>) => post("/api/ade/e2e/rotate", body),
  revoke: (body: Record<string, unknown>) => post("/api/ade/e2e/revoke", body),
  enrol: (body: Record<string, unknown>) => post("/api/ade/e2e/credentials", body),
  off: (body: Record<string, unknown>) => post("/api/ade/e2e/off", body),
  anchors: (body: Record<string, unknown>) => post("/api/ade/e2e/anchors", body),
  /** A page of the stored texts still sealed to a classical key, after `after` (the last page's `next`), to re-seal. */
  resealPage: (after: string | null) =>
    api<{ items: ResealItem[]; next: string | null }>(`/api/ade/e2e/reseal${after ? `?after=${encodeURIComponent(after)}` : ""}`),
  /** A batch of texts sealed again, swapped where what is stored is still what was read, with the recovery code's
   *  proof bound to the batch. */
  reseal: (body: Record<string, unknown>) => post<{ swapped: number; skipped?: number }>("/api/ade/e2e/reseal", body),
  /** The server's challenge for the recovery code as a confirmation: a fresh X25519 public key and a nonce for a
   *  classical recovery key, or an X-Wing ciphertext encapsulated to a post-quantum one and a nonce; `forKey`, the
   *  post-quantum recovery key an upgrade puts in place, to make it for that key (ProofChallenge); `digest`, a re-seal's
   *  batch (resealBinding), which the proof is then good for alone. */
  recoveryChallenge: (purpose: RecoveryPurpose, forKey?: Uint8Array, digest?: string) =>
    post<ProofChallenge>("/api/ade/e2e/recovery-proof", { purpose, ...(forKey ? { public_key: C.b64u(forKey) } : {}), ...(digest ? { digest } : {}) }),
  nonce: (purpose: "e2e-key" | "e2e-off") => post<Nonce>("/api/ade/link/nonce", { purpose }),
  /** A touch's confirmation, its options asked for with the touch's purpose (passkey.confirmCeremony). */
  confirm: (challengeId: string, credential: AssertionAnswer) =>
    post<{ confirmation: string }>("/api/account/confirm", { challenge_id: challengeId, credential }),
  /** Cancelling the change that waits: with a passkey's confirmation or the recovery code's proof (CancelChange). */
  cancel: (holdId: string, body: { confirmation: string } | { recovery_proof: RecoveryProof }) =>
    post<{ cancelled_at: number }>(`/api/ade/holds/${encodeURIComponent(holdId)}/cancel`, body),
};

// ---- words ---------------------------------------------------------------------------------------------------------

export const NOT_LEAD_BACK = "The keys on the server do not lead back to this key's own record.";
/** The current master key's grant to the key that unlocks, which does not open with it to the key it names: told from
 *  keys that do not lead back. */
export const GRANT_NOT_OPEN = "The master key's grant to this key does not open with it: the server holds a grant not sealed to this key, or a damaged one.";
/** The recovery code's anchor, which the code, checked against the recovery key, does not open. */
export const ANCHOR_NOT_OPEN = "The recovery code is this account's, but the anchor the server holds for it does not open with it: the server holds an anchor not made with this code, or a damaged one.";
export const NOT_LISTED = "The keys on the server do not list this key among those the master key is sealed to.";
export const NO_GRANT = "The server holds no master key sealed to this key: it was removed, or added without the master key.";
export const BAD_CONTENT_KEY = "A content key on the server does not open with the master key.";
export const ROSTER_NOT_OPEN = "The roster of these keys, which names the keys a new master key may be sealed to, does not open with the master key: what was sealed is read in this tab, but no passkey is added to encryption or has its record renewed, and no new master key is made, from these keys. To replace them, turn encryption off and start again.";
export const ROSTER_LACKS_THIS = "The roster of these keys, which names the keys a new master key may be sealed to, does not name the key that unlocked this tab: what was sealed is read in this tab, but no passkey is added to encryption or has its record renewed, and no new master key is made, from these keys. Unlock with another passkey, or turn encryption off and start again.";
export const NEWEST_UNOPENED = "The newest content key the account stated does not open with these keys, so what links seal to it is not read here, and no new content key or master key is made from them: the page cannot tell such a key from an old copy of the keys served with the newest key put back in. To replace the keys, turn encryption off and start again.";
export const OWN_RECORD = "This passkey did not open the record the server holds for it: that record may not be its own, or this browser may give the passkey another key than the one it was added with.";
export const NO_PRF = "This passkey gave no key in this browser, so it cannot unlock encryption here. Use the recovery code.";
/** Where another browser may help a security key that gave no key: on a computer; on an iPhone or iPad every browser
 *  uses Safari's engine, which is one of those that give a security key none. */
const ELSEWHERE = "on an iPhone or iPad, whose browsers use Safari's engine, another browser may not help";
/** A security key, by what the server keeps of it (its kind: a security key or a hardware key, securityKeyIds), that
 *  gave no PRF output: some browsers give a security key none, Safari among them. Decided by the stored kind, never the
 *  browser's word on the authenticator, which says cross-platform of a phone reached by its code too. */
export const NO_PRF_KEY = `This security key gave no key in this browser, so it cannot unlock encryption here: some browsers give a security key none, Safari among them. On a computer, unlock with it in another browser, such as Chrome or Firefox; ${ELSEWHERE}. Here, use another passkey or the recovery code.`;
/** NO_PRF_KEY as a security key is added to encryption, or turns it on, which it cannot be in this browser. */
export const NO_PRF_KEY_ADD = `This security key gave no key in this browser, so it cannot be added to encryption here: some browsers give a security key none, Safari among them. Add it on a computer, in another browser such as Chrome or Firefox; ${ELSEWHERE}.`;
/** A passkey that is no security key, that gave no PRF output as it is added. */
export const NO_PRF_ADD = "This passkey gave no key in this browser, so it cannot be added to encryption here.";
/** As a passkey's record is renewed (RenewAnchors): another passkey renews nothing of this one's. */
export const NO_PRF_RENEW = "This passkey gave no key in this browser, so its record cannot be renewed here: renew it in a browser where it gives its key.";
export const NO_PRF_KEY_RENEW = `This security key gave no key in this browser, so its record cannot be renewed here: some browsers give a security key none, Safari among them. Renew it on a computer, in another browser such as Chrome or Firefox; ${ELSEWHERE}.`;
/** As the upgrade keeps a passkey (Upgrade: its touch, or the touch that signs), which removes those not touched. */
export const NO_PRF_UPGRADE = "This passkey gave no key in this browser, so it cannot be kept here, and the upgrade removes from your account each passkey not touched: do the upgrade in a browser where it gives its key.";
export const NO_PRF_KEY_UPGRADE = `This security key gave no key in this browser, so it cannot be kept here, and the upgrade removes from your account each passkey not touched: some browsers give a security key none, Safari among them. Do the upgrade on a computer, in another browser such as Chrome or Firefox; ${ELSEWHERE}.`;

/** What a touch was for, which its no-key words say: unlocking (the default), adding a passkey to encryption or turning
 *  it on, renewing a passkey's record, or keeping a passkey in the upgrade. */
export type TouchFor = "unlock" | "add" | "renew" | "upgrade";

/** The words for a passkey that gave no PRF output in a touch made for `doing`, a security key's (`key`) or another's. */
export function noPrfWords(doing: TouchFor = "unlock", key = false): string {
  if (doing === "add") return key ? NO_PRF_KEY_ADD : NO_PRF_ADD;
  if (doing === "renew") return key ? NO_PRF_KEY_RENEW : NO_PRF_RENEW;
  if (doing === "upgrade") return key ? NO_PRF_KEY_UPGRADE : NO_PRF_UPGRADE;
  return key ? NO_PRF_KEY : NO_PRF;
}

/** The credentials of the passkeys among `passkeys` that are security keys by what the server keeps of them (their kind,
 *  passkey.isSecurityKey), with `credentials` naming each passkey's credential where the passkey's own entry does not. */
export function keyCredentials(passkeys: readonly E2EPasskey[], credentials: readonly { passkey_id: string; credential_id: string }[] = []): string[] {
  const keys = passkeys.filter((p) => isSecurityKey(p));
  const ids = keys.map((p) => p.credential_id).filter((id): id is string => !!id);
  for (const c of credentials) if (keys.some((p) => p.passkey_id === c.passkey_id)) ids.push(c.credential_id);
  return [...new Set(ids)];
}

/** keyCredentials of a view's passkeys: none for no view. */
export const securityKeyIds = (view: E2EView | null | undefined) => (view ? keyCredentials(view.passkeys, view.credentials) : []);
/** One press on Approve in a locked tab where no passkey that unlocks the keys may sign the answer (high security: only
 *  hardware keys sign then, and none unlocks encryption). */
export const NO_SIGNER_UNLOCKS = "No passkey that unlocks encryption may sign this here: unlock first, then approve.";
/** One press's touch answered with a credential the request did not name, which only a browser that ignores the list
 *  it was given can do: nothing of it is kept. */
export const NOT_ASKED = "This passkey is not one that unlocks encryption here.";
export const UNSTABLE = "This browser and key cannot unlock encryption: the key gave another answer the second time (some security keys do in Safari). Nothing was kept: use another passkey or another browser.";
export const WRONG_CODE = "That is not this account's recovery code.";
export const CODE_FORM = "A recovery code is 32 letters and digits, as it was shown.";
export const ROLLED_BACK = "The keys on the server do not lead back to the newest ones this browser opened: they may be an old copy the server was put back to.";
/** A view classical where this browser saw the account post-quantum. */
export const ROLLED_BACK_CLASSICAL = "This browser saw these keys post-quantum, and the server now serves them classical: the server may have been put back to a copy from before the upgrade. No key is opened from them and no change is made from them, though a change that waits may still be cancelled. Do not unlock here: a copy from before the upgrade is what someone who can write the server's database would serve to read what is sealed with keys a quantum computer could break. If you restored the server from such a backup yourself, clear this site's data in this browser, then unlock and upgrade again. If the upgrade is refused there (a linked session sent its post-quantum statement back in, so these keys are no longer the newest the account stated), turn encryption off and start again: that makes new post-quantum keys, and what was sealed to the classical ones is not read after it.";
/** A classical key where a post-quantum account takes none: a credential, the recovery key, or a roster
 *  naming one. */
export const CLASSICAL_IN_PQ = "These keys are post-quantum, and the server holds a classical key among those that unlock them: it is not used.";
/** A tab holding an account's classical keys, which the server now serves post-quantum: the upgrade took effect since
 *  the tab unlocked, and what the tab holds unlocks nothing of the new keys. */
export const UPGRADED_SINCE = "These keys were made post-quantum since this tab unlocked them (upgraded, or started again elsewhere), so what this tab holds does not open them: unlock again, with a passkey that unlocks them now or with the recovery code (the new one, if a new one was made).";
/** The words while the account's keys are not read yet, where a change needs their form. */
export const STILL_LOADING = "Encryption is still loading: try again in a moment.";
export const ROSTER_CLASSICAL = "The roster of these keys names a classical key, which a post-quantum account holds none of: what was sealed is read in this tab, but no passkey is added to encryption or has its record renewed, and no new master key is made, from these keys.";
/** A server whose start-up probe found no ML-KEM. */
export const NO_MLKEM = "The server cannot use ML-KEM, which post-quantum keys need: it makes none until its cryptography library can.";
/** Refused before anything is asked or touched where the server cannot use ML-KEM and the account's keys are
 *  post-quantum (`ring`, where the change has the keys open, or the view): every change to them, turning encryption off
 *  and renewing anchors too, though they make and check no key. The server refuses each with 503 then:
 *  the recovery code cancels any change, and to a post-quantum key it proves itself by the hybrid proof alone, which
 *  needs ML-KEM, so a change placed then would be one the code could not cancel. A classical account's are refused for
 *  none of this. */
function needsMlkem(ring: Ring | null, view: E2EView): void {
  if ((ring?.pq || view.pq) && !view.mlkem) throw new C.E2EError(NO_MLKEM);
}
export const CODE_REPLACED = "A new recovery code was made, so the code that unlocked this tab opens nothing now: unlock again, with a passkey or the new code.";
export const KEYS_CHANGED = "The keys on the server are not the ones this tab unlocked: wait a moment while it follows them, or unlock again.";
export const BEHIND = "This tab holds keys older than the newest this browser opened: it reads them again first, and makes no change from these.";
export const NOT_NEWEST = "The newest key the account stated is not one these keys hold: the keys the server lists may be an old copy of them, or the server was restored from a backup and a linked session sent its statements in again. No change is made from them; if the server was restored, turn encryption off and start again.";
export const STATED_OTHERWISE = "The server lists a statement of these keys other than it was signed: no change is made from them while it does. Links go by what was signed.";
export const NONE_KEPT = "After this no passkey would unlock encryption, only the recovery code: say that is what you want, or add a passkey to encryption first.";
export const NO_HARDWARE_KEPT = "High security keeps only hardware keys, and none of yours unlocks encryption yet: add one to encryption first.";
export const NONE_TO_SIGN = "No passkey that unlocks encryption and still does after this can make it: add a passkey to encryption first.";
export const NONE_AT_ALL = "No passkey of your account can sign this, even with the recovery code: add a passkey to your account first.";
export const WAITING_CHANGE = "A change waits, and no passkey that may make this one could cancel it, so this one cannot take its place: cancel that one, above, or let it take effect, first.";
export const CODE_NEEDED = "This goes only with the recovery code's proof: no passkey that unlocks encryption and still does after it can make it, or none of them could cancel the change that waits. Give the recovery code first.";
export const ASKED_ANOTHER = "The browser answered with a passkey it was not asked for.";
export const TOUCH_ONE_THAT_MAY = "None of the passkeys touched may confirm this while the change that waits does: touch one of those that may too.";
export const NO_SIGNER = "No passkey that unlocks encryption is left to sign turning it on again. Unlock with the recovery code to read what is sealed, then start again.";
export const UNNAMED = "which the roster of these keys does not name";
/** UNNAMED, naming the passkeys: no new master key is taken without them, and the page seals to none the roster lacks.
 *  Removing them is advised only for passkeys the person does not know: a roster that leaves out one of theirs is also
 *  what a change they did not make leaves, so the roster's word alone never says to remove it. */
export function unnamedWords(names: string[]): string {
  const one = names.length === 1;
  const it = one ? "it" : "them";
  return `The server lists ${names.map((n) => `“${n}”`).join(", ")} as unlocking encryption, ${UNNAMED}: no new master key is taken without ${it}, and this page seals none to a key the roster does not name. If you do not know ${one ? "that passkey" : "those passkeys"}, remove ${it} first, or in the same change. If ${one ? "it is" : "they are"} yours, do not remove ${it} on this word alone: a roster that leaves out a passkey of yours is also what a change you did not make would leave.`;
}

// ---- the newest master key this browser opened ---------------------------------------------------------------------

const FLOOR = "pp-e2e-floor:";
const MK_ID = /^[0-9a-f]{32}$/;

/** What this browser keeps of `account`'s keys, in localStorage, no secret: the newest master key it verified (floor),
 *  and one it made itself since and has not yet seen take effect (next: turning encryption on, or starting again, waits
 *  on a hold, and may be cancelled). And, against an account put back to its classical state:
 *  the master key it first verified post-quantum (pq: the account is post-quantum since that key), and the
 *  seq of the newest statement it saw (seq). */
interface Floor { floor: string | null; next: string | null; pq: string | null; seq: number }

const NO_FLOOR: Floor = { floor: null, next: null, pq: null, seq: 0 };

function readFloor(account: string): Floor {
  try {
    const v = JSON.parse(localStorage.getItem(FLOOR + account) ?? "null") as Partial<Floor> | null;
    const mk = (x: unknown) => (typeof x === "string" && MK_ID.test(x) ? x : null);
    const seq = typeof v?.seq === "number" && Number.isInteger(v.seq) && v.seq > 0 ? v.seq : 0;
    return { floor: mk(v?.floor), next: mk(v?.next), pq: mk(v?.pq), seq };
  } catch {
    return { ...NO_FLOOR };
  }
}

function writeFloor(account: string, f: Floor): void {
  try {
    if (f.floor || f.next || f.pq || f.seq) localStorage.setItem(FLOOR + account, JSON.stringify(f));
    else localStorage.removeItem(FLOOR + account);
  } catch {
    // a browser that keeps nothing (private mode): the anchor alone, as in a fresh one
  }
}

/** The localStorage key of `account`'s floor: a tab listens for it (a `storage` event) to see another tab raise it. */
export const floorKey = (account: string) => FLOOR + account;

/** The master keys the one the server says is current must lead back to, one of them, for `account`: the newest this
 *  browser verified, or keys it made itself since. So a server put back to an older master key (an old copy of the
 *  database, with a passkey's own record from before its anchor was renewed) is refused here. Empty in a browser that
 *  never unlocked this account, which then relies on the anchor alone. */
export function floorOf(account: string): string[] {
  const f = readFloor(account);
  return f.floor ? [f.floor, ...(f.next ? [f.next] : [])] : [];
}

const sameFloor = (a: Floor, b: Floor) => a.floor === b.floor && a.next === b.next && a.pq === b.pq && a.seq === b.seq;

/** The seq of the newest statement `view` lists, 0 for none. */
const newestSeq = (view: E2EView) => view.statements.reduce((n, s) => (typeof s.seq === "number" && s.seq > n ? s.seq : n), 0);

/** Why `view` is older than what this browser saw of `account`, or null: no keys at
 *  all where this browser saw the account's keys (a master key it opened, the account post-quantum, or a statement:
 *  the server put back to a copy from before encryption was turned on, or its keys' rows deleted), in the words for
 *  keys put back, since no key is served to call classical; classical where this browser saw the account post-quantum
 *  (a writer of the database who restores a snapshot from before the upgrade needs no quantum computer to put it back);
 *  or with fewer statements than the newest it saw. A browser that never saw the account trusts the server, as at every
 *  first look, and so does one that kept only keys it made itself, which it never saw take effect (`next` alone: a first
 *  turn-on it cancelled, say, or one that took effect and that no tab of this browser has opened since). */
export function rolledBack(account: string, view: E2EView): string | null {
  const f = readFloor(account);
  if (!hasKeys(view) && (f.floor || f.pq || f.seq)) return ROLLED_BACK;
  if (f.pq && (!view.pq || servesClassical(view))) return ROLLED_BACK_CLASSICAL;
  if (f.seq && newestSeq(view) < f.seq) return ROLLED_BACK;
  return null;
}

/** Whether `view` serves a classical account's keys, judged from their material whatever its `pq` says: a key that
 *  unlocks it (a credential's or the recovery key) or the newest key it stated is not an X-Wing key. A post-quantum
 *  account has none such (its retired classical content keys are kept, but nothing that unlocks it is classical, nor is
 *  its newest key). */
function servesClassical(view: E2EView): boolean {
  const classical = (b64: unknown) => typeof b64 === "string" && C.unb64u(b64)?.length !== C.HYBRID_KEY_LEN;
  return view.credentials.some((c) => classical(c.public_key)) || classical(view.recovery?.public_key) || classical(newestStated(view)?.key);
}

/** The refusal of a view older than this browser saw (rolledBack), in its words. Its class tells it apart from the same
 *  words given where the chain does not lead back to the newest master key this browser opened (ROLLED_BACK, from
 *  openRing's walk): a tab locked by it judges its reason again from each view it reads (e2e.ts), which it cannot do
 *  for the chain without a key. */
export class OlderView extends C.E2EError {}

/** Refuses (rolledBack, as OlderView) a view this browser saw newer: no key is opened from it (an unlock, a tab
 *  following a change) and no change made from it (turning it off, starting again; every other change is made from
 *  keys an unlock opened). A first turn-on is not refused, over no keys either (TurnOn.prepare). Cancelling a change that
 *  waits changes no key, and is not refused. */
function notRolledBack(account: string, view: E2EView): void {
  const why = rolledBack(account, view);
  if (why) throw new OlderView(why);
}

/** A change to the floor, read and written while no other tab of this origin changes it (the Web Locks API, one lock
 *  per account, which every browser that does X25519 has), so two tabs at once never lower it. Without it, compare and
 *  set: written only over what was read, and made again from what another tab wrote meanwhile. */
async function changeFloor(account: string, change: (f: Floor) => Floor | null): Promise<void> {
  const once = () => {
    for (let tries = 0; tries < 4; tries++) {
      const read = readFloor(account);
      const next = change(read);
      if (!next) return;
      if (!sameFloor(readFloor(account), read)) continue;          // another tab wrote it meanwhile: again, from that
      writeFloor(account, next);
      return;
    }
  };
  const locks = typeof navigator === "undefined" ? undefined : (navigator as Navigator & { locks?: LockManager }).locks;
  if (locks?.request) await locks.request(FLOOR + account, once);
  else once();
}

/** After an unlock, or a tab following a change: the floor raised to the ring's master key, which was verified to lead
 *  back to it; never lowered, should another tab have raised it past this one meanwhile. Keys this browser made are no
 *  longer waited for once the ring leads back to them. The account kept as post-quantum since the first master key
 *  this browser verified it post-quantum, and the newest statement seen raised to the ring's. */
export function raiseFloor(account: string, ring: Ring): Promise<void> {
  return changeFloor(account, (f) => {
    const reached = !!f.next && ring.masters.has(f.next);
    if (f.floor && !ring.masters.has(f.floor) && !reached) return null;
    return { floor: ring.master.id, next: reached ? null : f.next, pq: f.pq ?? (ring.pq ? ring.master.id : null),
      seq: Math.max(f.seq, ring.seq) };
  });
}

/** Keys this browser made (turning encryption on, or starting again): trusted once they take effect, though they do
 *  not lead back to the floor. */
export function expectKeys(account: string, mkId: string): Promise<void> {
  return changeFloor(account, (f) => ({ ...f, next: mkId }));
}

/** The floor dropped, at the person's word: they trust the keys the server holds now (they started encryption again,
 *  or restored the server from a backup themselves). What this browser saw of the account post-quantum is kept: that
 *  word is asked for keys that do not lead back to the newest it opened, and takes no copy from before the upgrade
 *  (ROLLED_BACK_CLASSICAL is asked no such word). */
export function dropFloor(account: string): Promise<void> {
  return changeFloor(account, (f) => ({ ...NO_FLOOR, pq: f.pq }));
}

/** Whether `ring` reaches this browser's floor: holds its master key, or keys this browser made. A tab whose keys are
 *  older (another tab followed a change, or opened newer keys) makes no change from them: a server that serves it the
 *  old rows would have it sign on the account's chain under a master key a passkey removed since still opens. */
export function atFloor(ring: Ring): boolean {
  const floor = floorOf(ring.account);
  return !floor.length || floor.some((m) => ring.masters.has(m));
}

/** Refuses (BEHIND) a change made from a ring below this browser's floor: called before it is prepared, and again,
 *  with nothing awaited, before it is signed. */
function aboveFloor(ring: Ring): void {
  if (!atFloor(ring)) throw new C.E2EError(BEHIND);
}

/** Refuses (NOT_NEWEST) a statement prepared from a ring that does not hold the key the newest e2e-key statement served
 *  names, the keys the server lists being the ones it opened (stillCurrent): what it signed would follow that statement
 *  on the account's chain under keys older than it. The server serves old rows of the keys with the account's
 *  statements (to this tab alone, in a browser whose floor never saw newer keys), or a linked session sent statements in
 *  after a restore whose keys the backup lacks. Read from the payloads, as signed and as links obey them, never from
 *  the columns served beside them, which a server with old key rows could set to a key the ring holds. A newest key the
 *  ring lists as unopened is refused the same (NEWEST_UNOPENED says so): whoever holds the master key can store a
 *  key that does not open, and so can a server with old key rows, putting a row for the newest key back among them,
 *  and the page cannot tell the two apart. */
function holdsNewestKey(ring: Ring, view: E2EView): void {
  const newest = newestStated(view);
  if (newest && (typeof newest.key_id !== "string" || !ring.keys.has(newest.key_id))) {
    throw new C.E2EError(newestUnopened(ring, view) ? NEWEST_UNOPENED : NOT_NEWEST);
  }
}

/** The payload of the newest e2e-key statement served, as signed: the key links seal to. */
const newestStated = (view: E2EView) => view.statements.map(payloadOf).reverse().find((p) => p.purpose === "e2e-key");

/** Whether the key links seal to, the newest the account stated, is one `ring` lists as unopened (NEWEST_UNOPENED). */
export function newestUnopened(ring: Ring, view: E2EView): boolean {
  const newest = newestStated(view);
  return !!newest && typeof newest.key_id === "string" && ring.unopened.includes(newest.key_id);
}

/** Refuses (STATED_OTHERWISE) a change made from keys whose statements the server lists other than they were signed
 *  (misstated): what it would check the change against is not what links go by. */
function statedAsSigned(view: E2EView): void {
  if (view.misstated.length) throw new C.E2EError(STATED_OTHERWISE);
}

// ---- the passkeys this browser removed -------------------------------------------------------------------------------

const REMOVED = "pp-e2e-removed:";
const REMOVED_KEPT = 64;

/** A passkey this browser removed from the account, as it was listed then: its id, its credential's id, and the
 *  public key of its key for encryption (none when it unlocked nothing). No secret. */
export interface Removed { passkey_id: string; credential_id: string; public_key: string | null }

/** The passkeys this browser removed from `account` (rememberRemoved), newest last. */
export function removedHere(account: string): Removed[] {
  try {
    const v = JSON.parse(localStorage.getItem(REMOVED + account) ?? "[]") as unknown;
    const text = (x: unknown) => (typeof x === "string" ? x : "");
    return (Array.isArray(v) ? v : []).flatMap((r) => (r && typeof r === "object" && text((r as Removed).passkey_id)
      ? [{ passkey_id: text((r as Removed).passkey_id), credential_id: text((r as Removed).credential_id), public_key: text((r as Removed).public_key) || null }]
      : []));
  } catch {
    return [];
  }
}

/** After this browser removed a passkey from the account (Account's Remove, while encryption is off and keeps its
 *  keys): it is remembered with what `view` lists of it, so it leaves the next master key (rosterAfter) whatever the
 *  server lists later, as a server that lists it again, enrolled, would keep the master key it holds current. */
export function rememberRemoved(account: string, view: E2EView | null, passkeyId: string): void {
  if (!passkeyId) return;
  const credential = view?.credentials.find((c) => c.passkey_id === passkeyId);
  const entry: Removed = { passkey_id: passkeyId, credential_id: credential?.credential_id ?? view?.passkeys.find((p) => p.passkey_id === passkeyId)?.credential_id ?? "",
    public_key: credential?.public_key ?? null };
  const list = [...removedHere(account).filter((r) => r.passkey_id !== passkeyId), entry].slice(-REMOVED_KEPT);
  try {
    localStorage.setItem(REMOVED + account, JSON.stringify(list));
  } catch {
    // a browser that keeps nothing: the server's word alone, as in a fresh one
  }
}

/** Whether a credential is one of a passkey this browser removed: by its passkey, its credential, or its key. */
const wasRemoved = (removed: Removed[], c: { passkey_id: string; credential_id: string; public_key: string }) =>
  removed.some((r) => r.passkey_id === c.passkey_id || (!!r.credential_id && r.credential_id === c.credential_id)
    || (!!r.public_key && r.public_key === c.public_key));

/** A statement's life, which bounds only the server's taking it (60 to 600 seconds). */
export const STATEMENT_LIFE = 600;

// ---- the ring: the keys unlocked in this tab --------------------------------------------------------------------------

/** A private key held in the tab: a classical X25519 key (never extractable), or a post-quantum (X-Wing) key held by
 *  e2eCrypto (C.Hybrid: its X25519 half never extractable, its ML-KEM half bytes the library takes, zeroed at lock).
 *  Which it is follows from its public key's length (32 or 1,216 bytes), never from a choice. */
export type Priv = { pq: false; key: CryptoKey } | { pq: true; key: C.Hybrid };

const classicalPriv = (key: CryptoKey): Priv => ({ pq: false, key });
const hybridPriv = (key: C.Hybrid): Priv => ({ pq: true, key });

/** A grant to `priv` (whose public key is `pub`) opened: the master key's 32 bytes, which the caller zeroes. A grant's
 *  form is its recipient's: form 1 opens only under a classical key and form 2 only under a post-quantum one, so a
 *  hybrid grant is never tried with X25519 alone. */
function openGrantBy(priv: Priv, pub: Uint8Array, sealed: string, account: string, to: string, mkId: string): Promise<C.Bytes> {
  return priv.pq ? C.openGrantHybrid(priv.key, sealed, account, to, mkId) : C.openGrant(priv.key, pub, sealed, account, to, mkId);
}

/** A post-quantum key's ML-KEM half put away (at each lock, and when a key made for a moment is done with); nothing for
 *  a classical key, which WebCrypto holds. */
export function forgetPriv(priv: Priv | null | undefined): void {
  if (priv?.pq) C.forgetHybrid(priv.key);
}

/** What unlocked the keys, kept so the tab follows a revocation made elsewhere without a touch: a credential's private
 *  key or the recovery key (Priv), each with its anchor; for the recovery code, also the key its anchor is sealed under
 *  (anchorKey, never extractable), so the tab renews that anchor without the code typed again. */
export type Via =
  | { kind: "credential"; credentialId: string; passkeyId: string; ck: Priv; CK: C.Bytes; anchor: string }
  | { kind: "recovery"; rk: Priv; RK: C.Bytes; anchor: string; anchorKey: CryptoKey };

export interface Ring {
  account: string;
  /** whether the account was post-quantum as the ring was opened (the view's pq), and the newest statement's seq then */
  pq: boolean;
  seq: number;
  via: Via;
  master: C.Master;                       // the current master key
  masters: Map<string, C.Master>;         // the master keys from it back to the anchor (and the floor), by mk_id
  /** the roster, opened and naming the key that unlocked the tab; null when it does not open, or does not name that
   *  key (rosterWhy says which): the tab reads, and makes no change that seals to the roster or rewrites it (rosterOf) */
  roster: C.Roster | null;
  rosterWhy: string;
  rosterSealed: string | null;            // the roster the server listed, sealed: to see whether it changed since
  keys: Map<string, { ek: Priv; EK: C.Bytes }>;   // the content keys, by key_id
  /** content keys the server lists under a master key off that path: none this page can trust, so none opened */
  unreached: string[];
  /** content keys the server lists under a master key on that path that do not open to the key they name: listed, so
   *  one such row never stops the tab reading what the others open */
  unopened: string[];
}

/** A grant's `to` for the recovery key. */
const RECOVERY = "recovery";
const viaTo = (via: Via) => (via.kind === "credential" ? via.credentialId : RECOVERY);
const viaKey = (via: Via) => (via.kind === "credential" ? { priv: via.ck, pub: via.CK } : { priv: via.rk, pub: via.RK });

/** Before anything is sent: a grant just sealed opens with the key it is sealed to, to the master key it names. */
async function holdsMaster(priv: Priv, pub: Uint8Array, sealed: string, account: string, to: string, mkId: string): Promise<void> {
  let bytes: C.Bytes;
  try {
    bytes = await openGrantBy(priv, pub, sealed, account, to, mkId);
  } catch {
    throw new C.E2EError("A grant just made does not open with the key it is sealed to.");
  }
  try {
    if ((await C.masterOf(bytes, account)).id !== mkId) throw new C.E2EError("A grant just made does not hold the master key it names.");
  } finally {
    C.zero(bytes);
  }
}

async function opensTo(via: Via, grant: E2EGrant, account: string): Promise<C.Master | null> {
  const { priv, pub } = viaKey(via);
  let bytes: C.Bytes;
  try {
    bytes = await openGrantBy(priv, pub, grant.sealed, account, viaTo(via), grant.mk_id);
  } catch {
    return null;
  }
  try {
    const m = await C.masterOf(bytes, account);
    return m.id === grant.mk_id ? m : null;
  } catch {
    return null;
  } finally {
    C.zero(bytes);
  }
}

/** A walk back along the chain from the current master key: the keys passed, by mk_id, the links used, and where it is. */
interface Walk { path: Map<string, C.Master>; used: Set<E2EChainLink>; at: C.Master }

/** The walk carried on until it has passed one of `targets`: each link opened with the chain subkey of the key after it,
 *  each older key's mk_id computed and matched. E2EError(`refusal`) when the chain does not reach one; a key replaced
 *  twice is a fork, refused the same. */
async function walkTo(view: E2EView, account: string, w: Walk, targets: string[], refusal: string): Promise<void> {
  while (!targets.some((t) => w.path.has(t))) {
    const links = view.chain.filter((l) => l.mk_id === w.at.id);
    if (links.length !== 1 || w.used.size > view.chain.length) throw new C.E2EError(refusal);
    const link = links[0];
    let older: C.Bytes;
    try {
      older = await C.openChain(w.at, link.wrapped, link.previous, account);
    } catch {
      throw new C.E2EError(refusal);
    }
    let m: C.Master;
    try {
      m = await C.masterOf(older, account);
    } finally {
      C.zero(older);
    }
    if (m.id !== link.previous || w.path.has(m.id)) throw new C.E2EError(refusal);
    w.path.set(m.id, m);
    w.used.add(link);
    w.at = m;
  }
}

/** A content key the server lists under `m`, a master key on the tab's path, opened: its key_id that of the public key
 *  it names, and its wrap holding that key, in the form its account keeps it (`pq`, a post-quantum account's): a
 *  post-quantum key (1,216 bytes) as its seed's 60-byte wrap, never in a classical account; a classical key (32 bytes)
 *  in a post-quantum account only as a retired key's 60-byte scalar wrap, and in a classical account as the classical
 *  wrap of its PKCS#8. Null when any of it fails. */
async function openContentKey(m: C.Master, k: E2EContentKey, account: string, pq: boolean): Promise<{ ek: Priv; EK: C.Bytes } | null> {
  try {
    const EK = C.unb64u(k.public_key);
    if (!EK || k.key_id !== (await C.keyIdOf(EK))) return null;
    if (EK.length === C.HYBRID_KEY_LEN) {
      // unwrapContentSeed checks the seed's key is the one the key_id names
      return pq ? { ek: hybridPriv(await C.unwrapContentSeed(m, k.wrapped, k.key_id, account)), EK } : null;
    }
    const ek = pq ? await C.unwrapClassicalScalar(m, k.wrapped, k.key_id, account) : await C.unwrapContentKey(m, k.wrapped, k.key_id, account);
    return C.same(await C.publicOf(ek), EK) ? { ek: classicalPriv(ek), EK } : null;
  } catch {
    return null;
  }
}

/** Steps 2 to 4 of unlocking, from what step 1 opened: the grant of the master key the server says is
 *  current, opened and its mk_id computed; the chain walked back to the anchor, and on to one key of each of `floors`
 *  (the newest master keys this browser and this tab opened before: one the server put back to before them is refused,
 *  which the anchor alone does not see when a passkey's own record from before is put back too); no second key on the
 *  way (a fork); the roster opened and naming this key; every content key opened that is under a key on the way, and
 *  those under a key off it listed as unreached. A tab unlocked with a recovery code since replaced is told so. What
 *  the server stores sealed it cannot check, so a roster or a content key that does not open is not the end of the
 *  unlock (else one bad row would stop every tab reading anything): a content key under a key on the way
 *  that does not open is listed as unopened, and a roster that does not open, or does not name this key, leaves the
 *  ring without one (rosterWhy), and no change that seals to it or rewrites it is made (rosterOf). Reading needs none of
 *  them: what opens is under a key on the way. */
export async function openRing(view: E2EView, account: string, via: Via, floors: string[][] = []): Promise<Ring> {
  // the view is older than nothing this browser saw: not classical where it saw post-quantum keys, nor
  // with fewer statements. Asked whatever floor is given: what it saw post-quantum outlives a floor the person dropped
  // (dropFloor), and a browser that saw nothing is refused nothing
  notRolledBack(account, view);
  // a post-quantum account opens with a post-quantum key alone, and a classical one with a classical key
  // (step 1 checks the key that unlocks against the view's form, so a classical key reaches here with a post-quantum
  // view only when a tab follows keys made post-quantum since it unlocked: an upgrade, or a start again elsewhere)
  if (via.kind === "credential" ? via.ck.pq !== view.pq : via.rk.pq !== view.pq) throw new C.E2EError(view.pq ? UPGRADED_SINCE : NOT_LEAD_BACK);
  if (via.kind === "recovery" && view.recovery && view.recovery.public_key !== C.b64u(via.RK)) throw new C.E2EError(CODE_REPLACED);
  const current = view.mk_id;
  const to = viaTo(via);
  const grant = current ? view.grants.find((g) => g.mk_id === current && g.to === to) : undefined;
  if (!current || !grant) throw new C.E2EError(NO_GRANT);
  const master = await opensTo(via, grant, account);
  if (!master) throw new C.E2EError(GRANT_NOT_OPEN);
  const w: Walk = { path: new Map([[master.id, master]]), used: new Set(), at: master };
  await walkTo(view, account, w, [via.anchor], NOT_LEAD_BACK);
  for (const floor of floors) if (floor.length) await walkTo(view, account, w, floor, ROLLED_BACK);
  const { path, used } = w;
  // a fork: a key on the way back that a second key replaced. A second key reaches the anchor only through such a link
  // (or it is the anchor), so this finds every one the server serves; links older than the way back are no matter
  if (view.chain.some((l) => !used.has(l) && path.has(l.previous))) throw new C.E2EError(NOT_LEAD_BACK);
  const row = view.roster.find((r) => r.mk_id === master.id);
  let roster: C.Roster | null = null;
  try {
    roster = row ? await C.openRoster(master, row.sealed, account) : null;
  } catch {
    roster = null;
  }
  let rosterWhy = roster ? "" : ROSTER_NOT_OPEN;
  // a post-quantum account's roster names post-quantum keys alone: one that names a
  // classical key for a credential or the recovery key is not sealed to, nor rewritten
  const hybridKey = (k: string) => C.unb64u(k)?.length === C.HYBRID_KEY_LEN;
  if (roster && view.pq && !(hybridKey(roster.recovery) && roster.credentials.every((c) => hybridKey(c.public_key)))) {
    roster = null;
    rosterWhy = ROSTER_CLASSICAL;
  }
  const listed = !roster ? false : via.kind === "credential"
    ? roster.credentials.some((c) => c.credential_id === via.credentialId && c.public_key === C.b64u(via.CK))
    : roster.recovery === C.b64u(via.RK);
  if (roster && !listed) {
    roster = null;
    rosterWhy = ROSTER_LACKS_THIS;
  }
  const keys = new Map<string, { ek: Priv; EK: C.Bytes }>();
  const unreached: string[] = [];
  const unopened: string[] = [];
  for (const k of view.content_keys) {
    const m = path.get(k.mk_id);
    if (!m) {
      unreached.push(k.key_id);                         // under a key off the path: none this page can trust
      continue;
    }
    const opened = await openContentKey(m, k, account, view.pq);
    if (opened) keys.set(k.key_id, opened);
    else unopened.push(k.key_id);
  }
  return { account, pq: view.pq, seq: newestSeq(view), via, master, masters: path, roster, rosterWhy,
    rosterSealed: row?.sealed ?? null, keys, unreached, unopened };
}

/** The roster `ring` opened, naming the key that unlocked it, for a change that seals to it or rewrites it: refused with
 *  the reason (rosterWhy) when the ring has none. */
function rosterOf(ring: Ring): C.Roster {
  if (!ring.roster) throw new C.E2EError(ring.rosterWhy || ROSTER_NOT_OPEN);
  return ring.roster;
}

/** Whether the server's keys are still the ones `ring` was opened from: its master key current, its roster unchanged,
 *  and the content keys listed the ones it opened, and those it listed as unreached (under a master key off its path)
 *  or unopened (under one on it, not opening), which a new master key leaves as they are: abandon. A change made from
 *  a ring that is not would undo what was made since (a passkey added, a key rotated): KEYS_CHANGED until the tab has
 *  followed them. */
function stillCurrent(ring: Ring, view: E2EView): void {
  const listed = view.content_keys.map((k) => k.key_id);
  const known = new Set([...ring.keys.keys(), ...ring.unreached, ...ring.unopened]);
  if (view.mk_id !== ring.master.id || (view.roster.find((r) => r.mk_id === ring.master.id)?.sealed ?? null) !== ring.rosterSealed
      || listed.length !== known.size || !listed.every((id) => known.has(id))) {
    throw new C.E2EError(KEYS_CHANGED);
  }
}

/** Every check before a change is prepared from `ring`: at this browser's floor (aboveFloor), the server's keys the ones
 *  it was opened from (stillCurrent), their statements listed as they were signed (statedAsSigned), and, for one that
 *  signs a statement, holding the newest key stated (holdsNewestKey). */
function checkCurrent(ring: Ring, view: E2EView, statement = false): void {
  aboveFloor(ring);
  stillCurrent(ring, view);
  statedAsSigned(view);
  if (statement) holdsNewestKey(ring, view);
}

/** A tab following a change made elsewhere (a rotation, a revocation), with the key it holds and no touch: the master
 *  key the server says is current must lead back to the one the tab holds (no older key put back in its place), and to
 *  one of `floor` (this browser's, floorOf). */
export const follow = (ring: Ring, view: E2EView, floor: string[] = []) =>
  openRing(view, ring.account, ring.via, [[ring.master.id], floor]);

/** Every post-quantum key `ring` holds put away (forgetPriv): its content keys', and with `via`, what unlocked it, at a
 *  lock. A tab that follows a change forgets the old ring's content keys alone, since the new ring keeps its `via`. */
export function forgetRing(ring: Ring | null | undefined, via = true): void {
  if (!ring) return;
  for (const k of ring.keys.values()) forgetPriv(k.ek);
  if (via) forgetPriv(ring.via.kind === "credential" ? ring.via.ck : ring.via.rk);
}

// ---- ceremonies ----------------------------------------------------------------------------------------------------

/** A touch's PRF output (`first`, or `second` where a second salt was asked), as a view of the buffer the browser gave,
 *  so zeroing it zeroes that. */
function prfOf(results: AuthenticationExtensionsClientOutputs, which: "first" | "second"): C.Bytes | null {
  const out = results.prf?.results?.[which];
  if (!out) return null;
  return (ArrayBuffer.isView(out) ? new Uint8Array(out.buffer as ArrayBuffer, out.byteOffset, out.byteLength) : new Uint8Array(out as ArrayBuffer)) as C.Bytes;
}

/** A touch: which credential, its PRF output for its salt, and for a second salt where one was asked, and the browser's
 *  word on the authenticator that answered (passkey.assertionWith). */
export interface Touch { credentialId: string; prf: C.Bytes; second: C.Bytes | null; credential: AssertionAnswer; attachment: string | null }

/** One touch of one of the allowed credentials, evaluating each one's PRF salt and, where given, a second salt
 *  (evalByCredential, keys in base64url without padding, allowCredentials exactly those), with user verification
 *  required, over `challenge`: a statement's, one derived from the server's, or the page's own (passkey.ts). A second
 *  output asked for and not given refuses the touch, unless `secondOptional` (an unlock, the passkey check), where its
 *  second is then null. No output refuses it, in words for what the touch was for (`doing`: "add", a passkey added to
 *  encryption or turning it on; otherwise unlocking) and for what answered (a security key, by the browser's word, is
 *  told to use another browser): `doing`, what it was for (TouchFor), and `keys`, the credentials that are security keys by
 *  what the server keeps of them (securityKeyIds), never by the browser's word. Call it first thing in a click's handler:
 *  nothing is awaited before the prompt. */
export async function prfTouch(o: { challenge: Challenge; rpId?: string; allow: { credentialId: string; salt: Uint8Array; second?: Uint8Array }[];
                                    secondOptional?: boolean; doing?: TouchFor; keys?: readonly string[] }): Promise<Touch> {
  const evalByCredential = Object.fromEntries(o.allow.map((a) => [a.credentialId,
    a.second ? { first: a.salt as C.Bytes, second: a.second as C.Bytes } : { first: a.salt as C.Bytes }]));
  const { credential, results, attachment } = await assertionWith({
    challenge: o.challenge, rpId: o.rpId, allowCredentials: o.allow.map((a) => ({ type: "public-key" as const, id: a.credentialId })),
  }, { prf: { evalByCredential } });
  const prf = prfOf(results, "first");
  const second = prfOf(results, "second");
  const asked = o.allow.find((a) => a.credentialId === credential.id);
  if (!prf || !asked || (asked.second && !second && !o.secondOptional)) {
    C.zero(prf, second);
    throw new C.E2EError(!asked ? "The browser answered with a passkey it was not asked for."
      : noPrfWords(o.doing, (o.keys ?? []).includes(credential.id)));
  }
  if (!asked.second) C.zero(second);                    // one not asked for is not kept
  return { credentialId: credential.id, prf, second: asked.second ? second : null, credential, attachment };
}

/** WebAuthn's hash of a PRF input, sha256("WebAuthn PRF" 0 input), which the browser takes of each input before the
 *  authenticator's hmac-secret does (WebAuthn level 3, the prf extension). */
export const prfInputHash = (input: Uint8Array) => C.sha256(C.concat(C.utf8("WebAuthn PRF"), new Uint8Array([0]), input));

/** Each credential's salt as prfInputHash hashes it, by the salt's base64url: worked out as a view is read
 *  (primeUnlock, from E2EApi.view), since nothing may be awaited before an unlock's touch, and forgotten at a reset
 *  (forgetUnlockHashes). The salts are the server's and are not secret. */
const hashedSalts = new Map<string, C.Bytes>();

/** Works out, ahead of an unlock's touch, the hash of each salt `view` lists that is not worked out yet. Best effort:
 *  a salt that cannot be hashed here is left out, and the unlock asks that credential for its salt alone. */
export async function primeUnlock(view: E2EView): Promise<void> {
  for (const c of view.credentials) {
    const salt = C.unb64u(c.salt);
    if (!salt || hashedSalts.has(c.salt)) continue;
    try {
      hashedSalts.set(c.salt, await prfInputHash(salt));
    } catch {
      // not hashed: the unlock asks the salt alone (unlockAllowed)
    }
  }
}

/** Forgets every salt's hash primeUnlock worked out: a sign-out, or another account (useE2EStore.reset). */
export function forgetUnlockHashes(): void {
  hashedSalts.clear();
}

/** The credentials one touch may unlock with: every enrolled one, each with its salt and, as its second, that salt's
 *  hash where primeUnlock has worked it out. A passkey reached by one path may hash each PRF input once more than
 *  reached by another (a phone's passkey from another device's browser
 *  over its QR code, and in the phone's own browser), so that the path that hashes more gives for the salt what the
 *  other gives for the salt's hash. The second output therefore opens, where the first does not (viaFromPrf), a record
 *  made on a path that hashes once more than this one; a record made on a path that hashes once less it does not open,
 *  which would take a preimage of the hash (the passkey check, below, tells the two apart). */
export function unlockAllowed(view: E2EView): { credentialId: string; salt: C.Bytes; second?: C.Bytes }[] {
  return view.credentials.flatMap((c) => {
    const salt = C.unb64u(c.salt);
    const second = hashedSalts.get(c.salt);
    return salt ? [{ credentialId: c.credential_id, salt, ...(second ? { second: second.slice() } : {}) }] : [];
  });
}

/** Step 1 of unlocking with a passkey's PRF output (zeroed once used): the credential's own wrap opened, giving its
 *  private key and its anchor, and the key checked against the public key the server lists for it. Where the output
 *  does not open the wrap (OWN_RECORD) and the touch gave `alt`, its output for the salt's hash (unlockAllowed), that
 *  is tried in its place; `alt` is zeroed whatever happens. */
export async function viaFromPrf(view: E2EView, account: string, credentialId: string, prf: C.Bytes, alt: C.Bytes | null = null): Promise<Via> {
  try {
    const via = await viaFromOutput(view, account, credentialId, prf);
    C.zero(alt);
    return via;
  } catch (e) {
    if (!alt || !(e instanceof C.E2EError && e.message === OWN_RECORD)) {
      C.zero(alt);
      throw e;
    }
  }
  return viaFromOutput(view, account, credentialId, alt);
}

/** viaFromPrf with one output (zeroed once used). */
async function viaFromOutput(view: E2EView, account: string, credentialId: string, prf: C.Bytes): Promise<Via> {
  const row = view.credentials.find((c) => c.credential_id === credentialId);
  if (!row) {
    C.zero(prf);
    throw new C.E2EError("This passkey is not one that unlocks encryption.");
  }
  const listed = C.unb64u(row.public_key);
  const hybrid = listed?.length === C.HYBRID_KEY_LEN;
  if (hybrid !== view.pq) {
    C.zero(prf);
    throw new C.E2EError(view.pq ? CLASSICAL_IN_PQ : NOT_LEAD_BACK);
  }
  const kek = await C.credentialKek(prf, account, credentialId);
  if (hybrid) {
    // a post-quantum credential: its seed's 60-byte wrap, its anchor stored beside it and bound by its AAD, and the key
    // the seed makes, both halves, the one the server lists (openRing then checks the roster names that one)
    const anchor = row.anchor;
    if (typeof anchor !== "string" || !MK_ID.test(anchor)) throw new C.E2EError(OWN_RECORD);
    let key: C.Hybrid;
    try {
      key = await C.unwrapCredentialSeed(kek, row.wrapped, anchor, account, credentialId, listed!);
    } catch (e) {
      throw new C.E2EError(e instanceof C.E2EError && e.message === C.OTHER_RECORD ? NOT_LEAD_BACK : OWN_RECORD);
    }
    return { kind: "credential", credentialId, passkeyId: row.passkey_id, ck: hybridPriv(key), CK: listed!, anchor };
  }
  let opened: { ck: CryptoKey; anchor: string };
  try {
    opened = await C.openCredential(kek, row.wrapped, account, credentialId);
  } catch {
    throw new C.E2EError(OWN_RECORD);
  }
  const CK = await C.publicOf(opened.ck);
  if (C.b64u(CK) !== row.public_key) throw new C.E2EError(NOT_LEAD_BACK);
  return { kind: "credential", credentialId, passkeyId: row.passkey_id, ck: classicalPriv(opened.ck), CK, anchor: opened.anchor };
}

/** Unlocking with a passkey's PRF output: step 1, then openRing, the current master key leading back to one of `floor`
 *  (this browser's, floorOf) where it has one. A view older than what this browser saw (rolledBack) is refused first,
 *  floor or none: after the person dropped the floor, what it saw post-quantum is still kept. `alt`, the touch's output
 *  for the salt's hash, is tried where `prf` does not open the credential's wrap (viaFromPrf). */
export async function unlockWithPrf(view: E2EView, account: string, credentialId: string, prf: C.Bytes, floor: string[] = [],
                                    alt: C.Bytes | null = null): Promise<Ring> {
  try {
    notRolledBack(account, view);                     // before any key is opened from it
  } catch (e) {
    C.zero(prf, alt);
    throw e;
  }
  return openMade(view, account, await viaFromPrf(view, account, credentialId, prf, alt), floor);
}

/** The ring opened from what an unlock just made (`via`, its own and no one else's): what it made is put away when the
 *  ring does not open. */
async function openMade(view: E2EView, account: string, via: Via, floor: string[]): Promise<Ring> {
  try {
    return await openRing(view, account, via, [floor]);
  } catch (e) {
    forgetPriv(via.kind === "credential" ? via.ck : via.rk);
    throw e;
  }
}

/** One touch of any enrolled credential, then unlockWithPrf. Call it first thing in a click's handler. */
export async function unlockWithTouch(view: E2EView, account: string, floor: string[] = []): Promise<Ring> {
  const allow = unlockAllowed(view);
  if (!allow.length) throw new C.E2EError("No passkey of this account unlocks encryption: use the recovery code.");
  const t = await prfTouch({ challenge: pageChallenge(), allow, secondOptional: true, keys: securityKeyIds(view) });
  return unlockWithPrf(view, account, t.credentialId, t.prf, floor, t.second);
}

// ---- one press: a signature and an unlock in one touch -------------------------------------------------------------

/** The credentials one press may touch while the keys are locked: those that unlock them (unlockAllowed) among those
 *  that may sign the step (`signers`, a prepared step's allow: none named, any of the account's passkeys may). */
export function approveAllowed(view: E2EView | null, signers: string[]): { credentialId: string; salt: C.Bytes; second?: C.Bytes }[] {
  if (!view) return [];
  const all = unlockAllowed(view);
  return signers.length ? all.filter((a) => signers.includes(a.credentialId)) : all;
}

/** One press's one touch: a passkey that unlocks the keys signs the prepared step `p` over its challenge, so the server
 *  takes it as signed, and gives, in the same touch, its PRF outputs for its salt and that salt's hash, which unlock
 *  the keys (unlockAllowed, viaFromPrf). The assertion, and the touch, or null where the passkey gave no PRF output
 *  (then nothing unlocks, and the caller sends nothing, saying `noPrf`, the words for what answered); NOT_ASKED where the browser answered with a credential it was
 *  not asked for. Call it first thing in a click's handler: nothing is awaited before the browser's prompt. */
export async function signAndTouch(p: { challenge: Challenge<"step">; rpId: string; allow: string[] }, view: E2EView | null):
    Promise<{ credential: AssertionAnswer; touch: Touch | null; noPrf?: string }> {
  const allow = approveAllowed(view, p.allow);
  if (!allow.length) throw new C.E2EError(NO_SIGNER_UNLOCKS);
  const evalByCredential = Object.fromEntries(allow.map((a) => [a.credentialId,
    a.second ? { first: a.salt as C.Bytes, second: a.second as C.Bytes } : { first: a.salt as C.Bytes }]));
  const { credential, results, attachment } = await assertionWith({
    challenge: p.challenge, rpId: p.rpId, allowCredentials: allow.map((a) => ({ type: "public-key" as const, id: a.credentialId })),
  }, { prf: { evalByCredential } });
  const prf = prfOf(results, "first");
  const second = prfOf(results, "second");
  const asked = allow.find((a) => a.credentialId === credential.id);
  if (!asked) {
    C.zero(prf, second);
    throw new C.E2EError(NOT_ASKED);
  }
  if (!prf) {
    C.zero(second);
    // its words go by what the server keeps of the passkey that answered (noPrfWords, securityKeyIds)
    return { credential, touch: null, noPrf: noPrfWords("unlock", securityKeyIds(view).includes(credential.id)) };
  }
  if (!asked.second) C.zero(second);                    // one not asked for is not kept
  return { credential, touch: { credentialId: credential.id, prf, second: asked.second ? second : null, credential, attachment } };
}

// ---- the passkey check ----------------------------------------------------------------------------------------------

/** The passkey check's input: sha256("proofpane-e2e/1 passkey check"), a salt no record is made with and the same on
 *  every device, so what a passkey gives for it may be shown and compared. */
export const CHECK_SALT_HEX = "b6b79d35db8d273e0055dd1559ed95d176bc711ac3b971567220daa02802fb7d";
/** The check's input as prfInputHash hashes it: what one path gives for the input, a path that hashes each input once
 *  less gives for this. */
export const CHECK_SALT_HASHED_HEX = "f6c22e92d09efc6e4413ac5899fd1bdce3f19a4a2151d489cb9a3d363db5af6e";

/** What the passkey check found: the passkey that answered, by its name, and how many were offered; the browser's word
 *  on the authenticator that answered; whether the authenticator says it verified the person (its flags' UV bit); and A
 *  and B, the first six bytes in hex of the sha256 of what it gave for the check's input and for that input's hash (B
 *  null where it gave the first output alone), with the outputs' lengths. */
export interface PasskeyCheck { credentialId: string; name: string; offered: number; attachment: string | null; verified: boolean; a: string; b: string | null; lengths: number[] }

const unhex = (h: string) => new Uint8Array((h.match(/../g) ?? []).map((x) => parseInt(x, 16)));

/** The passkey check: one touch of any enrolled credential for the check's input and its hash (prfTouch, user
 *  verification required), each output shown only as the start of its sha256 and then zeroed; nothing is sent to the
 *  server and no key is opened. A passkey gives the same A and B on every path that hashes its inputs as many times;
 *  where A on one path is B on another, the first hashes once more. Call it first thing in a click's handler. */
export async function passkeyCheck(view: E2EView): Promise<PasskeyCheck> {
  const salt = unhex(CHECK_SALT_HEX), second = unhex(CHECK_SALT_HASHED_HEX);
  const allow = view.credentials.map((c) => ({ credentialId: c.credential_id, salt, second }));
  if (!allow.length) throw new C.E2EError("No passkey of this account unlocks encryption, so none can be checked.");
  const t = await prfTouch({ challenge: pageChallenge(), allow, secondOptional: true, keys: securityKeyIds(view) });
  try {
    const start = async (b: C.Bytes) => C.hex((await C.sha256(b)).slice(0, 6));
    const flags = C.unb64u(t.credential.response.authenticatorData)?.[32] ?? 0;
    const row = view.credentials.find((c) => c.credential_id === t.credentialId);
    return {
      credentialId: t.credentialId, name: view.passkeys.find((p) => p.passkey_id === row?.passkey_id)?.name ?? "a passkey",
      offered: allow.length, attachment: t.attachment, verified: (flags & 0x04) !== 0,
      a: await start(t.prf), b: t.second ? await start(t.second) : null,
      lengths: t.second ? [t.prf.length, t.second.length] : [t.prf.length],
    };
  } finally {
    C.zero(t.prf, t.second);
  }
}

/** Step 1 of unlocking with the recovery code: rk derived from it and checked against RK, and the recovery anchor
 *  opened with the code's anchor key, which is kept (never extractable) to renew that anchor (RenewAnchors). */
export async function viaFromCode(view: E2EView, account: string, text: string): Promise<Extract<Via, { kind: "recovery" }>> {
  const code = C.recoveryBytes(text);
  if (!code) throw new C.E2EError(CODE_FORM);
  try {
    if (!view.recovery) throw new C.E2EError("This account has no recovery code.");
    // a post-quantum account's recovery key is an X-Wing key: a classical one listed is no key of the code's, and is
    // said as such, not as a wrong code
    if (view.pq && C.unb64u(view.recovery.public_key)?.length !== C.HYBRID_KEY_LEN) throw new C.E2EError(CLASSICAL_IN_PQ);
    const { rk, RK } = await recoveryOf(code, account, view.pq);
    if (C.b64u(RK) !== view.recovery.public_key) {
      forgetPriv(rk);
      throw new C.E2EError(WRONG_CODE);
    }
    let anchor: string;
    let anchorKey: CryptoKey;
    try {
      anchorKey = await C.recoveryAnchorKey(code, account);
      try {
        anchor = await C.openRecoveryAnchorWith(anchorKey, view.recovery.anchor, account);
      } catch {
        throw new C.E2EError(ANCHOR_NOT_OPEN);
      }
    } catch (e) {
      forgetPriv(rk);                                 // the code's key is kept only with its anchor
      throw e;
    }
    return { kind: "recovery", rk, RK, anchor, anchorKey };
  } finally {
    C.zero(code);
  }
}

/** The recovery key from the code's 20 bytes, in the account's form: post-quantum (`pq`), an X-Wing key whose seed is
 *  derived from the code with the post-quantum info (C.recoveryHybrid); else the classical X25519 key. */
async function recoveryOf(code: Uint8Array, account: string, pq: boolean): Promise<{ rk: Priv; RK: C.Bytes }> {
  if (pq) {
    const key = await C.recoveryHybrid(code, account);
    return { rk: hybridPriv(key), RK: C.hybridPublic(key) };
  }
  const { rk, RK } = await C.recoveryKeys(code, account);
  return { rk: classicalPriv(rk), RK };
}

/** Unlocking with the recovery code: step 1, then openRing, as unlockWithPrf (a view older than what this browser saw
 *  refused first, floor or none). */
export async function unlockWithCode(view: E2EView, account: string, text: string, floor: string[] = []): Promise<Ring> {
  notRolledBack(account, view);
  return openMade(view, account, await viaFromCode(view, account, text), floor);
}

/** The current master key's 32 bytes, opened again from its grant to what unlocked the ring, for `use`, and zeroed
 *  after: the page keeps the master key only as a key that cannot be extracted. */
async function withMasterBytes<T>(ring: Ring, view: E2EView, use: (bytes: C.Bytes) => Promise<T>): Promise<T> {
  const grant = view.grants.find((g) => g.mk_id === ring.master.id && g.to === viaTo(ring.via));
  if (!grant) throw new C.E2EError(NO_GRANT);
  const { priv, pub } = viaKey(ring.via);
  const bytes = await openGrantBy(priv, pub, grant.sealed, ring.account, viaTo(ring.via), ring.master.id);
  try {
    if ((await C.masterOf(bytes, ring.account)).id !== ring.master.id) throw new C.E2EError(NOT_LEAD_BACK);
    return await use(bytes);
  } finally {
    C.zero(bytes);
  }
}

// ---- statements ----------------------------------------------------------------------------------------------------

function statementBytes(s: E2EStatement): C.Bytes {
  if (typeof s.payload === "string") {
    if (canonicalJson(JSON.parse(s.payload)) !== s.payload) throw new C.E2EError(`Statement ${s.seq} is not in its canonical form.`);
    return C.utf8(s.payload);
  }
  return C.canonicalBytes(s.payload);
}

const payloadOf = (s: E2EStatement): Record<string, unknown> =>
  (typeof s.payload === "string" ? JSON.parse(s.payload) : s.payload) as Record<string, unknown>;

/** The next statement's seq and prev, after checking that the statements served are listed as they were signed
 *  (statedAsSigned) and chain from the first: seq 1 and then one more each time, each prev the hex sha256 of the one
 *  before's canonical bytes. */
export async function nextLink(view: E2EView): Promise<{ seq: number; prev: string }> {
  statedAsSigned(view);
  let seq = 0;
  let prev = "";
  for (const s of view.statements) {
    const p = payloadOf(s);
    if (s.seq !== seq + 1 || p.seq !== s.seq || p.prev !== prev) {
      throw new C.E2EError(`The statements on the server do not chain: statement ${s.seq} does not follow the one before.`);
    }
    prev = C.hex(await C.sha256(statementBytes(s)));
    seq = s.seq;
  }
  return { seq: seq + 1, prev };
}

/** A statement composed around a nonce the server gave, and its challenge (the sha256 of its canonical bytes). */
export interface Prepared { payload: Record<string, unknown>; challenge: Challenge<"step">; rpId: string; allow: string[]; account: string }

/** An e2e-key statement (the key, its key_id, and `retires`) or an e2e-off, as the next of the chain: `{v,
 *  deployment, purpose, account, nonce, id, seq, prev, key, key_id, retires, created_at, expires_at}`, nothing else;
 *  `v` 3 for a post-quantum key (1,216 bytes) and 2 otherwise, e2e-off included. */
export async function prepareStatement(view: E2EView, purpose: "e2e-key" | "e2e-off",
                                       key?: { EK: Uint8Array; keyId: string; retires: "all" | "grace" }): Promise<Prepared> {
  const { seq, prev } = await nextLink(view);
  const n = await E2EApi.nonce(purpose);
  const now = datedNow(n.server_time);                  // a statement dated ahead would come into the list as it likes
  const payload: Record<string, unknown> = {
    v: key?.EK.length === C.HYBRID_KEY_LEN ? 3 : 2, deployment: n.deployment, purpose, account: n.account, nonce: n.nonce, id: C.hex(C.random(16)), seq, prev,
    created_at: now, expires_at: now + STATEMENT_LIFE,
  };
  if (key) { payload.key = C.b64u(key.EK); payload.key_id = key.keyId; payload.retires = key.retires; }
  return { payload, challenge: await stepChallenge(payload), rpId: n.rp_id, account: n.account,
    allow: (n.credentials ?? []).filter((c) => typeof c === "string") };
}

/** The browser's prompt over a prepared statement: call it first thing in the click's handler. */
export async function signStatement(p: Prepared, allow = p.allow): Promise<Signed> {
  const { credential } = await assertionWith({
    challenge: p.challenge, rpId: p.rpId, allowCredentials: allow.map((id) => ({ type: "public-key" as const, id })),
  });
  return { payload: p.payload, credential };
}

function sameAccount(p: Prepared, account: string) {
  if (p.account !== account) throw new C.E2EError("The server's nonce names another account than this page's.");
}

/** A new content key pair, wrapped under `master`; the wrap opened again (what the page will hold) and checked. For a
 *  post-quantum account (`pq`), an X-Wing key: 32 random bytes of seed, its 60-byte wrap, and the key the wrap opens to
 *  checked against its key_id; the seed is zeroed and the key made to check it put away. */
async function newContentKey(master: C.Master, account: string, pq = false): Promise<{ row: ContentKeyRow; EK: C.Bytes; keyId: string }> {
  if (pq) {
    const seed = C.random(C.SEED_LEN);
    try {
      const made = await C.hybridFromSeed(seed);
      const EK = C.hybridPublic(made);
      C.forgetHybrid(made);
      const keyId = await C.keyIdOf(EK);
      const wrapped = await C.wrapContentSeed(master, seed, keyId, account);
      C.forgetHybrid(await C.unwrapContentSeed(master, wrapped, keyId, account));      // refuses another key
      return { row: { key_id: keyId, public_key: C.b64u(EK), mk_id: master.id, wrapped }, EK, keyId };
    } finally {
      C.zero(seed);
    }
  }
  const pair = await C.newPair(true);
  const keyId = await C.keyIdOf(pair.pub);
  const wrapped = await C.wrapContentKey(master, pair.priv, keyId, account);
  const ek = await C.unwrapContentKey(master, wrapped, keyId, account);
  if (!C.same(await C.publicOf(ek), pair.pub)) throw new C.E2EError("The new content key's wrap does not hold it.");
  return { row: { key_id: keyId, public_key: C.b64u(pair.pub), mk_id: master.id, wrapped }, EK: pair.pub, keyId };
}

const withProof = (proof?: RecoveryProof | null) => (proof ? { recovery_proof: proof } : {});

/** What every write to a post-quantum account's keys carries: the server refuses one
 *  without it, in the words it has for a page older than it. Nothing for a classical account, as an older page sends.
 *  */
export const KEM = { kem: "x-wing" } as const;
const kemOf = (pq: boolean) => (pq ? KEM : {});

// ---- who makes a change, as the server takes one (the second version) -------------------------------------------------

/** Whether a passkey unlocks the keys and goes on doing so as they stand (e2e_keys._staying): its credential enrolled,
 *  its passkey the account's, and a hardware key while high security is on. */
export function staysEnrolled(view: E2EView, passkeyId: string): boolean {
  const p = view.passkeys.find((k) => k.passkey_id === passkeyId);
  return !!p && view.credentials.some((c) => c.passkey_id === passkeyId) && (!view.high_security || p.tier === "hardware");
}

/** Whether the change that waits is the upgrade to post-quantum keys. It is a revocation for every rule (e2e_keys
 *  _in_place_of): a change made by whoever may cancel it takes its place and ends it,
 *  as for any change that waits, so a stolen passkey that places one upgrade after another never keeps the owner from
 *  revoking it. Named here for what is said of it. */
export const upgradeWaits = (view: E2EView) => view.pending?.change === "revoke" && view.pending.body?.upgrade === true;

/** Whether the change that waits is the fold: turning a classical account's encryption on again with post-quantum keys,
 *  its new master key made as the upgrade makes one (Fold). It is a turn-on for every
 *  rule, held as the upgrade is held, and removes no passkey. Named here for what is said of it. */
export const foldWaits = (view: E2EView) => view.pending?.change === "turn-on" && view.pending.body?.upgrade === true;

/** Whether a change that `passkeyId` signed or confirmed may take the place of the change that waits
 *  (e2e_keys._in_place_of): none waits, or that passkey could cancel it (the view's cancel_by). */
export const inPlaceBy = (view: E2EView, passkeyId: string) => !view.pending || view.pending.cancel_by.includes(passkeyId);

/** Whether a change may go with the recovery code's proof: the account has a code, and none waits or the code cancels
 *  the one that does (the view's cancel_by_code). */
export const codeMakes = (view: E2EView) => !!view.recovery && (!view.pending || view.pending.cancel_by_code);

/** The passkeys that may sign a statement sent with the recovery code's proof, as the server takes one: any of the
 *  account's, a hardware key's alone while high security is on or a revocation turns it on (_may_sign,
 *  NEEDS_HARDWARE_TO_TURN). */
export function anySigners(view: E2EView, what: RevokeWhat = {}): E2EPasskey[] {
  const high = !!what.highSecurity || view.high_security;
  return view.passkeys.filter((p) => !!p.credential_id && (!high || p.tier === "hardware"));
}

/** The passkeys that make a change without the recovery code: those that unlock the keys and still do after it
 *  (`staying`: for a revocation, not one it removes, and only the hardware keys while high security is on or it turns it
 *  on), and of them those that may take the place of the change that waits (`inPlace`). */
export function stayingMakers(view: E2EView, what: RevokeWhat = {}): { staying: E2EPasskey[]; inPlace: E2EPasskey[] } {
  const removing = new Set(what.remove ?? []);
  const high = !!what.highSecurity || view.high_security;
  const staying = view.passkeys.filter((p) => !!p.credential_id && !removing.has(p.passkey_id)
    && view.credentials.some((c) => c.passkey_id === p.passkey_id) && (!high || p.tier === "hardware"));
  return { staying, inPlace: staying.filter((p) => inPlaceBy(view, p.passkey_id)) };
}

/** Whether a change goes only with the recovery code's proof: no passkey that unlocks the keys and stays after it may
 *  make it in the place of the change that waits, and the code's proof may (codeMakes), with a passkey that may sign it
 *  where it is signed (`signed`: a change with a statement; one only confirmed takes the proof alone). `what`, a
 *  revocation's; nothing for another change. The page then asks for the code before any touch, and sends the change
 *  with the proof, never without. */
export function onlyWithCode(view: E2EView, what: RevokeWhat = {}, signed = true): boolean {
  return !stayingMakers(view, what).inPlace.length && codeMakes(view) && (!signed || anySigners(view, what).length > 0);
}

/** Why a change cannot be made at all, before any touch, or null: none of `makers` may make it in place of the change
 *  that waits, and the recovery code's proof cannot either (the account has no code that cancels that change, or
 *  `signers` says no passkey of the account may sign it even with the code). When this is null and `makers.inPlace` is
 *  empty, the change goes only with the code's proof. */
function refusal(view: E2EView, makers: { staying: E2EPasskey[]; inPlace: E2EPasskey[] }, signers = true): string | null {
  if (makers.inPlace.length) return null;
  if (!signers) return NONE_AT_ALL;
  if (codeMakes(view)) return null;
  return makers.staying.length ? WAITING_CHANGE : NONE_TO_SIGN;
}

/** The credentials the browser is offered to sign `p` with: `signers`', those the server names of them when it names
 *  any (its nonce's credentials); E2EError(`why`) when none is left. */
function allowOf(p: Prepared, signers: E2EPasskey[], why: string): string[] {
  const allow = signers.map((s) => s.credential_id).filter((id) => !p.allow.length || p.allow.includes(id));
  if (!allow.length) throw new C.E2EError(why);
  return allow;
}

/** The credentials the browser is offered to sign `p`, the statement of a change (`what`, a revocation's; nothing for
 *  another): with the recovery code's proof (`code`), any passkey that may sign (anySigners); without it, those that
 *  unlock the keys and stay and may take the place of the change that waits (stayingMakers). */
function signersOf(p: Prepared, view: E2EView, what: RevokeWhat, code: boolean): string[] {
  return allowOf(p, code ? anySigners(view, what) : stayingMakers(view, what).inPlace, code ? NONE_AT_ALL : NONE_TO_SIGN);
}

/** What a passkey's confirmation for one of encryption's changes waits on when its challenge is not here yet. */
export const BEING_PREPARED = "The confirmation is being prepared: try again in a moment.";

// ---- turning it on -------------------------------------------------------------------------------------------------

/** Which group of four characters the person types back, of the eight the recovery code is shown in. */
const someGroup = () => C.random(1)[0] % 8;

/** Turning encryption on, in its order: the recovery code first, four of its characters typed back;
 *  then the master key, the content key pair (wrapped under the master key at once, its private half then dropped), the
 *  credential pair (its private half held only wrapped under a key that cannot be extracted until the first touch wraps
 *  it for good), the statement, and the master key sealed to each passkey the first touch may use and to the recovery
 *  key, its bytes then zeroed (prepare); a first touch that signs the statement and evaluates the passkey's PRF
 *  (firstTouch), a second that evaluates it again and must open what the first wrapped, with the anchor (secondTouch);
 *  then the roster, the grants sent, the recovery anchor, the content key's wrap and the roster opened again before
 *  anything is sent, and one request with everything (send). Each touch is its own click. The server holds it as it
 *  holds any change, so the keys are unlocked once it takes effect. For an account that keeps keys from before
 *  (encryption turned off), `replace` starts again: what was sealed under the old keys then opens no more.
 *
 *  Every key it makes is post-quantum: X-Wing keys for the content key,
 *  the credential and the recovery code, each private key a 32-byte seed in a 60-byte wrap, the credential's anchor
 *  stored beside its wrap, a version-3 statement, form-2 grants, and `kem: "x-wing"` in the request. `classical` makes
 *  the classical keys instead: what an account turned on by an older release holds, which the page itself never makes
 *  now (its tests stand in for such an account with it). */
export class TurnOn {
  readonly code = C.random(C.CODE_BYTES);
  readonly group = someGroup();
  /** whether the keys it makes are post-quantum */
  readonly pq: boolean;
  private s: {
    view: E2EView; master: C.Master; content: ContentKeyRow; contentPub: C.Bytes;
    cred: { held: HeldSecret | null; pub: C.Bytes }; rk: Priv; RK: C.Bytes; statement: Prepared;
    allow: E2EPasskey[]; salts: Map<string, C.Bytes>; grants: Map<string, string>; recoveryGrant: string;
    touched?: { passkey: E2EPasskey; signed: Signed; wrapped: string };
    ck?: Priv;
  } | null = null;

  constructor(readonly account: string, readonly replace = false, opts: { classical?: boolean } = {}) {
    this.pq = !opts.classical;
  }

  /** The code as it is shown, once. */
  codeText(): string { return C.recoveryText(this.code); }
  /** Whether the person typed back the group asked for. */
  codeMatches(typed: string): boolean {
    return typed.replace(/[\s-]+/g, "").toUpperCase() === this.codeText().split(" ")[this.group];
  }

  /** The passkeys a first touch may use: those the server says may be enrolled (while high security is on, a hardware
   *  key), of them the ones its statements may be signed with when the server names those. Starting again (`replace`),
   *  a passkey enrolled before may be again: the keys from before are forgotten. */
  static allowed(view: E2EView, signers: string[] = [], replace = false): E2EPasskey[] {
    const may = (p: E2EPasskey) => (replace ? !view.high_security || p.tier === "hardware" : p.can_enrol !== false);
    return view.passkeys.filter((p) => may(p) && !!p.credential_id && (!signers.length || signers.includes(p.credential_id)));
  }

  async prepare(view: E2EView): Promise<void> {
    this.dispose(false);
    // starting again over keys older than this browser saw is refused. A first turn-on is not, over no keys where this
    // browser saw the account's either: Encryption warns there that the server may have been put back, and linked
    // sessions send in the clear until encryption is on
    if (this.replace) notRolledBack(this.account, view);
    if (this.pq && !view.mlkem) throw new C.E2EError(NO_MLKEM);
    const { account, pq } = this;
    const mkBytes = C.random(32);
    let rk: Priv | null = null;
    try {
      const master = await C.masterOf(mkBytes, account);
      // the content key wrapped under the master key now: its private half is not kept while the person touches
      const content = await newContentKey(master, account, pq);
      // the credential's private half is wrapped under its passkey's PRF output at the first touch: until then it is
      // held only wrapped under a key that cannot be extracted
      const cred = await newHeldKey(pq);
      const recovery = await recoveryOf(this.code, account, pq);
      rk = recovery.rk;
      const statement = await prepareStatement(view, "e2e-key", { EK: content.EK, keyId: content.keyId, retires: "all" });
      sameAccount(statement, account);
      const may = TurnOn.allowed(view, statement.allow, this.replace);
      if (!may.length) throw new C.E2EError("No passkey of this account can unlock encryption: add a passkey first.");
      // while a change waits, the server makes this one in its place only signed by a passkey that could cancel that one
      const allow = may.filter((p) => inPlaceBy(view, p.passkey_id));
      if (!allow.length) throw new C.E2EError(WAITING_CHANGE);
      const salts = new Map(allow.map((p) => [p.credential_id, C.random(32)]));
      // the master key's bytes are needed only to seal it: to each passkey the first touch may pick, and to the
      // recovery key, now, so they are not kept while the person touches
      const grants = new Map<string, string>();
      for (const p of allow) grants.set(p.credential_id, await C.sealGrant(cred.pub, mkBytes, account, p.credential_id, master.id));
      const recoveryGrant = await C.sealGrant(recovery.RK, mkBytes, account, RECOVERY, master.id);
      this.s = { view, master, content: content.row, contentPub: content.EK, cred, rk: recovery.rk, RK: recovery.RK, statement,
        allow, salts, grants, recoveryGrant };
      rk = null;
    } finally {
      C.zero(mkBytes);
      forgetPriv(rk);
    }
  }

  /** The first touch: signs the statement and evaluates the PRF; the credential's private key wrapped under it, and then
   *  held no more. */
  async firstTouch(): Promise<void> {
    const s = this.need();
    const t = await prfTouch({ challenge: s.statement.challenge, rpId: s.statement.rpId, doing: "add", keys: keyCredentials(s.allow),
      allow: s.allow.map((p) => ({ credentialId: p.credential_id, salt: s.salts.get(p.credential_id)! })) });
    const passkey = s.allow.find((p) => p.credential_id === t.credentialId)!;
    const kek = await C.credentialKek(t.prf, this.account, t.credentialId);
    if (!s.cred.held) throw new C.E2EError("Start turning it on again.");
    const held = s.cred.held;
    s.cred.held = null;
    const wrapped = await held.seal(kek, s.master.id, this.account, t.credentialId);
    s.touched = { passkey, signed: { payload: s.statement.payload, credential: t.credential }, wrapped };
  }

  /** The second touch: the PRF again, which must open the wrap the first made and give back the key and the anchor; an
   *  unstable PRF fails here, and nothing is kept. */
  async secondTouch(): Promise<void> {
    const s = this.need();
    if (!s.touched) throw new C.E2EError("Sign with the passkey first.");
    const id = s.touched.passkey.credential_id;
    const t = await prfTouch({ challenge: pageChallenge(), rpId: s.statement.rpId, allow: [{ credentialId: id, salt: s.salts.get(id)! }], doing: "add",
      keys: keyCredentials(s.allow) });
    const kek = await C.credentialKek(t.prf, this.account, id);
    const opened = await openOwnRecord(kek, s.touched.wrapped, s.master.id, this.account, id, s.cred.pub);
    if (!opened) {
      this.dispose(false);
      throw new C.E2EError(UNSTABLE);
    }
    s.ck = opened;
  }

  /** Everything sealed, checked, and sent in one request: `seal` asks the server to seal in place what links sent in the
   *  clear. It waits on a hold, as every change does, and the server keeps none of it until the hold ends. Returns the
   *  server's answer (the hold), what unlocks the keys (`via`, for this tab's memory only, which opens them once the
   *  hold has ended), the view read after it, and the ring when the server serves the keys already (a hold of no time
   *  ends at that read), else null. This browser trusts the keys it made (expectKeys), though they do not lead back to
   *  keys it opened before. */
  async send(seal: boolean): Promise<{ answer: Answer; ring: Ring | null; via: Via; view: E2EView }> {
    const s = this.need();
    if (!s.touched || !s.ck) throw new C.E2EError("Touch the passkey a second time first.");
    const { account, pq } = this;
    const id = s.touched.passkey.credential_id;
    const credentialGrant = s.grants.get(id);
    if (!credentialGrant) throw new C.E2EError("The browser answered with a passkey it was not asked for.");
    const roster: C.Roster = {
      credentials: [{ credential_id: id, passkey_id: s.touched.passkey.passkey_id, public_key: C.b64u(s.cred.pub) }],
      recovery: C.b64u(s.RK),
    };
    const rosterSealed = await C.sealRoster(s.master, roster, account);
    const anchor = await C.sealRecoveryAnchor(this.code, s.master.id, account);
    // before anything is sent: each grant opens, the recovery one with the rk just derived and the passkey's with the key
    // its second touch opened; the content key's wrap with its subkey; the recovery anchor with the code; the roster,
    // to what it was meant to say
    await holdsMaster(s.rk, s.RK, s.recoveryGrant, account, RECOVERY, s.master.id);
    await holdsMaster(s.ck, s.cred.pub, credentialGrant, account, id, s.master.id);
    const content = await openContentKey(s.master, s.content, account, pq);
    forgetPriv(content?.ek);
    if (!content || !C.same(content.EK, s.contentPub)) throw new C.E2EError("The content key's wrap does not hold the content key.");
    if (await C.openRecoveryAnchor(this.code, anchor, account) !== s.master.id) throw new C.E2EError("The recovery anchor does not name the master key.");
    if (!sameRoster(await C.openRoster(s.master, rosterSealed, account), roster)) throw new C.E2EError("The roster does not say what it was meant to.");
    const answer = await E2EApi.turnOn({
      statement: s.touched.signed,
      content_key: s.content,
      mk_id: s.master.id,
      credential: { passkey_id: s.touched.passkey.passkey_id, salt: C.b64u(s.salts.get(id)!), public_key: C.b64u(s.cred.pub), wrapped: s.touched.wrapped,
        ...(pq ? { anchor: s.master.id } : {}) },
      recovery: { public_key: C.b64u(s.RK), anchor },
      grants: [{ to: id, sealed: credentialGrant }, { to: RECOVERY, sealed: s.recoveryGrant }],
      roster: rosterSealed,
      seal,
      ...(this.replace ? { replace: true } : {}),
      ...kemOf(pq),
    });
    C.zero(this.code);
    forgetPriv(s.rk);
    await expectKeys(account, s.master.id);
    const via: Via = { kind: "credential", credentialId: id, passkeyId: s.touched.passkey.passkey_id, ck: s.ck, CK: s.cred.pub, anchor: s.master.id };
    s.ck = undefined;                          // the caller's now (via), so disposing of the flow leaves it held
    let view: E2EView;
    let ring: Ring | null;
    try {
      view = await E2EApi.view();
      ring = view.on && view.mk_id === s.master.id ? await openRing(view, account, via) : null;
    } catch (e) {
      forgetPriv(via.ck);
      throw e;
    }
    return { answer, ring, via, view };
  }

  /** Drop everything made so far, the code too unless `code` is false: the key the second touch opened among it. */
  dispose(code = true) {
    if (this.s) {
      forgetPriv(this.s.rk);
      forgetPriv(this.s.ck);
      this.s.cred.held?.drop();
    }
    this.s = null;
    if (code) C.zero(this.code);
  }

  private need() {
    if (!this.s) throw new C.E2EError("Encryption is not being turned on.");
    return this.s;
  }
}

/** A credential's private key made before the touch that wraps it, held until then only wrapped under a key that
 *  cannot be extracted: a classical X25519 key (C.holdPrivate), or a post-quantum key's seed, its 32 bytes zeroed once
 *  wrapped. `seal` gives the credential's own wrap under the touch's key, once; `drop` forgets it. */
interface HeldSecret {
  seal(kek: CryptoKey, anchor: string, account: string, credentialId: string): Promise<string>;
  drop(): void;
}

/** A new credential key pair, its private half held (HeldSecret) and its public key: post-quantum (`pq`) or
 *  classical. `wait`: it waits for a touch (turning on), so a classical private key is held wrapped; enrolment makes
 *  the pair at its touch and seals it at once. */
async function newHeldKey(pq: boolean, wait = true): Promise<{ held: HeldSecret; pub: C.Bytes }> {
  if (!pq) {
    const pair = await C.newPair(true);
    const held = wait ? await C.holdPrivate(pair.priv) : { release: async () => pair.priv };
    let used = false;
    return { pub: pair.pub, held: {
      seal: async (kek, anchor, account, credentialId) => {
        if (used) throw new C.E2EError("That key was given back already.");
        used = true;
        return C.sealCredential(kek, await held.release(), anchor, account, credentialId);
      },
      drop: () => { used = true; },
    } };
  }
  const seed = C.random(C.SEED_LEN);
  let blob: C.Bytes | null = null;
  const key = (await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"])) as CryptoKey;
  const aad = C.utf8(`${C.V2} held seed`);
  try {
    const made = await C.hybridFromSeed(seed);
    const pub = C.hybridPublic(made);
    C.forgetHybrid(made);
    blob = await C.gcmSeal(key, seed, aad);
    return { pub, held: {
      seal: async (kek, anchor, account, credentialId) => {
        const sealed = blob;
        blob = null;
        if (!sealed) throw new C.E2EError("That key was given back already.");
        const back = await C.gcmOpen(key, sealed, aad);
        try {
          return await C.sealCredentialSeed(kek, back, anchor, account, credentialId);
        } finally {
          C.zero(back);
        }
      },
      drop: () => { blob = null; },
    } };
  } finally {
    C.zero(seed);
  }
}

/** A credential's own wrap just made, opened again with the key a second touch gave: its private key, when it holds the
 *  key `pub` and the anchor `anchor` (a post-quantum wrap's anchor is in its AAD, so the wrap opens only with it); null
 *  when it does not, as an unstable PRF gives. */
async function openOwnRecord(kek: CryptoKey, wrapped: string, anchor: string, account: string, credentialId: string,
                             pub: C.Bytes): Promise<Priv | null> {
  if (pub.length === C.HYBRID_KEY_LEN) {
    try {
      return hybridPriv(await C.unwrapCredentialSeed(kek, wrapped, anchor, account, credentialId, pub));
    } catch {
      return null;
    }
  }
  let opened: { ck: CryptoKey; anchor: string };
  try {
    opened = await C.openCredential(kek, wrapped, account, credentialId);
  } catch {
    return null;
  }
  return opened.anchor === anchor && C.same(await C.publicOf(opened.ck), pub) ? classicalPriv(opened.ck) : null;
}

/** Turning encryption on again, for an account that keeps its keys from when it was on, with them unlocked: a new
 *  content key under the current master key and its statement, which retires every earlier key for sealing; the
 *  credentials, the recovery code and the master key stay as they are. While a new master key is due (rekeyDue: a
 *  passkey that unlocked it was removed, or left out by high security, while it was off), it makes one as a revocation
 *  does, sealed only to the passkeys that stay. One touch of a passkey that unlocks encryption and stays, the statement's
 *  signature, as the server takes it from no other: where none stays, only starting again turns it on (NO_SIGNER). It
 *  waits on a hold, as every change does. */
export class TurnOnAgain {
  private s: { statement: Prepared; body: Record<string, unknown>; allow: string[] } | null = null;
  /** whether it makes a new master key; the passkeys that then leave the roster, and those that stay (passkey ids) */
  rekey = false;
  leaving: string[] = [];
  kept: string[] = [];

  constructor(readonly ring: Ring, readonly view: E2EView) {}

  async prepare(): Promise<void> {
    const { ring, view } = this;
    needsMlkem(ring, view);
    checkCurrent(ring, view, true);
    rosterOf(ring);                           // whom it is signed by, and whom a new master key is sealed to
    this.rekey = rekeyDue(ring, view);
    const after = rosterAfter(ring, view).kept;
    if (!after.length) throw new C.E2EError(NO_SIGNER);
    // while a change waits, the server makes this one in its place only signed by a passkey that could cancel that one,
    // and it takes no recovery code's proof with turning encryption on
    if (!after.some((c) => inPlaceBy(view, c.passkey_id))) throw new C.E2EError(WAITING_CHANGE);
    let statement: Prepared;
    let body: Record<string, unknown>;
    let kept: C.Roster["credentials"];
    if (this.rekey) {
      const made = await newMaster(ring, view, {});
      ({ statement, body, kept } = made);
      this.leaving = made.leaving.map((c) => c.passkey_id);
    } else {
      const { row, EK, keyId } = await newContentKey(ring.master, ring.account, ring.pq);
      statement = await prepareStatement(view, "e2e-key", { EK, keyId, retires: "all" });
      sameAccount(statement, ring.account);
      body = { content_key: row, ...kemOf(ring.pq) };
      kept = rosterOf(ring).credentials;
    }
    // signed by a passkey that unlocks encryption and stays, as the server takes it, that may take the place of the
    // change that waits; of them, those the server names
    const named = statement.allow;
    const allow = kept.filter((c) => inPlaceBy(view, c.passkey_id)).map((c) => c.credential_id)
      .filter((id) => !named.length || named.includes(id));
    if (!allow.length) throw new C.E2EError(NO_SIGNER);
    this.kept = kept.map((c) => c.passkey_id);
    this.s = { statement, body, allow };
  }

  /** Signs and sends: call it first thing in the click's handler. Refused before the touch from keys below this browser's
   *  floor, which another tab may have raised since it was prepared. */
  async signAndSend(seal: boolean): Promise<Answer> {
    if (!this.s) throw new C.E2EError("Prepare turning it on first.");
    aboveFloor(this.ring);
    const signed = await signStatement(this.s.statement, this.s.allow);
    return E2EApi.turnOn({ statement: signed, ...this.s.body, seal });
  }
}

// ---- adding a passkey to encryption --------------------------------------------------------------------------------

/** Adding a credential, with the master key unlocked: two touches of the new passkey. The first,
 *  over the page's own challenge, evaluates its PRF; the new key pair is made then, and its private half wrapped under
 *  that PRF with the current mk_id as its anchor. The second, over the challenge derived from the server's for
 *  e2e-enrol-own (fetched as the first ends), must open that wrap, or nothing is kept; it is then the passkey's own
 *  confirmation, which the server takes the new credential only with (its ENROL_OWN), so the key it names is never
 *  one put in for a passkey nobody touched. Then the master key is sealed to the new key, the grant opened with it
 *  before it is sent, and the roster rewritten. It is confirmed as the server takes it (CONFIRM_STAYING): by a passkey
 *  that unlocks the keys already and still does, never the one added, touched after the two (confirmAndSend); or with
 *  the recovery code's proof (sendWithCode). While a change waits, only by one that may take its place: a passkey its
 *  cancel_by names, or the code that cancels it; when none of those passkeys is left, only with the code (byCode). */
export class Enrol {
  private s: {
    salt: C.Bytes; cred?: { pub: C.Bytes }; wrapped?: string; ck?: Priv;
    own?: Ceremony | null; ownConfirmation?: string; ceremony?: Ceremony | null; confirmation?: string;
  } | null = null;
  /** whether it goes only with the recovery code's proof: no passkey that unlocks the keys and stays may confirm it in
   *  the place of the change that waits (onlyWithCode; the passkey added unlocks nothing yet, so it is never one) */
  readonly byCode: boolean;

  constructor(readonly ring: Ring, readonly view: E2EView, readonly passkey: E2EPasskey) {
    this.byCode = onlyWithCode(view, {}, false);
  }

  /** The passkeys whose touch confirms it: those that unlock the keys and stay, but the one added, that may take the
   *  place of the change that waits. */
  confirmers(): E2EPasskey[] {
    return stayingMakers(this.view).inPlace.filter((p) => p.passkey_id !== this.passkey.passkey_id);
  }

  async prepare(): Promise<void> {
    needsMlkem(this.ring, this.view);
    checkCurrent(this.ring, this.view);       // the roster it rewrites is the one the ring opened
    rosterOf(this.ring);
    const staying = stayingMakers(this.view).staying.filter((p) => p.passkey_id !== this.passkey.passkey_id);
    const why = refusal(this.view, { staying, inPlace: this.confirmers() });
    if (why) throw new C.E2EError(why);
    this.s = { salt: C.random(32) };
  }

  /** The first touch of the new passkey, over the page's own challenge, evaluating its PRF: call it first thing in the
   *  click's handler. The second touch's challenge is fetched as it ends. */
  async firstTouch(): Promise<void> {
    const s = this.need();
    aboveFloor(this.ring);
    const id = this.passkey.credential_id;
    const t = await prfTouch({ challenge: pageChallenge(), allow: [{ credentialId: id, salt: s.salt }], doing: "add", keys: keyCredentials([this.passkey]) });
    const kek = await C.credentialKek(t.prf, this.ring.account, id);
    // the key pair made only now, and only its public key kept once the private one is sealed, so an extractable
    // private key is never kept while the page waits for a touch; a post-quantum account's a
    // post-quantum key, whose seed is zeroed once wrapped
    const cred = await newHeldKey(this.ring.pq, false);
    s.wrapped = await cred.held.seal(kek, this.ring.master.id, this.ring.account, id);
    s.cred = { pub: cred.pub };
    await this.fetchOwn();
  }

  /** The second touch's challenge, fetched (again): the server's for e2e-enrol-own. A failure leaves none, which the
   *  second touch then says. */
  private async fetchOwn(): Promise<void> {
    const s = this.s;
    if (!s) return;
    s.own = await confirmCeremony("e2e-enrol-own").catch(() => null);
  }

  /** The second touch, over the challenge derived from the server's for e2e-enrol-own, which must open what the first
   *  wrapped, and is then the passkey's own confirmation: call it first thing in the click's handler. Its confirmation
   *  is asked for only once the wrap opened to the same key; when that fails, a fresh challenge is fetched and the
   *  touch may be made again. Then, unless it goes only with the recovery code, the confirming touch's challenge is
   *  fetched, so that touch reaches the browser's prompt with nothing awaited. */
  async secondTouch(): Promise<void> {
    const s = this.need();
    if (!s.wrapped || !s.cred) throw new C.E2EError("Touch the passkey the first time first.");
    const own = s.own;
    if (!own) {
      void this.fetchOwn();
      throw new C.E2EError(BEING_PREPARED);
    }
    const id = this.passkey.credential_id;
    const t = await prfTouch({ challenge: own.ask.challenge, rpId: own.ask.rpId, allow: [{ credentialId: id, salt: s.salt }], doing: "add",
      keys: keyCredentials([this.passkey]) });
    s.own = null;
    const kek = await C.credentialKek(t.prf, this.ring.account, id);
    // the wrap opens to the key whose public key it sends, with the current anchor (enrolment's
    // second touch)
    const opened = await openOwnRecord(kek, s.wrapped, this.ring.master.id, this.ring.account, id, s.cred.pub);
    if (!opened) {
      this.s = null;
      throw new C.E2EError(UNSTABLE);
    }
    try {
      s.ownConfirmation = (await E2EApi.confirm(own.challenge_id, t.credential)).confirmation;
    } catch (e) {
      forgetPriv(opened);
      void this.fetchOwn();
      throw e;
    }
    s.ck = opened;
    if (!this.byCode) await this.fetchCeremony();
  }

  /** The confirmation's challenge, fetched (again): a failure leaves none, which the confirming touch then says. */
  private async fetchCeremony(): Promise<void> {
    const s = this.s;
    if (!s) return;
    s.ceremony = await confirmCeremony("e2e-enrol-touch").catch(() => null);
  }

  /** The confirming touch, of a passkey that unlocks the keys and stays (confirmers), the only ones the browser is
   *  offered, over the challenge derived from the server's; then everything sent with its confirmation. Call it first
   *  thing in the click's handler. A fresh challenge is fetched behind it, should the change be refused and confirmed
   *  again. */
  async confirmAndSend(): Promise<Answer> {
    const s = this.need();
    if (!s.ck || !s.cred || !s.wrapped) throw new C.E2EError("Touch the passkey twice first.");
    if (this.byCode) throw new C.E2EError(CODE_NEEDED);
    const c = s.ceremony;
    if (!c) {
      void this.fetchCeremony();
      throw new C.E2EError(BEING_PREPARED);
    }
    aboveFloor(this.ring);
    const allow = this.confirmers().map((p) => p.credential_id);
    const { credential } = await assertionWith({ challenge: c.ask.challenge, rpId: c.ask.rpId,
      allowCredentials: allow.map((id) => ({ type: "public-key" as const, id })) });
    s.ceremony = null;
    void this.fetchCeremony();
    if (!allow.includes(credential.id)) throw new C.E2EError(ASKED_ANOTHER);
    s.confirmation = (await E2EApi.confirm(c.challenge_id, credential)).confirmation;
    return this.send();
  }

  /** Everything sent with the recovery code's proof, made after the touches with `rk`, the code's key (codeKey, or that
   *  of keys unlocked with the code): no confirming touch. It waits a day, as what the code confirms does. */
  async sendWithCode(rk: Priv): Promise<Answer> {
    const s = this.need();
    if (!s.ck || !s.cred || !s.wrapped) throw new C.E2EError("Touch the passkey twice first.");
    aboveFloor(this.ring);                    // before the proof is asked for, as before any touch
    return this.sendWith({ recovery_proof: await proofFrom(rk, "credentials") });
  }

  /** Everything sent again with the confirmation already made (confirmAndSend), which the server uses up only when it
   *  places the change: after a refusal that was not about the confirmation. */
  async send(): Promise<Answer> {
    const s = this.need();
    if (!s.confirmation) throw new C.E2EError(this.byCode ? CODE_NEEDED : "Confirm it with a passkey that unlocks encryption first.");
    return this.sendWith({ confirmation: s.confirmation });
  }

  private async sendWith(word: { confirmation: string } | { recovery_proof: RecoveryProof }): Promise<Answer> {
    const s = this.need();
    if (!s.ck || !s.cred || !s.wrapped) throw new C.E2EError("Touch the passkey twice first.");
    aboveFloor(this.ring);
    const { account, master } = this.ring;
    const roster = rosterOf(this.ring);
    const id = this.passkey.credential_id;
    const pub = s.cred.pub;
    const grant = await withMasterBytes(this.ring, this.view, (bytes) => C.sealGrant(pub, bytes, account, id, master.id));
    const next: C.Roster = {
      credentials: [...roster.credentials.filter((c) => c.credential_id !== id),
        { credential_id: id, passkey_id: this.passkey.passkey_id, public_key: C.b64u(pub) }],
      recovery: roster.recovery,
    };
    const sealed = await C.sealRoster(master, next, account);
    // before it is sent: the grant opens with the key the second touch opened, and the roster to what it was meant to say
    await holdsMaster(s.ck, pub, grant, account, id, master.id);
    if (!sameRoster(await C.openRoster(master, sealed, account), next)) throw new C.E2EError("The new roster does not say what it was meant to.");
    return E2EApi.enrol({
      credential: { passkey_id: this.passkey.passkey_id, salt: C.b64u(s.salt), public_key: C.b64u(pub), wrapped: s.wrapped,
        confirmation: s.ownConfirmation, ...(this.ring.pq ? { anchor: master.id } : {}) },
      mk_id: master.id,
      grant,
      roster: sealed,
      ...word,
      ...kemOf(this.ring.pq),
    });
  }

  /** Drop what was made: the key the second touch opened, kept until then to check the grant to it again at each send. */
  dispose() {
    forgetPriv(this.s?.ck);
    this.s = null;
  }

  private need() {
    if (!this.s) throw new C.E2EError("Start adding the passkey again.");
    return this.s;
  }
}

// ---- a routine rotation --------------------------------------------------------------------------------------------

/** A routine rotation: a new content key pair wrapped under the current master key, and its statement, which retires the
 *  earlier keys for sealing an hour after (grace). One touch, the statement's signature. Made only from keys at this
 *  browser's floor, the ones the server lists, holding the newest key stated: a statement signed from older keys (a
 *  revocation this tab has not followed, or old rows served to it alone) would put a key the revocation's passkey still
 *  opens after it on the account's chain. */
export class Rotate {
  private s: { statement: Prepared; row: ContentKeyRow } | null = null;
  /** whether it goes only with the recovery code's proof (onlyWithCode): no passkey that unlocks the keys and stays may
   *  sign it in the place of the change that waits */
  readonly byCode: boolean;

  constructor(readonly ring: Ring, readonly view: E2EView) {
    this.byCode = onlyWithCode(view);
  }

  async prepare(): Promise<void> {
    needsMlkem(this.ring, this.view);
    checkCurrent(this.ring, this.view, true);
    const why = refusal(this.view, stayingMakers(this.view), anySigners(this.view).length > 0);
    if (why) throw new C.E2EError(why);
    const { row, EK, keyId } = await newContentKey(this.ring.master, this.ring.account, this.ring.pq);
    const statement = await prepareStatement(this.view, "e2e-key", { EK, keyId, retires: "grace" });
    sameAccount(statement, this.ring.account);
    signersOf(statement, this.view, {}, this.byCode);
    this.s = { statement, row };
  }

  /** Signs and sends: call it first thing in the click's handler. With `proof`, the recovery code's proof made before
   *  it (recoveryProof), any passkey that may sign signs it; without one, a passkey that unlocks the keys and stays, and
   *  one that goes only with the code (byCode) is refused here before any touch. Refused before the touch, too, from
   *  keys below this browser's floor, which another tab may have raised since it was prepared. */
  async signAndSend(proof?: RecoveryProof | null): Promise<Answer> {
    if (!this.s) throw new C.E2EError("Prepare the rotation first.");
    if (this.byCode && !proof) throw new C.E2EError(CODE_NEEDED);
    aboveFloor(this.ring);
    const signed = await signStatement(this.s.statement, signersOf(this.s.statement, this.view, {}, !!proof));
    return E2EApi.rotate({ statement: signed, content_key: this.s.row, ...withProof(proof), ...kemOf(this.ring.pq) });
  }

  /** Signs it, and sends it with the recovery code's proof, asked for after the touch and made with `rk`, the code's key
   *  (codeKey, or that of keys unlocked with the code): call it first thing in the click's handler. It waits a day, as
   *  what the code confirms does. */
  async signAndSendWithCode(rk: Priv): Promise<Answer> {
    if (!this.s) throw new C.E2EError("Prepare the rotation first.");
    aboveFloor(this.ring);
    const signed = await signStatement(this.s.statement, signersOf(this.s.statement, this.view, {}, true));
    return E2EApi.rotate({ statement: signed, content_key: this.s.row, recovery_proof: await proofFrom(rk, "rotate"), ...kemOf(this.ring.pq) });
  }
}

// ---- a new master key: a revocation, or turning it on again while one is due ---------------------------------------

/** What a revocation does besides replacing the master key: passkeys removed from the account (their credentials leave
 *  the roster), high security turned on (every credential but a hardware key's leaves it), or a new recovery code (its
 *  20 bytes, which the caller zeroes when it is done with them). `onlyCode`: the person said that is what they want,
 *  should no passkey unlock encryption after it, only the recovery code. */
export interface RevokeWhat { remove?: string[]; highSecurity?: boolean; newCode?: Uint8Array; onlyCode?: boolean }

type RosterEntry = C.Roster["credentials"][number];

/** Who of the roster stays and who leaves, by what `what` does and what the server lists: a credential leaves when its
 *  passkey is removed now, is gone from the account, or is no longer enrolled with the key the roster names it by,
 *  while high security is on or turned on now when it is not a hardware key's, and whatever the server lists when this
 *  browser removed its passkey (removedHere). None of either for a ring without a roster (rosterOf refuses the change). */
export function rosterAfter(ring: Ring, view: E2EView, what: RevokeWhat = {}): { kept: RosterEntry[]; leaving: RosterEntry[] } {
  const removing = new Set(what.remove ?? []);
  const high = !!what.highSecurity || view.high_security;
  const tier = new Map(view.passkeys.map((p) => [p.passkey_id, p.tier]));
  const removed = removedHere(ring.account);
  const enrolled = (c: RosterEntry) => view.credentials.some((v) =>
    v.credential_id === c.credential_id && v.passkey_id === c.passkey_id && v.public_key === c.public_key);
  const leaves = (c: RosterEntry) => removing.has(c.passkey_id) || !tier.has(c.passkey_id) || !enrolled(c)
    || (high && tier.get(c.passkey_id) !== "hardware") || wasRemoved(removed, c);
  const named = ring.roster?.credentials ?? [];
  return { kept: named.filter((c) => !leaves(c)), leaving: named.filter(leaves) };
}

/** The passkeys whose credentials the server counts as staying after `what` (their passkey the account's and not
 *  removed now, a hardware key's while high security is or will be on), that the roster does not name with the key the
 *  server lists: added without the master key, so never sealed to, and the server takes a new master key only granted
 *  to every credential that stays. */
export function unnamed(ring: Ring, view: E2EView, what: RevokeWhat = {}): E2EPasskey[] {
  const removing = new Set(what.remove ?? []);
  const high = !!what.highSecurity || view.high_security;
  return view.passkeys.filter((p) => !removing.has(p.passkey_id) && (!high || p.tier === "hardware")
    && view.credentials.some((c) => c.passkey_id === p.passkey_id && !isNamed(ring, c)));
}

/** Whether the roster `ring` opened names a credential the server lists: its id, its passkey and its key, all three;
 *  never for a ring without a roster. */
export const isNamed = (ring: Ring, c: E2ECredential) => !!ring.roster?.credentials.some((r) =>
  r.credential_id === c.credential_id && r.passkey_id === c.passkey_id && r.public_key === c.public_key);

/** Whether turning encryption on again makes a new master key: never while it is on, when nothing is turned on again;
 *  else when the server says one is due, or the roster names a credential that leaves it (rosterAfter). */
export const rekeyDue = (ring: Ring, view: E2EView) => !view.on && (view.rekey_due || rosterAfter(ring, view).leaving.length > 0);

/** The passkeys a revocation removes that could still cancel it while it waits (passkey ids, in the account's order),
 *  as the server decides who may (e2e_keys.cancellers, the second version): when it is signed by a passkey and leaves
 *  no passkey besides its signer, those it removes, so neither of two who each hold one passkey makes a change the
 *  other cannot stop; while high security is on, hardware keys alone count, here and as what is left. None when it goes
 *  with the recovery code's proof (`byCode`), which only the code cancels, and none for a revocation that leaves more
 *  than its signer. */
export function removedMayCancel(view: E2EView, what: RevokeWhat, byCode = false): string[] {
  if (byCode) return [];
  const keys = view.passkeys.filter((p) => !view.high_security || p.tier === "hardware");
  const removing = new Set(what.remove ?? []);
  const turnsHigh = !!what.highSecurity && !view.high_security;
  const gone = keys.filter((p) => removing.has(p.passkey_id) || (turnsHigh && p.tier !== "hardware"));
  // what is left is its signer's alone: a passkey that unlocks the keys and stays, which is one of them
  return keys.length - gone.length === 1 ? gone.map((p) => p.passkey_id) : [];
}

/** Which of the holds (the view's `holds`) a revocation waits on, as the server picks it: with the recovery code's proof
 *  (`byCode`), the code's (`recovery`); else the long one when it makes a new recovery code, removes no passkey (those it
 *  names, and those turning high security on leaves out, as R), or leaves no passkey that unlocks the keys but its
 *  signer where others did; else a change's (`change`). */
export function revocationHold(view: E2EView, what: RevokeWhat, byCode: boolean): "change" | "long" | "recovery" {
  if (byCode) return "recovery";
  const removes = new Set(what.remove ?? []);
  if (what.highSecurity && !view.high_security) for (const p of view.passkeys) if (p.tier !== "hardware") removes.add(p.passkey_id);
  const enrolled = new Set(view.credentials.map((c) => c.passkey_id));
  // signed without the code by a passkey that stays: one staying alone is its signer
  const leavesSignerAlone = enrolled.size > 1 && stayingMakers(view, what).staying.length === 1;
  return what.newCode || !removes.size || leavesSignerAlone ? "long" : "change";
}

const sameRoster = (a: C.Roster, b: C.Roster) => a.recovery === b.recovery && a.credentials.length === b.credentials.length
  && a.credentials.every((c, i) => c.credential_id === b.credentials[i].credential_id && c.passkey_id === b.credentials[i].passkey_id
    && c.public_key === b.credentials[i].public_key);

interface NewMaster { statement: Prepared; body: Record<string, unknown>; kept: RosterEntry[]; leaving: RosterEntry[] }

/** A content key's wrap under `m` (its row as the server lists it, opened by this tab to `EK`) made again under `next`,
 *  in the form `to` keeps it: a classical account's as the classical wrap of its PKCS#8,
 *  inside WebCrypto; a post-quantum account's as its seed's 60-byte wrap, or for a retired classical key its 32-byte
 *  X25519 scalar's 60-byte wrap with an AAD of its own. `from`, the form it is kept in now: a classical account's, which
 *  only the upgrade wraps again in a post-quantum account's form. Every secret byte read is zeroed, and the scalar of a
 *  classical key is trusted only once its key_id is the row's (C.scalarOf, C.openClassicalScalar). */
async function rewrap(m: C.Master, next: C.Master, row: E2EContentKey, EK: C.Bytes, account: string,
                      to: "pq" | "classical", from: "pq" | "classical" = to): Promise<string> {
  const keyId = row.key_id;
  if (to === "classical") {
    const ek = await C.unwrapContentKey(m, row.wrapped, keyId, account, true);
    if (!C.same(await C.publicOf(ek), EK)) throw new C.E2EError(BAD_CONTENT_KEY);
    return C.wrapContentKey(next, ek, keyId, account);
  }
  if (EK.length === C.HYBRID_KEY_LEN) {
    if (from !== "pq") throw new C.E2EError(BAD_CONTENT_KEY);
    const seed = await C.openContentSeed(m, row.wrapped, keyId, account);
    try {
      return await C.wrapContentSeed(next, seed, keyId, account);
    } finally {
      C.zero(seed);
    }
  }
  let scalar: C.Bytes;
  if (from === "pq") {
    scalar = await C.openClassicalScalar(m, row.wrapped, keyId, account);
  } else {
    const ek = await C.unwrapContentKey(m, row.wrapped, keyId, account, true);
    if (!C.same(await C.publicOf(ek), EK)) throw new C.E2EError(BAD_CONTENT_KEY);
    scalar = await C.scalarOf(ek, keyId);
  }
  try {
    return await C.wrapClassicalScalar(next, scalar, keyId, account);
  } finally {
    C.zero(scalar);
  }
}

/** A new master key and what goes with it, sealed only to the roster's keys that stay (rosterAfter)
 *  and to the recovery key, or the new code's: the chain link from the current one; a grant to each; every content key
 *  this tab holds, and only those, wrapped again under it, each checked against the public half it was verified with,
 *  and those the server lists under a master key off the tab's path, or under one on it that do not open, left as they
 *  are (abandon: none this page can trust or open, so one junk row cannot block every revocation); a new content key
 *  pair; the roster, which a ring without one cannot give (rosterOf: it is whom the new key is sealed to); and their
 *  statement, which retires every earlier key at once. Everything made that the page can open is opened again before it
 *  is given out, and each grant to another passkey's key, which it cannot, is checked to name that key. Refused from
 *  keys below this browser's floor, not the ones the server lists, or not holding the newest key stated; when the
 *  server lists a credential that stays which the roster does not name (unnamed: the server takes a new master key only
 *  granted to every one, and the page seals to none the roster lacks); when no passkey would unlock encryption after
 *  it, unless `what.onlyCode`; and when turning high security on keeps no hardware key. */
async function newMaster(ring: Ring, view: E2EView, what: RevokeWhat): Promise<NewMaster> {
  const { account } = ring;
  checkCurrent(ring, view, true);
  const named = rosterOf(ring);
  const { kept, leaving } = rosterAfter(ring, view, what);
  const lacking = unnamed(ring, view, what);
  if (lacking.length) throw new C.E2EError(unnamedWords(lacking.map((p) => p.name)));
  if (what.highSecurity && !kept.length) throw new C.E2EError(NO_HARDWARE_KEPT);
  if (!kept.length && !what.onlyCode) throw new C.E2EError(NONE_KEPT);
  const recovery = what.newCode ? await recoveryOf(what.newCode, account, ring.pq) : null;
  const roster: C.Roster = { credentials: kept, recovery: recovery ? C.b64u(recovery.RK) : named.recovery };
  const nextBytes = C.random(32);
  try {
    const next = await C.masterOf(nextBytes, account);
    const chain = await withMasterBytes(ring, view, (old) => C.sealChain(next, old, ring.master.id, account));
    const grants: { to: string; sealed: string }[] = [];
    const recipients = new Map<string, C.Bytes>();          // each grant's key, by its `to`
    for (const c of kept) {
      const pub = C.unb64u(c.public_key);
      if (!pub) throw new C.E2EError("The roster holds a key that is not one.");
      recipients.set(c.credential_id, pub);
      grants.push({ to: c.credential_id, sealed: await C.sealGrant(pub, nextBytes, account, c.credential_id, next.id) });
    }
    const RK = C.unb64u(roster.recovery);
    if (!RK) throw new C.E2EError("The roster holds a recovery key that is not one.");
    recipients.set(RECOVERY, RK);
    const recoveryGrant = await C.sealGrant(RK, nextBytes, account, RECOVERY, next.id);
    grants.push({ to: RECOVERY, sealed: recoveryGrant });
    // the content keys this tab verified, wrapped again under the new master key, each in its own form (rewrap)
    const rewrapped: { key_id: string; wrapped: string; EK: C.Bytes }[] = [];
    for (const [keyId, held] of ring.keys) {
      const row = view.content_keys.find((k) => k.key_id === keyId);
      const m = row ? ring.masters.get(row.mk_id) : undefined;
      if (!row || !m) throw new C.E2EError(KEYS_CHANGED);
      rewrapped.push({ key_id: keyId, wrapped: await rewrap(m, next, row, held.EK, account, ring.pq ? "pq" : "classical"), EK: held.EK });
    }
    const fresh = await newContentKey(next, account, ring.pq);
    const sealedRoster = await C.sealRoster(next, roster, account);
    const anchor = what.newCode ? await C.sealRecoveryAnchor(what.newCode, next.id, account) : null;

    // before anything is given out, everything made that the page can open opens: the chain link with the new key, to
    // the one it replaces; the grant to what unlocked this tab where it stays, and the recovery grant with the new code's
    // key; the new recovery anchor with the new code; each content key wrapped again, to the key it was; the roster, to
    // what it was meant to say. A grant to another passkey's key (or to the recovery key, the code not held here) it
    // cannot open: each names the key it is for
    const older = await C.openChain(next, chain, ring.master.id, account);
    try {
      if ((await C.masterOf(older, account)).id !== ring.master.id) throw new C.E2EError("The chain link does not hold the master key it replaces.");
    } finally {
      C.zero(older);
    }
    for (const g of grants) {
      if (C.envelopeKeyId(g.sealed) !== await C.keyIdOf(recipients.get(g.to)!)) throw new C.E2EError("A grant just made is not sealed to the key it is for.");
    }
    const mine = grants.find((g) => g.to === viaTo(ring.via) && (ring.via.kind === "credential" || !recovery));
    if (mine) {
      const { priv, pub } = viaKey(ring.via);
      await holdsMaster(priv, pub, mine.sealed, account, mine.to, next.id);
    }
    if (recovery) await holdsMaster(recovery.rk, recovery.RK, recoveryGrant, account, RECOVERY, next.id);
    if (what.newCode && anchor && await C.openRecoveryAnchor(what.newCode, anchor, account) !== next.id) {
      throw new C.E2EError("The new recovery anchor does not name the new master key.");
    }
    for (const k of rewrapped) {
      const opened = await openContentKey(next, { key_id: k.key_id, public_key: C.b64u(k.EK), mk_id: next.id, wrapped: k.wrapped }, account, ring.pq);
      forgetPriv(opened?.ek);
      if (!opened) throw new C.E2EError("A content key wrapped again does not hold the key it was.");
    }
    if (!sameRoster(await C.openRoster(next, sealedRoster, account), roster)) throw new C.E2EError("The new roster does not say what it was meant to.");

    const statement = await prepareStatement(view, "e2e-key", { EK: fresh.EK, keyId: fresh.keyId, retires: "all" });
    sameAccount(statement, account);
    const body: Record<string, unknown> = {
      content_key: fresh.row,
      mk_id: next.id,
      chain: { previous: ring.master.id, wrapped: chain },
      grants,
      roster: sealedRoster,
      content_keys: rewrapped.map((k) => ({ key_id: k.key_id, wrapped: k.wrapped })),
      ...kemOf(ring.pq),
    };
    // the content keys under a master key off this tab's path, and those under one on it that do not open: left as they
    // are, not wrapped again
    const abandon = [...ring.unreached, ...ring.unopened];
    if (abandon.length) body.abandon = abandon;
    if (recovery && anchor) body.recovery = { public_key: C.b64u(recovery.RK), anchor };
    return { statement, body, kept, leaving };
  } finally {
    C.zero(nextBytes);
    forgetPriv(recovery?.rk);
  }
}

/** A revocation: a new master key (newMaster), the passkeys it removes removed and high security
 *  turned on if it says so. It seals only to the roster's keys that stay, never to a key the server lists that the
 *  roster lacks. One touch signs it, as the server takes it: of a passkey that unlocks the keys and stays after it (a
 *  hardware key's while high security is on or turned on now) that may take the place of the change that waits (the
 *  view's cancel_by); or, with the recovery code's proof beside it, of any passkey that may sign, where none of those
 *  can (byCode) or the person gives the code. Sent with the code, it takes the place of the change that waits in one
 *  step, as the server allows it: cancelling that one with the code and then revoking leaves a gap in which a script
 *  that keeps placing changes places another. */
export class Revoke {
  private s: { statement: Prepared; body: Record<string, unknown> } | null = null;
  /** the passkeys whose credentials leave the roster, and those whose credentials stay (passkey ids) */
  leaving: string[] = [];
  kept: string[] = [];
  /** whether it goes only with the recovery code's proof (onlyWithCode): then any passkey that may sign it signs it, and
   *  it is sent with the proof (signAndSendWithCode), never without */
  readonly byCode: boolean;

  constructor(readonly ring: Ring, readonly view: E2EView, readonly what: RevokeWhat) {
    this.byCode = onlyWithCode(view, what);
  }

  async prepare(): Promise<void> {
    needsMlkem(this.ring, this.view);
    const why = refusal(this.view, stayingMakers(this.view, this.what), anySigners(this.view, this.what).length > 0);
    if (why) throw new C.E2EError(why);
    const made = await newMaster(this.ring, this.view, this.what);
    signersOf(made.statement, this.view, this.what, this.byCode);
    this.s = { statement: made.statement,
      body: { ...made.body, remove: this.what.remove ?? [], high_security: !!this.what.highSecurity } };
    this.leaving = made.leaving.map((c) => c.passkey_id);
    this.kept = made.kept.map((c) => c.passkey_id);
  }

  /** Signs and sends: call it first thing in the click's handler. With `proof`, the recovery code's proof made before
   *  it, any passkey that may sign signs it; one that goes only with the code (byCode), which the server refuses
   *  without it, is refused here, before any touch, without one. */
  async signAndSend(proof?: RecoveryProof | null): Promise<Answer> {
    if (!this.s) throw new C.E2EError("Prepare the revocation first.");
    if (this.byCode && !proof) throw new C.E2EError(CODE_NEEDED);
    const { signed, body } = await this.sign(!!proof);
    return E2EApi.revoke({ statement: signed, ...body, ...withProof(proof) });
  }

  /** Signs it, and sends it with the recovery code's proof, asked for after the touch and made with `rk`, the code's key
   *  (codeKey, or that of keys unlocked with the code): call it first thing in the click's handler. Sent so, it waits a
   *  day, as what the code confirms does, and only the code cancels it. */
  async signAndSendWithCode(rk: Priv): Promise<Answer> {
    const { signed, body } = await this.sign(true);
    return E2EApi.revoke({ statement: signed, ...body, recovery_proof: await proofFrom(rk, "revoke") });
  }

  /** The touch that signs it, of the passkeys that may (signersOf, with the code's proof or without), refused first,
   *  with nothing awaited, from keys below this browser's floor, which another tab may have raised since it was
   *  prepared. */
  private async sign(code: boolean): Promise<{ signed: Signed; body: Record<string, unknown> }> {
    if (!this.s) throw new C.E2EError("Prepare the revocation first.");
    aboveFloor(this.ring);
    return { signed: await signStatement(this.s.statement, signersOf(this.s.statement, this.view, this.what, code)), body: this.s.body };
  }
}

// ---- renewing anchors ----------------------------------------------------------------------------------------------

/** Renewing anchors: one touch of each credential, its own wrap opened and written again with the current mk_id as its
 *  anchor, so a master key kept from before (a removed credential with an old copy of the database) cannot fork past
 *  it; and the recovery code's anchor written again so too, with the code's anchor key (that of a tab unlocked with the
 *  code, or the code typed), since the code unlocks as a passkey does. The wrap is written under a fresh salt (the touch
 *  evaluates the old one and the new), so a PRF output given before opens the renewed record no more; the key it
 *  wraps stays the same, so such an output still opens the record from before, and with it that key, which every grant
 *  to the passkey opens: only removing the passkey (a revocation) ends that. Every touch is a confirmation of its own
 *  passkey, over a challenge derived from the server's (fetched before it, and again after each touch), as the server
 *  takes renewed anchors (ANCHOR_OWN): each renewed record but the confirming passkey's own carries its own passkey's
 *  confirmation, so no passkey rewrites another's record without that one's touch. The change is confirmed by one of
 *  the passkeys touched that unlocks the keys and stays and may take the place of the change that waits; where none
 *  may, by the recovery code's proof (byCode: the code given here, or the one this tab was unlocked with). Made only
 *  from keys at this browser's floor that the server lists: renewed to an older master key, the anchors would let one
 *  who holds that key fork past them. */
export class RenewAnchors {
  private ceremony: Ceremony | null = null;
  /** each touched passkey's confirmation, by passkey id, in the order they were touched */
  readonly confirmations = new Map<string, string>();
  readonly rows: { passkey_id: string; salt: string; wrapped: string; anchor?: string }[] = [];
  readonly done = new Set<string>();
  /** the recovery code's anchor written again, with the recovery key it is of; null when it was current already or
   *  not renewed */
  recovery: { public_key: string; anchor: string } | null = null;
  /** whether the recovery code's anchor was checked (and renewed where it was not current) */
  codeDone = false;
  /** the code's key, for its proof when no passkey touched may confirm the change */
  private rk: Priv | null = null;
  /** whether it goes only with the recovery code's proof: no passkey that unlocks the keys and stays may confirm it in
   *  the place of the change that waits (onlyWithCode) */
  readonly byCode: boolean;

  constructor(readonly ring: Ring, readonly view: E2EView) {
    this.byCode = onlyWithCode(view, {}, false);
  }

  /** The credentials to touch: those the roster names that the server lists whose passkeys unlock the keys and stay,
   *  the only ones whose own confirmation the server takes (a hardware key's while high security is on). */
  targets(): E2ECredential[] {
    return this.view.credentials.filter((c) => !!this.ring.roster?.credentials.some((r) => r.credential_id === c.credential_id)
      && staysEnrolled(this.view, c.passkey_id));
  }

  /** Whether the account's recovery code is the one the roster names, so its anchor is one to renew. */
  hasCode(): boolean {
    return !!this.view.recovery && !!this.ring.roster && this.view.recovery.public_key === this.ring.roster.recovery;
  }

  /** Whether the code's anchor is renewed with nothing typed: this tab was unlocked with the code in force. */
  codeHeld(): boolean {
    const { via } = this.ring;
    return via.kind === "recovery" && C.b64u(via.RK) === this.view.recovery?.public_key;
  }

  async prepare(): Promise<void> {
    needsMlkem(this.ring, this.view);
    checkCurrent(this.ring, this.view);
    rosterOf(this.ring);                      // whom it renews: the credentials the roster names
    const why = refusal(this.view, stayingMakers(this.view));
    if (why) throw new C.E2EError(why);
    await this.fetchCeremony();
  }

  /** The next touch's challenge, fetched: a failure leaves none, which that touch then says. */
  private async fetchCeremony(): Promise<void> {
    this.ceremony = await confirmCeremony("e2e-anchor-touch").catch(() => null);
  }

  /** The recovery code's anchor written again naming the current master key, under the code's anchor key: the one this
   *  tab was unlocked with (codeHeld), or of the code typed (`text`), checked to be the account's code in force. The
   *  anchor the server lists must open with it. No touch. */
  async renewCode(text?: string): Promise<void> {
    aboveFloor(this.ring);
    const { account, master } = this.ring;
    const served = this.view.recovery;
    if (!served || !this.hasCode()) throw new C.E2EError("This account's recovery code is not the one its keys name: read the keys again.");
    const via = this.codeHeld() ? (this.ring.via as Extract<Via, { kind: "recovery" }>)
      : await viaFromCode(this.view, account, text ?? "");
    let anchor: string;
    try {
      anchor = await C.openRecoveryAnchorWith(via.anchorKey, served.anchor, account);
    } catch {
      throw new C.E2EError(NOT_LEAD_BACK);    // a code typed opened this anchor already (viaFromCode): the tab's own
    }
    this.forgetCode();                        // a code typed before is held no more
    this.rk = via.rk;
    if (anchor !== master.id) {
      const sealed = await C.sealRecoveryAnchorWith(via.anchorKey, master.id, account);
      // before it is sent: the renewed anchor opens with the code's key, to the current master key
      if (await C.openRecoveryAnchorWith(via.anchorKey, sealed, account) !== master.id) throw new C.E2EError("The renewed recovery anchor does not name the master key.");
      this.recovery = { public_key: served.public_key, anchor: sealed };
    }
    this.codeDone = true;
  }

  /** One credential's touch, its passkey's own confirmation, over the challenge derived from the server's: call it first
   *  thing in the click's handler. The next touch's challenge is fetched as it ends. */
  async touch(credentialId: string): Promise<void> {
    aboveFloor(this.ring);
    const row = this.targets().find((c) => c.credential_id === credentialId);
    const salt = row ? C.unb64u(row.salt) : null;
    const listed = this.ring.roster?.credentials.find((c) => c.credential_id === credentialId);
    if (!row || !salt || !listed) throw new C.E2EError("This passkey has no key to renew.");
    const ceremony = this.ceremony;
    if (!ceremony) {
      void this.fetchCeremony();
      throw new C.E2EError(BEING_PREPARED);
    }
    const fresh = C.random(32);
    const t = await prfTouch({ challenge: ceremony.ask.challenge, rpId: ceremony.ask.rpId, allow: [{ credentialId, salt, second: fresh }],
      doing: "renew", keys: securityKeyIds(this.view) });
    this.ceremony = null;
    try {
      await this.renew(t, ceremony.challenge_id, row, listed, fresh);
    } finally {
      await this.fetchCeremony();
    }
  }

  /** What a touch gave, used: its confirmation, and the passkey's record opened and, where it does not name the current
   *  master key, written again under the fresh salt's key. */
  private async renew(t: Touch, challengeId: string, row: E2ECredential, listed: RosterEntry, fresh: C.Bytes): Promise<void> {
    const { account, master } = this.ring;
    const credentialId = row.credential_id;
    const kek = await C.credentialKek(t.prf, account, credentialId);
    const renewed = await C.credentialKek(t.second!, account, credentialId);
    if (this.ring.pq) {
      await this.renewSeed(t, challengeId, row, listed, fresh, kek, renewed);
      return;
    }
    let opened: { anchor: string; der: C.Bytes };
    try {
      opened = await C.openCredentialDer(kek, row.wrapped, account, credentialId);
    } catch {
      throw new C.E2EError(OWN_RECORD);
    }
    let made: { passkey_id: string; salt: string; wrapped: string } | null = null;
    try {
      const ck = await C.privateFromDer(opened.der);
      if (C.b64u(await C.publicOf(ck)) !== listed.public_key) throw new C.E2EError(NOT_LISTED);
      if (opened.anchor !== master.id && !this.done.has(credentialId)) {
        const wrapped = await C.sealCredentialDer(renewed, opened.der, master.id, account, credentialId);
        // before it is sent: the renewed record opens under the fresh salt's key, to this key and the current anchor
        const back = await C.openCredential(renewed, wrapped, account, credentialId);
        if (back.anchor !== master.id || C.b64u(await C.publicOf(back.ck)) !== listed.public_key) throw new C.E2EError(OWN_RECORD);
        made = { passkey_id: row.passkey_id, salt: C.b64u(fresh), wrapped };
      }
    } finally {
      C.zero(opened.der);
    }
    // its confirmation, kept only once its own record was checked, and before its renewed record is kept
    this.confirmations.set(row.passkey_id, (await E2EApi.confirm(challengeId, t.credential)).confirmation);
    if (made) this.rows.push(made);
    this.done.add(credentialId);
  }

  /** renew, for a post-quantum credential: its seed's wrap opened with the anchor stored beside it, the seed's key the
   *  roster's (both halves, byte for byte), and, where that anchor is not the current master key, the seed wrapped again
   *  under the fresh salt's key with the current master key as its anchor, which opens again to the same key before it
   *  is kept. */
  private async renewSeed(t: Touch, challengeId: string, row: E2ECredential, listed: RosterEntry, fresh: C.Bytes,
                          kek: CryptoKey, renewed: CryptoKey): Promise<void> {
    const { account, master } = this.ring;
    const credentialId = row.credential_id;
    const pub = C.unb64u(listed.public_key);
    const anchor = row.anchor;
    if (!pub || pub.length !== C.HYBRID_KEY_LEN || typeof anchor !== "string" || !MK_ID.test(anchor)) throw new C.E2EError(OWN_RECORD);
    let seed: C.Bytes;
    try {
      seed = await C.openCredentialSeed(kek, row.wrapped, anchor, account, credentialId);
    } catch {
      throw new C.E2EError(OWN_RECORD);
    }
    let made: { passkey_id: string; salt: string; wrapped: string; anchor: string } | null = null;
    try {
      const key = await C.hybridFromSeed(seed);
      const same = C.same(C.hybridPublic(key), pub);
      C.forgetHybrid(key);
      if (!same) throw new C.E2EError(NOT_LISTED);
      if (anchor !== master.id && !this.done.has(credentialId)) {
        const wrapped = await C.sealCredentialSeed(renewed, seed, master.id, account, credentialId);
        // before it is sent: the renewed record opens under the fresh salt's key and the current anchor, to this key
        forgetPriv(await openOwnRecord(renewed, wrapped, master.id, account, credentialId, pub).then((k) => {
          if (!k) throw new C.E2EError(OWN_RECORD);
          return k;
        }));
        made = { passkey_id: row.passkey_id, salt: C.b64u(fresh), wrapped, anchor: master.id };
      }
    } finally {
      C.zero(seed);
    }
    this.confirmations.set(row.passkey_id, (await E2EApi.confirm(challengeId, t.credential)).confirmation);
    if (made) this.rows.push(made);
    this.done.add(credentialId);
  }

  /** The passkeys to touch whose own confirmation may confirm the change (passkey ids): of targets(), those that may
   *  take the place of the change that waits. */
  mayConfirm(): string[] {
    return this.targets().filter((c) => inPlaceBy(this.view, c.passkey_id)).map((c) => c.passkey_id);
  }

  /** The passkey whose confirmation confirms the change: the first touched that unlocks the keys and stays and may take
   *  the place of the change that waits; null when none may. */
  maker(): string | null {
    return [...this.confirmations.keys()].find((id) => staysEnrolled(this.view, id) && inPlaceBy(this.view, id)) ?? null;
  }

  /** The passkey whose confirmation the change goes with: the maker, unless the code's anchor is renewed, which goes
   *  only with the code's proof; null when no passkey's does. */
  confirmedBy(): string | null {
    return this.recovery ? null : this.maker();
  }

  /** The renewed wraps, and the code's renewed anchor, sent in one request: each renewed record but the maker's with its
   *  own passkey's confirmation, and the change confirmed by the maker's, or, where no passkey touched may, by the
   *  recovery code's proof when the code was given and may. The code's renewed anchor goes only with the code's proof,
   *  which no passkey's confirmation covers (the server's ANCHOR_CODE): then no passkey is the maker, every renewed
   *  record carries its own confirmation, and the change waits the code's hold, which only the code cancels. Else
   *  refused: TOUCH_ONE_THAT_MAY while a passkey that may is still untouched, WAITING_CHANGE where the code cannot
   *  either, CODE_NEEDED until the code is given. Null when every anchor renewed was current already. */
  async send(): Promise<Answer | null> {
    aboveFloor(this.ring);
    if (!this.rows.length && !this.recovery) return null;
    const maker = this.confirmedBy();
    let word: { confirmation: string } | { recovery_proof: RecoveryProof };
    if (maker) word = { confirmation: this.confirmations.get(maker)! };
    else if (this.rk && codeMakes(this.view)) word = { recovery_proof: await proofFrom(this.rk, "anchors") };
    else if (!this.recovery && this.mayConfirm().length) throw new C.E2EError(TOUCH_ONE_THAT_MAY);
    else if (!codeMakes(this.view)) throw new C.E2EError(WAITING_CHANGE);
    else throw new C.E2EError(CODE_NEEDED);
    const anchors = this.rows.map((r) => (r.passkey_id === maker ? r : { ...r, confirmation: this.confirmations.get(r.passkey_id)! }));
    return E2EApi.anchors({ anchors, ...(this.recovery ? { recovery_anchor: this.recovery } : {}), mk_id: this.ring.master.id, ...word, ...kemOf(this.ring.pq) });
  }

  /** The code's key put away, when it was typed: the tab's own, when the code unlocked it, stays with the tab. */
  private forgetCode() {
    const own = this.ring.via.kind === "recovery" ? this.ring.via.rk : null;
    if (this.rk !== own) forgetPriv(this.rk);
    this.rk = null;
  }

  /** Drop what was given: the code's key, when it was typed. */
  dispose() {
    this.forgetCode();
  }
}

// ---- turning it off ------------------------------------------------------------------------------------------------

/** Turning encryption off: a signed e2e-off, the next of the chain, with no key. Signed, as the server takes it, by a
 *  passkey that unlocks the keys and stays, one that may take the place of the change that waits; or, with the recovery
 *  code's proof beside it, by any passkey that may sign, where none of those can (byCode) or the person gives the code. */
export class TurnOff {
  private statement: Prepared | null = null;
  /** whether it goes only with the recovery code's proof (onlyWithCode) */
  readonly byCode: boolean;

  constructor(readonly view: E2EView, readonly account: string) {
    this.byCode = onlyWithCode(view);
  }

  async prepare(): Promise<void> {
    notRolledBack(this.account, this.view);   // no change is made from keys older than this browser saw
    needsMlkem(null, this.view);              // it has no keys open: the view says whether they are post-quantum
    const why = refusal(this.view, stayingMakers(this.view), anySigners(this.view).length > 0);
    if (why) throw new C.E2EError(why);
    this.statement = await prepareStatement(this.view, "e2e-off");
    sameAccount(this.statement, this.account);
    signersOf(this.statement, this.view, {}, this.byCode);
  }

  /** Signs and sends: call it first thing in the click's handler. With `proof`, the recovery code's proof made before it,
   *  any passkey that may sign signs it; one that goes only with the code (byCode) is refused here, before any touch,
   *  without one. */
  async signAndSend(proof?: RecoveryProof | null): Promise<Answer> {
    if (!this.statement) throw new C.E2EError("Prepare turning it off first.");
    if (this.byCode && !proof) throw new C.E2EError(CODE_NEEDED);
    const signed = await signStatement(this.statement, signersOf(this.statement, this.view, {}, !!proof));
    return E2EApi.off({ statement: signed, ...withProof(proof), ...kemOf(this.view.pq) });
  }

  /** Signs it, and sends it with the recovery code's proof, asked for after the touch and made with `rk`, the code's key
   *  (codeKey): call it first thing in the click's handler. */
  async signAndSendWithCode(rk: Priv): Promise<Answer> {
    if (!this.statement) throw new C.E2EError("Prepare turning it off first.");
    const signed = await signStatement(this.statement, signersOf(this.statement, this.view, {}, true));
    return E2EApi.off({ statement: signed, recovery_proof: await proofFrom(rk, "off"), ...kemOf(this.view.pq) });
  }
}

// ---- the upgrade to post-quantum keys -----------------------------------------------

export const UPGRADE_NOT_CLASSICAL = "These keys are post-quantum already.";
export const UPGRADE_OFF = "Encryption is off: turn it on again first, then upgrade; or start again, which makes post-quantum keys.";
export const UPGRADE_WAITING = "A change to these keys waits: cancel it, or let it take effect, before upgrading.";
export const UPGRADE_NOT_SEAT = "This passkey is not one the upgrade keeps: it does not unlock encryption, or the roster of these keys does not name it.";
export const UPGRADE_AGAIN = "This passkey's new key was made once already: start the upgrade again.";
export const UPGRADE_CODE_FIRST = "Give the recovery code you have, or make a new one, first.";
export const UPGRADE_CODE_SHOWN = "Type back the four characters asked for first.";
export const UPGRADE_STALE = "It was prepared over four and a half minutes ago, and what the server gave for it lasts five: prepare it again, then touch each passkey to keep again.";
export const UPGRADE_PREPARED_AGAIN = "It was prepared again meanwhile: touch each passkey to keep again.";
/** How long after it is prepared the upgrade may be touched and signed: the server's challenges for it (the statement's
 *  nonce, each touch's confirmation) last five minutes (passkeys.CHALLENGE_TTL); half a minute is left for the last
 *  prompt and the request. */
export const UPGRADE_WINDOW_MS = 270_000;

/** What one passkey that stays through the upgrade is given, and its touch's result: a fresh X-Wing key's seed held
 *  wrapped until its touch wraps it under the fresh salt's key, with the new master key as its anchor; the new master
 *  key's grant to its public key, sealed as the upgrade is prepared; the subject its confirmation is made for. */
interface UpgradeSeat {
  passkey: E2EPasskey; credential: E2ECredential; listed: RosterEntry; salt: C.Bytes; pub: C.Bytes; held: HeldSecret | null;
  grant: string; subject: string; ceremony: Ceremony | null;
  /** the touch's result, sent as the body's credentials entry */
  made?: { passkey_id: string; salt: string; public_key: string; wrapped: string; anchor: string; confirmation?: string };
  key?: Priv;
}

/** What one preparation of the upgrade made: the new master key, the statement, the new content key, the chain link,
 *  every content key wrapped again, and the recovery grant and anchor. A touch made for one preparation is used for it
 *  alone. */
interface UpgradeMade {
  next: C.Master; statement: Prepared; content: ContentKeyRow; chain: string; rewrapped: { key_id: string; wrapped: string }[];
  recoveryGrant: string; anchor: string;
}

/** The subject a staying passkey's upgrade touch is confirmed for: "e2e-upgrade:" and the first 16 bytes, as 32 hex, of
 *  the sha256 of the RFC 8785 JSON of its credential id, the new master key's mk_id, its new public key and fresh salt
 *  (base64url, as the body sends them), and the hex sha256 of the version-3 statement's canonical bytes: what exists
 *  before its touch. Its wrap, which comes from the touch, is not bound. */
export async function upgradeSubject(o: { credential_id: string; mk_id: string; public_key: string; salt: string; statement: Record<string, unknown> }): Promise<string> {
  return `e2e-upgrade:${await upgradeBinding(o)}`;
}

/** The 32 hex of a staying passkey's upgrade subject (upgradeSubject). */
async function upgradeBinding(o: { credential_id: string; mk_id: string; public_key: string; salt: string; statement: Record<string, unknown> }): Promise<string> {
  const statement = C.hex(await C.sha256(C.canonicalBytes(o.statement)));
  const bound = { credential_id: o.credential_id, mk_id: o.mk_id, public_key: o.public_key, salt: o.salt, statement };
  return C.hex((await C.sha256(C.canonicalBytes(bound))).subarray(0, 16));
}

/** The links that have not said they can seal post-quantum: they hold their texts once the upgrade takes
 *  effect, until updated. Their word alone, guidance, never a check. */
export const linksNotPq = (view: E2EView) => view.links.filter((l) => !l.seals?.includes(2));

/** The upgrade of a classical account to post-quantum keys, from keys unlocked in this
 *  tab: one revocation, made in one sitting, with `upgrade: true`. The recovery code first, as the person chose: the
 *  one they have, typed and checked against the recovery key (keepCode), or a new one, shown once with four characters
 *  typed back (newCode). Then (prepare) a new master key, the chain link from the current one, a new X-Wing content
 *  key and its version-3 statement retiring every earlier key at once, every content key this tab holds wrapped again
 *  in its 60-byte form (a classical key as its X25519 scalar), and for each passkey that may stay (those the roster
 *  names that stay) a fresh X-Wing key and a fresh salt, the new master key's grant to it, and the subject its touch's
 *  confirmation is made for (upgradeSubject). Then one touch of each passkey to keep but the one that signs (touch): its
 *  PRF under its current salt opens its current record, to the key the roster names, and under the fresh salt wraps the
 *  new seed with the new master key as its anchor, checked to open to that key and the grant to it to open to the new
 *  master key; the touch is that passkey's own confirmation, for the subject bound to it. Then the signature, whose
 *  touch keeps its passkey the same way (its signature is its confirmation), and the request (signAndSend): every
 *  passkey that unlocks encryption and was neither touched nor signed with removed, the roster under the new master key
 *  naming the kept passkeys' new keys and the new recovery key, every grant sealed to the key the roster names. A kept
 *  code goes with two proofs: the classical one, against the stored classical recovery key (recovery_proof), and the
 *  hybrid one, to the new key (new_recovery_proof). It waits a day (the long hold, or the code's when the code's proof
 *  comes with it); nothing of it is in force until it takes effect, and, as for any revocation that waits (the server's
 *  rule (v)), a change made by whoever may cancel it takes its place and ends it, and nothing changes beside it. It is
 *  made only while no other change waits (stricter than the server, which takes it in the place of one it may). */
export class Upgrade {
  /** the passkeys that may stay, each touched or not yet, in the account's order */
  seats: UpgradeSeat[] = [];
  /** the new recovery code's bytes, when one is made, and which group of four is typed back */
  readonly newCode: C.Bytes | null;
  readonly group = someGroup();
  private code: { bytes: C.Bytes; classical: Priv; hybrid: Priv; RK: C.Bytes; shown: boolean } | null = null;
  private s: UpgradeMade | null = null;
  /** when it was last prepared (Date.now()): it is touched and signed within UPGRADE_WINDOW_MS of then */
  private preparedAt = 0;
  /** preparations begun: one overtaken by a later one keeps nothing it made */
  private preparing = 0;

  /** `keep`: the person keeps the recovery code they have (typed with useCode); else a new one is made. `view`, the
   *  account as read; prepare takes a newer one. */
  constructor(readonly ring: Ring, public view: E2EView, readonly keep: boolean) {
    this.newCode = keep ? null : C.random(C.CODE_BYTES);
  }

  /** The milliseconds left to touch and sign what was prepared (UPGRADE_WINDOW_MS from prepare); 0 when nothing is
   *  prepared or the time is up, and it is then prepared again. */
  left(): number {
    return this.s ? Math.max(0, this.preparedAt + UPGRADE_WINDOW_MS - Date.now()) : 0;
  }

  /** The new code as it is shown, once. */
  codeText(): string {
    if (!this.newCode) throw new C.E2EError(UPGRADE_CODE_FIRST);
    return C.recoveryText(this.newCode);
  }

  /** The kept code as typed, checked to be the account's: its classical key the recovery key the server lists (and the
   *  roster names), as unlocking with it checks; its X-Wing key derived from it. Or, for a new code, the four
   *  characters typed back. */
  async useCode(text: string): Promise<void> {
    this.forgetCode();
    let bytes: C.Bytes | null = null;
    let hybrid: Priv | null = null;
    try {
      if (this.keep) {
        bytes = C.recoveryBytes(text);
        if (!bytes) throw new C.E2EError(CODE_FORM);
        const classical = await recoveryOf(bytes, this.ring.account, false);
        if (!this.view.recovery || C.b64u(classical.RK) !== this.view.recovery.public_key || C.b64u(classical.RK) !== rosterOf(this.ring).recovery) {
          throw new C.E2EError(WRONG_CODE);
        }
        const made = await recoveryOf(bytes, this.ring.account, true);
        hybrid = made.rk;
        this.code = { bytes, classical: classical.rk, hybrid, RK: made.RK, shown: true };
        return;
      }
      const want = this.codeText().split(" ")[this.group];
      if (text.replace(/[\s-]+/g, "").toUpperCase() !== want) throw new C.E2EError("Those are not the four characters asked for.");
      bytes = this.newCode!.slice();
      const made = await recoveryOf(bytes, this.ring.account, true);
      hybrid = made.rk;
      this.code = { bytes, classical: hybrid, hybrid, RK: made.RK, shown: true };
    } finally {
      // whatever did not become the code (a wrong one, or a failure part way) is not kept
      if (!this.code) {
        if (bytes) C.zero(bytes);
        forgetPriv(hybrid);
      }
    }
  }

  /** Why it cannot be prepared from `view`, before anything is made: the account post-quantum already, encryption off,
   *  no ML-KEM on the server, or a change waiting. */
  protected readiness(view: E2EView): void {
    if (view.pq || this.ring.pq) throw new C.E2EError(UPGRADE_NOT_CLASSICAL);
    if (!view.on) throw new C.E2EError(UPGRADE_OFF);
    if (!view.mlkem) throw new C.E2EError(NO_MLKEM);
    if (view.pending) throw new C.E2EError(UPGRADE_WAITING);
  }

  /** The passkeys that unlock encryption and are not touched: the upgrade removes them from the account (passkey
   *  ids, in the account's order). Before any touch, every one that unlocks encryption. */
  removes(): string[] {
    const touched = new Set(this.seats.filter((s) => s.made).map((s) => s.passkey.passkey_id));
    const enrolled = new Set(this.view.credentials.map((c) => c.passkey_id));
    return this.view.passkeys.filter((p) => enrolled.has(p.passkey_id) && !touched.has(p.passkey_id)).map((p) => p.passkey_id);
  }

  /** Prepares it (again, from `view`, the account as read now, where given): what was prepared before is dropped, with
   *  any touch made for it, and the code given is kept. Each touch and the signature follow within UPGRADE_WINDOW_MS. */
  async prepare(view: E2EView = this.view): Promise<void> {
    this.view = view;
    const { ring } = this;
    const { account } = ring;
    this.readiness(view);
    // the code as given now: one given again meanwhile (useCode) leaves this preparation nothing
    const code = this.code;
    if (!code) throw new C.E2EError(this.keep ? UPGRADE_CODE_FIRST : UPGRADE_CODE_SHOWN);
    checkCurrent(ring, view, true);
    rosterOf(ring);
    const lacking = unnamed(ring, view, {});
    if (lacking.length) throw new C.E2EError(unnamedWords(lacking.map((p) => p.name)));
    this.dispose(false);
    this.preparedAt = Date.now();
    const gen = ++this.preparing;
    const nextBytes = C.random(32);
    const seats: UpgradeSeat[] = [];
    try {
      const next = await C.masterOf(nextBytes, account);
      const chain = await withMasterBytes(ring, view, (old) => C.sealChain(next, old, ring.master.id, account));
      // every content key this tab verified, wrapped again under the new master key in a post-quantum account's form
      const rewrapped: { key_id: string; wrapped: string }[] = [];
      for (const [keyId, held] of ring.keys) {
        const row = view.content_keys.find((k) => k.key_id === keyId);
        const m = row ? ring.masters.get(row.mk_id) : undefined;
        if (!row || !m) throw new C.E2EError(KEYS_CHANGED);
        rewrapped.push({ key_id: keyId, wrapped: await rewrap(m, next, row, held.EK, account, "pq", "classical") });
      }
      const content = await newContentKey(next, account, true);
      const statement = await prepareStatement(view, "e2e-key", { EK: content.EK, keyId: content.keyId, retires: "all" });
      sameAccount(statement, account);
      // a fresh X-Wing key and a fresh salt for each passkey that may stay, and the new master key's grant to it
      for (const listed of rosterAfter(ring, view, {}).kept) {
        const passkey = view.passkeys.find((p) => p.passkey_id === listed.passkey_id);
        const credential = view.credentials.find((c) => c.credential_id === listed.credential_id);
        if (!passkey || !credential) continue;
        const made = await newHeldKey(true);
        const salt = C.random(32);
        const grant = await C.sealGrant(made.pub, nextBytes, account, listed.credential_id, next.id);
        const binding = await upgradeBinding({ credential_id: listed.credential_id, mk_id: next.id, public_key: C.b64u(made.pub),
          salt: C.b64u(salt), statement: statement.payload });
        // the change its confirmation is made for, as the server names it (passkeys.CHANGE)
        seats.push({ passkey, credential, listed, salt, pub: made.pub, held: made.held, grant, subject: `e2e-upgrade:${binding}`, ceremony: null });
      }
      if (!seats.length) throw new C.E2EError(NONE_TO_SIGN);
      const recoveryGrant = await C.sealGrant(code.RK, nextBytes, account, RECOVERY, next.id);
      const anchor = await C.sealRecoveryAnchor(code.bytes, next.id, account);
      if (gen !== this.preparing || this.code !== code) throw new C.E2EError(UPGRADE_PREPARED_AGAIN);
      this.s = { next, statement, content: content.row, chain, rewrapped, recoveryGrant, anchor };
      this.seats = seats;
    } catch (e) {
      for (const x of seats) x.held?.drop();          // nothing it made is kept by a preparation that did not end
      throw e;
    } finally {
      C.zero(nextBytes);
    }
    await Promise.all(seats.map((s) => this.fetchCeremony(s)));
  }

  /** A seat's touch's challenge, fetched (again): its options asked for the subject bound to it. A failure leaves none,
   *  which its touch then says. */
  private async fetchCeremony(seat: UpgradeSeat): Promise<void> {
    seat.ceremony = await confirmCeremony("e2e-upgrade-touch", false, { change: seat.subject }).catch(() => null);
  }

  /** One passkey's touch, its own confirmation: call it first thing in the click's handler. Its PRF under its current
   *  salt must open its current record to the key the roster names (so it is the roster's passkey, and its PRF is
   *  stable in this browser); under the fresh salt it wraps its new seed, with the new master key as its anchor, which
   *  must open again to the new key, and the grant to it to the new master key (keepSeat). Its confirmation is asked for only
   *  then. */
  async touch(credentialId: string): Promise<void> {
    const s = this.inTime();
    aboveFloor(this.ring);
    const seat = this.seats.find((x) => x.credential.credential_id === credentialId);
    const current = seat ? C.unb64u(seat.credential.salt) : null;
    if (!seat || !current) throw new C.E2EError(UPGRADE_NOT_SEAT);
    if (seat.made) return;
    const ceremony = seat.ceremony;
    if (!ceremony) {
      void this.fetchCeremony(seat);
      throw new C.E2EError(BEING_PREPARED);
    }
    const t = await prfTouch({ challenge: ceremony.ask.challenge, rpId: ceremony.ask.rpId, doing: "upgrade", keys: securityKeyIds(this.view),
      allow: [{ credentialId, salt: current, second: seat.salt }] });
    seat.ceremony = null;
    try {
      await this.keepSeat(s, seat, t, ceremony.challenge_id);
    } finally {
      // touched again, with a fresh challenge, where its seed is still as it was
      if (this.s === s && !seat.made && seat.held) void this.fetchCeremony(seat);
    }
  }

  /** What a touch of `seat`'s passkey gave, for the preparation `s`, used (its PRF output under its current salt, and
   *  under its fresh salt): its current record opened to the key the roster names; then, for a touch of its own
   *  (`challengeId`), its confirmation, asked for before its new seed is used, so one the server no longer takes (its
   *  challenge ended) leaves the seat as it was, to be touched again; then its new seed wrapped under the fresh salt's
   *  key with the new master key as its anchor, opened again to its new key, and the grant to that key opened to the new
   *  master key. The statement's signer's touch is its signature, and needs no confirmation (the server's reading:
   *  e2e_keys._upgrade_touches). Its new seed can be wrapped only once: a touch that fails after that leaves the seat
   *  unusable, and the upgrade is prepared again. A preparation made meanwhile (or the flow disposed of) keeps nothing
   *  of it. */
  private async keepSeat(s: UpgradeMade, seat: UpgradeSeat, t: Touch, challengeId: string | null): Promise<void> {
    const { account } = this.ring;
    const credentialId = seat.credential.credential_id;
    // both outputs made keys of at once (credentialKek zeroes them), so neither stays whatever follows
    const kek = await C.credentialKek(t.prf, account, credentialId);
    const fresh = await C.credentialKek(t.second!, account, credentialId);
    const ours = () => {
      if (this.s !== s) throw new C.E2EError(UPGRADE_PREPARED_AGAIN);
    };
    let opened: { ck: CryptoKey; anchor: string };
    try {
      opened = await C.openCredential(kek, seat.credential.wrapped, account, credentialId);
    } catch {
      throw new C.E2EError(OWN_RECORD);
    }
    if (C.b64u(await C.publicOf(opened.ck)) !== seat.listed.public_key) throw new C.E2EError(NOT_LISTED);
    if (!seat.held) throw new C.E2EError(UPGRADE_AGAIN);
    const confirmation = challengeId ? (await E2EApi.confirm(challengeId, t.credential)).confirmation : null;
    ours();
    const held = seat.held;
    if (!held) throw new C.E2EError(UPGRADE_AGAIN);           // kept by another touch meanwhile
    seat.held = null;
    const wrapped = await held.seal(fresh, s.next.id, account, credentialId);
    const key = await openOwnRecord(fresh, wrapped, s.next.id, account, credentialId, seat.pub);
    if (!key) throw new C.E2EError(UNSTABLE);
    try {
      await holdsMaster(key, seat.pub, seat.grant, account, credentialId, s.next.id);
      ours();
      seat.made = { passkey_id: seat.passkey.passkey_id, salt: C.b64u(seat.salt), public_key: C.b64u(seat.pub), wrapped, anchor: s.next.id,
        ...(confirmation ? { confirmation } : {}) };
      seat.key = key;
    } catch (e) {
      forgetPriv(key);
      throw e;
    }
  }

  /** The passkeys the statement may be signed with: every passkey the upgrade may keep (signing one keeps it), of them
   *  those the server names when it names any (its nonce's credentials). Once a signature kept a passkey and what it
   *  signed was not taken, that passkey alone: it has no confirmation of its own (its signature was its confirmation),
   *  so the server takes it in the upgrade only as its signer. */
  signers(): string[] {
    const named = this.s?.statement.allow ?? [];
    return this.signing().filter((x) => inPlaceBy(this.view, x.passkey.passkey_id))
      .map((x) => x.credential.credential_id).filter((id) => !named.length || named.includes(id));
  }

  /** The seats whose passkey may sign it, before the server's word on which: every one the upgrade may keep, or the one
   *  whose signature kept it before (signers). */
  protected signing(): UpgradeSeat[] {
    const signedBefore = this.seats.filter((x) => x.made && !x.made.confirmation);
    return signedBefore.length ? signedBefore : this.seats.filter((x) => x.made || x.held);
  }

  /** What refuses the signature before its touch, beyond the time and this browser's floor: nothing, for the upgrade. */
  protected beforeSign(): void {}

  /** What the recovery code's proofs are made for: the change's purpose. */
  protected proofPurpose(): RecoveryPurpose {
    return "revoke";
  }

  /** The request, sent: a revocation, removing every passkey neither touched nor signed with. */
  protected post(body: Record<string, unknown>): Promise<Answer> {
    return E2EApi.revoke({ ...body, remove: this.removes(), high_security: false });
  }

  /** The signature, and everything checked and sent in one request: call it first thing in the click's handler. The
   *  touch that signs is of a passkey the upgrade may keep, and keeps it (its PRF evaluated in the same ceremony, as
   *  turning encryption on does), so the last passkey to keep needs no touch of its own.
   *  Every passkey that unlocks encryption and was neither touched nor signed with is removed. Returns the server's
   *  answer (the hold) and what will unlock the keys it puts in place (`via`: the signer's new key, for this tab's
   *  memory only, which opens them once the hold has ended). */
  async signAndSend(): Promise<{ answer: Answer; via: Via }> {
    const s = this.inTime();
    const code = this.code;
    if (!code) throw new C.E2EError(UPGRADE_CODE_FIRST);
    aboveFloor(this.ring);
    this.beforeSign();
    const allow = this.signers();
    if (!allow.length) throw new C.E2EError(NONE_TO_SIGN);
    const t = await prfTouch({ challenge: s.statement.challenge, rpId: s.statement.rpId, doing: "upgrade", keys: securityKeyIds(this.view),
      allow: allow.map((id) => {
        const seat = this.seats.find((x) => x.credential.credential_id === id)!;
        return { credentialId: id, salt: C.unb64u(seat.credential.salt)!, ...(seat.made ? {} : { second: seat.salt }) };
      }) });
    if (this.s !== s) {
      C.zero(t.prf, t.second);
      throw new C.E2EError(UPGRADE_PREPARED_AGAIN);
    }
    const signer = this.seats.find((x) => x.credential.credential_id === t.credentialId)!;
    if (signer.made) C.zero(t.prf, t.second);
    else await this.keepSeat(s, signer, t, null);
    const signed: Signed = { payload: s.statement.payload, credential: t.credential };
    const kept = this.seats.filter((x) => x.made);
    const { account } = this.ring;
    const roster: C.Roster = {
      credentials: kept.map((x) => ({ credential_id: x.credential.credential_id, passkey_id: x.passkey.passkey_id, public_key: x.made!.public_key })),
      recovery: C.b64u(code.RK),
    };
    const sealedRoster = await C.sealRoster(s.next, roster, account);
    // before anything is sent: the chain link opens to the current master key; the recovery grant with the new code's
    // key, and the anchor with the code; every content key wrapped again, to the key it was; the roster, to what it was
    // meant to say (each passkey's grant was opened with its new key as it was kept)
    const older = await C.openChain(s.next, s.chain, this.ring.master.id, account);
    try {
      if ((await C.masterOf(older, account)).id !== this.ring.master.id) throw new C.E2EError("The chain link does not hold the master key it replaces.");
    } finally {
      C.zero(older);
    }
    await holdsMaster(code.hybrid, code.RK, s.recoveryGrant, account, RECOVERY, s.next.id);
    if (await C.openRecoveryAnchor(code.bytes, s.anchor, account) !== s.next.id) throw new C.E2EError("The new recovery anchor does not name the new master key.");
    for (const k of s.rewrapped) {
      const EK = this.ring.keys.get(k.key_id)!.EK;
      const opened = await openContentKey(s.next, { key_id: k.key_id, public_key: C.b64u(EK), mk_id: s.next.id, wrapped: k.wrapped }, account, true);
      forgetPriv(opened?.ek);
      if (!opened) throw new C.E2EError("A content key wrapped again does not hold the key it was.");
    }
    if (!sameRoster(await C.openRoster(s.next, sealedRoster, account), roster)) throw new C.E2EError("The new roster does not say what it was meant to.");
    const purpose = this.proofPurpose();
    const proofs = this.keep ? {
      // the code is the account's (the classical proof against the stored classical key), and whoever sends this holds
      // the new key (the hybrid proof, to the body's new recovery key)
      recovery_proof: await proofFrom(code.classical, purpose),
      new_recovery_proof: await proofFrom(code.hybrid, purpose, code.RK),
    } : {};
    const abandon = [...this.ring.unreached, ...this.ring.unopened];
    const answer = await this.post({
      statement: signed,
      content_key: s.content,
      mk_id: s.next.id,
      chain: { previous: this.ring.master.id, wrapped: s.chain },
      grants: [...kept.map((x) => ({ to: x.credential.credential_id, sealed: x.grant })), { to: RECOVERY, sealed: s.recoveryGrant }],
      roster: sealedRoster,
      content_keys: s.rewrapped,
      ...(abandon.length ? { abandon } : {}),
      upgrade: true,
      // each kept passkey's new key; its own touch's confirmation, but the signer's, whose touch is its signature
      credentials: kept.map((x) => {
        const { confirmation, ...made } = x.made!;
        return x === signer || !confirmation ? made : { ...made, confirmation };
      }),
      recovery: { public_key: C.b64u(code.RK), anchor: s.anchor },
      kept: this.keep,
      ...proofs,
      ...KEM,
    });
    const via: Via = { kind: "credential", credentialId: signer.credential.credential_id, passkeyId: signer.passkey.passkey_id, ck: signer.key!,
      CK: signer.pub, anchor: s.next.id };
    for (const x of kept) if (x !== signer) forgetPriv(x.key);
    signer.key = undefined;                    // the tab's now (via), so disposing of the flow leaves it held
    this.forgetCode();
    if (this.newCode) C.zero(this.newCode);
    return { answer, via };
  }

  private forgetCode() {
    if (!this.code) return;
    C.zero(this.code.bytes);
    forgetPriv(this.code.hybrid);
    if (this.code.classical !== this.code.hybrid) forgetPriv(this.code.classical);
    this.code = null;
  }

  /** Drop what was made (the code too unless `code` is false). */
  dispose(code = true) {
    for (const x of this.seats) {
      x.held?.drop();
      forgetPriv(x.key);
    }
    this.seats = [];
    this.s = null;
    if (code) {
      this.forgetCode();
      if (this.newCode) C.zero(this.newCode);
    }
  }

  private need() {
    if (!this.s) throw new C.E2EError("Prepare the upgrade first.");
    return this.s;
  }

  /** What was prepared, while there is time left to touch and sign it (left): UPGRADE_STALE, before any touch, once
   *  there is none. */
  private inTime(): UpgradeMade {
    const s = this.need();
    if (!this.left()) throw new C.E2EError(UPGRADE_STALE);
    return s;
  }
}

// ---- turning a classical account on again with post-quantum keys: the fold ----------

export const FOLD_ON = "Encryption is on now: upgrade its keys under Post-quantum keys instead.";
export const FOLD_NOT_DUE = "No new master key is due: turn it on again with these keys, then upgrade them.";
export const FOLD_WAITING = "A change to these keys waits: cancel it, or let it take effect, before turning it on again.";
export const FOLD_TOUCH_EACH = "Touch each passkey but the one you sign with first: turning it on again with post-quantum keys gives every passkey that unlocks it a new key.";
export const FOLD_SPENT = "A passkey's new key could not be made: prepare it again, then touch each passkey again.";

/** Turning a classical account's encryption on again while a new master key is due (rekeyDue), with that key made as
 *  the upgrade makes one (Upgrade), in the same change: one turn-on with `upgrade: true`, after which the account is
 *  post-quantum (the server's fold). What the upgrade makes, it makes: the recovery code
 *  kept (typed, and proved twice, for turning on) or a new one; a new master key, its chain link, a new X-Wing content
 *  key and its version-3 statement; every content key this tab holds wrapped again; a fresh X-Wing key and salt for
 *  each passkey the roster names that stays, touched once, its touch its own confirmation, and the signer's touch its
 *  signature. Unlike the upgrade it removes no passkey: every one that stays is given its new key, so it is signed only
 *  once each of them is touched but the signer (FOLD_TOUCH_EACH), and a passkey not at hand is removed from the account
 *  first, which takes effect at once while encryption is off. It is held as the upgrade is (a day; the code's day,
 *  which only the code cancels, when the code's proof comes with it), and made only while encryption is off, a new
 *  master key is due and no change waits. `seal`, given as it is signed: what linked sessions sent in the clear while
 *  it was off is sealed in place as it takes effect. */
export class Fold extends Upgrade {
  private sealing = true;

  protected readiness(view: E2EView): void {
    if (view.pq || this.ring.pq) throw new C.E2EError(UPGRADE_NOT_CLASSICAL);
    if (view.on) throw new C.E2EError(FOLD_ON);
    if (!view.mlkem) throw new C.E2EError(NO_MLKEM);
    if (view.pending) throw new C.E2EError(FOLD_WAITING);
    if (!rekeyDue(this.ring, view)) throw new C.E2EError(FOLD_NOT_DUE);
  }

  /** None: every passkey that unlocks encryption and stays is given its new key. */
  removes(): string[] {
    return [];
  }

  /** The seats whose passkey may sign it: the one left untouched, whose signature gives it its new key; or, once every
   *  one is touched, any (and the one whose signature kept it before, alone: its touch is no confirmation of its own).
   *  None while two or more are left untouched, or one could not be given its new key. */
  protected signing(): UpgradeSeat[] {
    const left = this.seats.filter((x) => !x.made);
    if (left.length > 1 || left.some((x) => !x.held)) return [];
    const signedBefore = this.seats.filter((x) => x.made && !x.made.confirmation);
    if (signedBefore.length) return left.length ? [] : signedBefore;
    return left.length ? left : this.seats;
  }

  protected beforeSign(): void {
    if (this.seats.some((x) => !x.made && !x.held)) throw new C.E2EError(FOLD_SPENT);
    if (!this.signing().length) throw new C.E2EError(FOLD_TOUCH_EACH);
  }

  protected proofPurpose(): RecoveryPurpose {
    return "turn-on";
  }

  /** The request, sent: turning encryption on again, with whether it seals in place. */
  protected post(body: Record<string, unknown>): Promise<Answer> {
    return E2EApi.turnOn({ ...body, seal: this.sealing });
  }

  /** The signature, and everything checked and sent in one request (Upgrade.signAndSend): call it first thing in the
   *  click's handler. */
  signAndSend(seal = true): Promise<{ answer: Answer; via: Via }> {
    this.sealing = seal;
    return super.signAndSend();
  }
}

// ---- the change that waits, checked with this tab's keys ------------------------------------------------------------

/** What this tab's keys make of the change that waits, read from its body as the server keeps it (pending.body) before
 *  its hold ends: `opens`, what it puts in place opens with what unlocked this tab, as it says; `leaves`, it leaves out
 *  what unlocked this tab, which unlocks encryption no more after it; `broken`, what it puts in place does not open with
 *  these keys, or not as it says. With the reason in words. */
export interface WaitingCheck { state: "opens" | "leaves" | "broken"; why: string }

export const WAITING_OPENS = "Checked with this tab's keys: what it puts in place opens with them.";
export const WAITING_LEAVES = "Checked with this tab's keys: it leaves out what unlocked this tab, which unlocks encryption no more after it.";
export const WAITING_NEW_CODE = "Checked with this tab's keys: it makes a new recovery code, so the one this tab was unlocked with opens nothing after it.";
export const WAITING_STARTS_AGAIN = "It starts encryption again: what was sealed under the keys this tab unlocked is never read after it.";
export const WAITING_BROKEN = "Checked with this tab's keys: what it puts in place does not open with them, or not as it says. Cancel it unless you know why.";

/** Whether the turning-on that waits is another than the one that made the master key `anchor`, put in its place: any
 *  passkey that may cancel a turning-on may take its place. A turning-on's body names the master key
 *  it makes; a body the server does not serve says nothing either way, and counts as the tab's own. */
export function turnOnInPlace(pending: E2EPending | null, anchor: string): boolean {
  return pending?.change === "turn-on" && pending.body !== null && pending.body.mk_id !== anchor;
}

/** What a tab that waited on the keys it made says when other keys took effect instead (the store's load). */
export const KEYS_MADE_ELSEWHERE = "Keys other than those this tab made took effect: the recovery code this tab showed does not unlock them. Which passkeys do is shown under What unlocks it, below.";

/** The change that waits checked with `ring`, the keys this tab unlocked: a rotation's content key, a revocation's new
 *  master key (and a turn-on's that makes one), a passkey being added (its grant and roster), a turn-on that starts
 *  again with replace; null for one with nothing these keys open (renewed anchors, turning encryption off, statements
 *  a session sent in, the upgrade and the fold, whose new master key is sealed to new keys alone), with no body, or
 *  when nothing waits, and for a new master key or a passkey being added from a
 *  ring without a roster, which cannot say whom the one is to be sealed to, nor what the other's roster is to name.
 *  What does not open is `broken`. */
export async function checkWaiting(ring: Ring, view: E2EView): Promise<WaitingCheck | null> {
  const pending = view.pending;
  const b = pending?.body;
  if (!pending || !b) return null;
  if (!ring.roster && (pending.change === "revoke" || pending.change === "credentials" || (pending.change === "turn-on" && b.shape === "rekey"))) return null;
  const opens = (ok: boolean): WaitingCheck => (ok ? { state: "opens", why: WAITING_OPENS } : { state: "broken", why: WAITING_BROKEN });
  try {
    if (pending.change === "rotate") return opens(await contentKeyOpens(ring.master, b.content_key, ring.account, b.statement, ring.pq));
    // the upgrade seals the new master key to new keys this tab does not hold: nothing of it opens here,
    // nor of the fold, which makes its new master key as the upgrade does
    if ((pending.change === "revoke" || pending.change === "turn-on") && b.upgrade === true) return null;
    if (pending.change === "revoke") return await newMasterOpens(ring, view, b);
    if (pending.change === "credentials") return opens(await addingOpens(ring, view, b));
    if (pending.change === "turn-on" && b.shape === "again") return opens(await contentKeyOpens(ring.master, b.content_key, ring.account, b.statement, ring.pq));
    if (pending.change === "turn-on" && b.shape === "rekey") return await newMasterOpens(ring, view, b);
    if (pending.change === "turn-on" && b.shape === "whole" && b.replace === true) return { state: "leaves", why: WAITING_STARTS_AGAIN };
    return null;
  } catch {
    return { state: "broken", why: WAITING_BROKEN };
  }
}

/** Whether checkWaiting has something to check of `pending` once the keys are unlocked: what a locked tab may say
 *  unlocking would check. */
export function checkable(pending: E2EPending | null): boolean {
  const b = pending?.body;
  if (!pending || !b) return false;
  if ((pending.change === "revoke" || pending.change === "turn-on") && b.upgrade === true) return false;
  if (pending.change === "turn-on") return b.shape === "again" || b.shape === "rekey" || (b.shape === "whole" && b.replace === true);
  return pending.change === "rotate" || pending.change === "revoke" || pending.change === "credentials";
}

/** Whether a content key as a body keeps it ({key_id, public_key, mk_id, wrapped}) is wrapped under `master` and opens
 *  to the key it names, its key_id that key's, and, where its statement is given (as kept: its canonical text), the
 *  one that statement states. */
async function contentKeyOpens(master: C.Master, raw: unknown, account: string, statement: unknown, pq: boolean): Promise<boolean> {
  const k = objectOr<Record<string, unknown>>(raw);
  if (!k || typeof k.key_id !== "string" || typeof k.public_key !== "string" || typeof k.wrapped !== "string" || k.mk_id !== master.id) return false;
  // in the form its account keeps it (openContentKey): a post-quantum account's new key is post-quantum
  const pub = C.unb64u(k.public_key);
  if (!pub || (pub.length === C.HYBRID_KEY_LEN) !== pq) return false;
  const opened = await openContentKey(master, { key_id: k.key_id, public_key: k.public_key, mk_id: k.mk_id, wrapped: k.wrapped }, account, pq);
  forgetPriv(opened?.ek);
  if (!opened) return false;
  if (statement === undefined) return true;
  const s = objectOr<Record<string, unknown>>(statement);
  const payload = typeof s?.payload === "string" ? objectOr<Record<string, unknown>>(JSON.parse(s.payload)) : null;
  return !!payload && payload.key === k.public_key && payload.key_id === k.key_id;
}

/** The credentials the server counts as staying after a change that waits, as it lists them: each whose passkey is the
 *  account's and not one the change removes (`removes`, as the server lists them), a hardware key's while high security
 *  is or will be on (`high`). */
function stayingAfter(view: E2EView, removes: string[], high: boolean): E2ECredential[] {
  const tier = new Map(view.passkeys.map((p) => [p.passkey_id, p.tier]));
  return view.credentials.filter((c) => tier.has(c.passkey_id) && !removes.includes(c.passkey_id) && (!high || tier.get(c.passkey_id) === "hardware"));
}

/** A new master key a waiting change would put in place (a revocation's, or a turn-on's that makes one), as its body
 *  keeps it, with the keys this tab unlocked: it leaves what unlocked this tab where it grants nothing to it, or makes a
 *  new recovery code where this tab was unlocked with the code; else the grant to it opens to the key the body names,
 *  whose chain link opens to this tab's master key, its content key and each content key this tab holds wrapped again
 *  (but those it leaves as they are) open to the keys they were, and its roster names the recovery key it is to have,
 *  this tab's credential with this tab's own key and passkey, and every credential the server counts as staying
 *  (stayingAfter) with the key the server lists for it, and nothing else. Its grants go to those credentials and the
 *  recovery key alone, each exactly the grant of the new master key to the key the roster names for its recipient
 *  (isGrantOf: sealed again from the bytes this tab's grant opened to, and compared byte for byte), so each opens for
 *  its recipient although this tab holds none of their keys. */
async function newMasterOpens(ring: Ring, view: E2EView, b: Record<string, unknown>): Promise<WaitingCheck> {
  const broken: WaitingCheck = { state: "broken", why: WAITING_BROKEN };
  const { account } = ring;
  const newCode = objectOr<{ public_key?: unknown }>(b.recovery);
  if (ring.via.kind === "recovery" && newCode) return { state: "leaves", why: WAITING_NEW_CODE };
  const to = viaTo(ring.via);
  const sealed = objectOr<Record<string, unknown>>(b.grants)?.[to];
  if (typeof sealed !== "string") return { state: "leaves", why: WAITING_LEAVES };
  if (typeof b.mk_id !== "string" || b.previous !== ring.master.id || typeof b.chain !== "string" || typeof b.roster !== "string") return broken;
  const { priv, pub } = viaKey(ring.via);
  const bytes = await openGrantBy(priv, pub, sealed, account, to, b.mk_id);
  try {
    const next = await C.masterOf(bytes, account);
    if (next.id !== b.mk_id) return broken;
    const older = await C.openChain(next, b.chain, ring.master.id, account);
    try {
      if ((await C.masterOf(older, account)).id !== ring.master.id) return broken;
    } finally {
      C.zero(older);
    }
    if (!(await contentKeyOpens(next, b.content_key, account, b.statement, ring.pq))) return broken;
    const wraps = objectOr<Record<string, unknown>>(b.content_keys) ?? {};
    const abandon = list<unknown>(b.abandon);
    for (const [keyId, held] of ring.keys) {
      if (abandon.includes(keyId)) continue;
      const wrapped = wraps[keyId];
      if (typeof wrapped !== "string") return broken;
      const again = await openContentKey(next, { key_id: keyId, public_key: C.b64u(held.EK), mk_id: next.id, wrapped }, account, ring.pq);
      forgetPriv(again?.ek);
      if (!again) return broken;
    }
    const roster = await C.openRoster(next, b.roster, account);
    const recovery = newCode ? newCode.public_key : rosterOf(ring).recovery;
    if (roster.recovery !== recovery) return broken;
    const via = ring.via;
    if (via.kind === "credential" && !roster.credentials.some((c) =>
      c.credential_id === to && c.passkey_id === via.passkeyId && c.public_key === C.b64u(via.CK))) return broken;
    const removes = view.pending?.removes ?? [];
    const stay = stayingAfter(view, removes, view.high_security || b.high_security === true);
    const named = (c: E2ECredential) => roster.credentials.some((r) =>
      r.credential_id === c.credential_id && r.passkey_id === c.passkey_id && r.public_key === c.public_key);
    if (roster.credentials.length !== stay.length || !stay.every(named)) return broken;
    const grants = objectOr<Record<string, unknown>>(b.grants) ?? {};
    const sealedTo = new Map<string, unknown>([...roster.credentials.map((c) => [c.credential_id, c.public_key] as const), [RECOVERY, recovery]]);
    if (Object.keys(grants).length !== sealedTo.size) return broken;
    for (const [recipient, sealed] of Object.entries(grants)) {
      const key = sealedTo.get(recipient);
      const raw = typeof key === "string" ? C.unb64u(key) : null;
      if (!raw || !(await C.isGrantOf(sealed, raw, bytes, account, recipient, b.mk_id))) return broken;
    }
    return { state: "opens", why: WAITING_OPENS };
  } finally {
    C.zero(bytes);
  }
}

/** A passkey being added, as its body keeps it, with the keys this tab unlocked: sealed to this tab's master key, its
 *  key of the account's kind (an X-Wing key for a post-quantum account, whose credential's anchor, stored beside its
 *  wrap, is that master key), its grant exactly the grant of that master key to the key its credential names
 *  (isGrantOf, from the bytes this tab's own grant opens to), and its roster naming the recovery key, every credential
 *  this tab's roster names, and it. */
async function addingOpens(ring: Ring, view: E2EView, b: Record<string, unknown>): Promise<boolean> {
  const cred = objectOr<Record<string, unknown>>(b.credential);
  const pub = typeof cred?.public_key === "string" ? C.unb64u(cred.public_key) : null;
  const id = cred?.credential_id;
  const grant = b.grant;
  if (!cred || !pub || typeof id !== "string" || b.mk_id !== ring.master.id || typeof grant !== "string" || typeof b.roster !== "string") return false;
  if ((pub.length === C.HYBRID_KEY_LEN) !== ring.pq || (ring.pq && cred.anchor !== ring.master.id)) return false;
  if (!(await withMasterBytes(ring, view, (bytes) => C.isGrantOf(grant, pub, bytes, ring.account, id, ring.master.id)))) return false;
  const roster = await C.openRoster(ring.master, b.roster, ring.account);
  const named = (id: unknown, key: unknown) => roster.credentials.some((c) => c.credential_id === id && c.public_key === key);
  const before = rosterOf(ring);
  return roster.recovery === before.recovery && named(cred.credential_id, cred.public_key)
    && before.credentials.every((c) => named(c.credential_id, c.public_key));
}

// ---- the recovery code as a confirmation ---------------------------------------------------------------------------

/** The recovery code's proof for one change of `purpose`, for an owner who lost every passkey: the server's fresh
 *  challenge, answered with the HMAC the code's key makes (proofFrom). With the keys unlocked by the code, its key is
 *  used and nothing is typed again; otherwise the code as typed, its key in the account's form (`pq`, a post-quantum
 *  account's). */
export async function recoveryProof(account: string, purpose: RecoveryPurpose,
                                    from: { ring?: Ring | null; code?: string; pq?: boolean }): Promise<RecoveryProof> {
  if (from.ring?.via.kind === "recovery") return proofFrom(from.ring.via.rk, purpose);
  const code = from.code ? C.recoveryBytes(from.code) : null;
  if (!code) throw new C.E2EError(CODE_FORM);
  let rk: Priv;
  try {
    rk = (await recoveryOf(code, account, !!from.pq)).rk;
  } finally {
    C.zero(code);
  }
  try {
    return await proofFrom(rk, purpose);
  } finally {
    forgetPriv(rk);
  }
}

/** The recovery code's key from the code as typed, checked to be the account's recovery code in force (as unlocking
 *  with it checks: WRONG_CODE otherwise), for a change sent with the code's proof made after its touch
 *  (Revoke.signAndSendWithCode). A key that cannot be extracted (a post-quantum one's ML-KEM half put away at lock, as
 *  every key the tab holds); the code's bytes are zeroed. */
export async function codeKey(view: E2EView, account: string, text: string): Promise<Priv> {
  return (await viaFromCode(view, account, text)).rk;
}

/** The server's challenge for the recovery code's proof, as it gives one: for a classical recovery key, a classical
 *  one (server_key, an X25519 key); for a post-quantum one, an X-Wing encapsulation to it (server_ct, 1,120 bytes). */
export interface ProofChallenge { challenge_id: string; nonce: string; server_key?: string; server_ct?: string }

/** The recovery code's proof for `purpose` from its key: the server's fresh challenge, answered in the key's form. A
 *  post-quantum key answers only a challenge encapsulated to it, and a classical key only a classical one: a challenge
 *  in the other form is refused, so a hybrid key is never proved with X25519 alone. `forKey`, an upgrade's new recovery
 *  key: the challenge is made for that key, which the server checks the proof against. */
async function proofFrom(rk: Priv, purpose: RecoveryPurpose, forKey?: Uint8Array, digest?: string): Promise<RecoveryProof> {
  const o = await E2EApi.recoveryChallenge(purpose, forKey, digest);
  const nonce = C.unb64u(o.nonce);
  if (!nonce) throw new C.E2EError(CHALLENGE_FORM);
  if (rk.pq) {
    const ct = C.unb64u(o.server_ct);
    if (!ct || ct.length !== C.HYBRID_CT_LEN || o.server_key !== undefined) throw new C.E2EError(CHALLENGE_FORM);
    return { challenge_id: o.challenge_id, proof: await C.recoveryProofHybrid(rk.key, ct, nonce, purpose) };
  }
  const key = C.unb64u(o.server_key);
  if (!key || o.server_ct !== undefined) throw new C.E2EError(CHALLENGE_FORM);
  return { challenge_id: o.challenge_id, proof: await C.recoveryProof(rk.key, key, nonce, purpose) };
}

export const CHALLENGE_FORM = "The server's challenge is not in its form.";

// ---- re-sealing what is stored classical --------------------------------------------

/** Where a stored text is, as the server names it: its table ("events" and "rows" with the field that holds the text,
 *  "talks" for a conversation whole, with none) and its row. The page sends it back as it was served. */
export interface ResealWhere { table: string; id: string | number; field?: string }
/** A conversation as the server serves it to re-seal: its messages as stored, and what its slots are made of. */
export interface ResealTalk { link_id: string; session_id: string; earlier: number; messages: { who: string; text: string; at: number | null; ms?: number | null }[] }
/** One stored form-1 text to re-seal, as GET /api/ade/e2e/reseal serves it: an envelope with the slot it is bound to,
 *  or a conversation whole (`where.table` "talks"), whose slots the page makes itself. */
export interface ResealItem { where: ResealWhere; value: string | ResealTalk; slot?: string }
/** What is sent for one item: where, the sha256 of what is stored now (the server swaps only that), and what replaces
 *  it. */
export interface ResealSwap { where: ResealWhere; old_sha256: string; new: string | Record<string, unknown> }

export const RESEAL_NOT_PQ = "Only a post-quantum account's texts are sealed again: upgrade the keys first.";
export const RESEAL_OFF = "Encryption is off: nothing is sealed again until it is on.";
export const RESEAL_NO_KEY = "The newest key the account stated is not a post-quantum key this tab holds: unlock again, then re-seal.";
export const RESEAL_SKIPPED = "It does not open with these keys, so it was left as it is.";
// A conversation sealed again is held to no bound here: the server takes up to what its messages come to sealed and its
// key's (e2e_keys._reseal_swap: that, or 300,000 bytes where more). A message that was in form 1 holds exactly the
// bytes it held (a plaintext opens only in its RFC 8785 form) in form 3, whose envelope is 32 bytes shorter than form 1's;
// one that was in the clear holds its words as RFC 8785 bytes beside form 3's 45, which is what the server counts for it
// (_talk_size).

/** A batch's items at most (e2e_keys.RESEAL_MAX). */
const RESEAL_BATCH_ITEMS = 200;
/** The slot makers of a conversation (e2e.ts): a message's, and its key's. */
export interface ResealSlots {
  talk: (account: string, linkId: string, sessionId: string, index: number, count: number, earlier: number, who: string,
         at: number | null | undefined, ms: number | null | undefined) => string;
  talkKey: (account: string, linkId: string, sessionId: string, count: number, earlier: number) => string;
}
/** A batch's bound on what it sends, under the server's 1 MiB for a request. */
const RESEAL_BATCH_BYTES = 512 * 1024;

/** The sha256 of a stored value, as hex: a text's UTF-8, or the RFC 8785 JSON of a conversation as served. */
export async function storedDigest(value: unknown): Promise<string> {
  return C.hex(await C.sha256(typeof value === "string" ? C.utf8(value) : C.canonicalBytes(value)));
}

/** What a re-seal batch's recovery proof is bound to: the hex sha256 of the RFC 8785 JSON of the list of
 *  [where, old_sha256, sha256 of new] of each swap, in the order sent. */
export async function resealBinding(swaps: ResealSwap[]): Promise<string> {
  const rows = await Promise.all(swaps.map(async (s) => [s.where, s.old_sha256, await storedDigest(s.new)]));
  return C.hex(await C.sha256(C.canonicalBytes(rows)));
}

/** A re-seal batch's recovery proof: the code's key `rk` answering a challenge made for the batch's digest
 *  (resealBinding), which the proof is good for alone. */
export async function resealProof(rk: Priv, batch: ResealSwap[]): Promise<RecoveryProof> {
  return proofFrom(rk, "reseal", undefined, await resealBinding(batch));
}

export const RESEAL_STOPPED = "Re-sealing stopped: what was sealed again stays so, and the rest is left as it is.";
/** How many times one page of re-sealing is read, the first read included, where the server swaps none of a batch
 *  because something it names was sealed again, changed or deleted since the page was read, or is a question that may
 *  yet be answered (404 RESEAL_GONE, 409 RESEAL_CHANGED: "read them again"); after that, the server's words. */
export const RESEAL_READS = 3;
/** The server's words for those two refusals (e2e_keys._reseal_swap), the only ones a page is read again for: its other
 *  404s and 409s (a classical account, encryption off, no recovery code, from the batch or from its proof's challenge)
 *  end the run at once, and so does a proxy's answer, which carries no words of the app (api takes a refusal's words
 *  from the app's own JSON alone). */
export const RESEAL_GONE = "That stored text is not one to seal again: it was sealed again, sent again or deleted since it was read, or it is a question that may yet be answered, whose answer is signed over its text as stored. Read them again.";
export const RESEAL_CHANGED = "What is stored there changed since it was read: read the stored texts again.";

/** Whether `e` is the server's refusal of a re-seal batch because something in it changed since its page was read: its
 *  words, with their status. */
function changedSinceRead(e: unknown): boolean {
  return e instanceof ApiError && ((e.status === 404 && e.message === RESEAL_GONE) || (e.status === 409 && e.message === RESEAL_CHANGED));
}

/** Re-sealing what the server still stores sealed to a classical key, from a
 *  post-quantum account's keys unlocked in this tab: page by page, each text opened with the key it was sealed to
 *  (kept, wrapped again, since the upgrade) and sealed again with the same slot to the newest key the account stated,
 *  in form 2; a conversation whole, under a fresh conversation key (form 3 beside its form-2 key), its messages in the
 *  clear too where a client sent it half sealed (message). Sent in batches under
 *  the request's bound, each with the recovery code's proof bound to the batch (resealBinding), so a passkey or a
 *  sign-in alone cannot use it: re-sealing rewrites what is stored. The server swaps a value only where what it stores
 *  is still the one read (its sha256), and logs each swap by its hashes. A text that does not open is left as it is
 *  and counted. Never sends anything in the clear: what it sends is sealed. */
export class Reseal {
  /** texts sealed again (a conversation counts as one), texts left as they are because they do not open here, and
   *  batches sent */
  sealed = 0;
  skipped = 0;
  batches = 0;
  private stopped = false;

  /** `slots`, e2e.ts's talkSlot and talkKeySlot, which make a conversation's slots (given, so this module does not
   *  import the page's interface, which imports it) */
  constructor(readonly ring: Ring, readonly view: E2EView, readonly slots: ResealSlots) {}

  /** Stops it before its next text, page or batch: what was sent stays sealed again, nothing more is. */
  stop() {
    this.stopped = true;
  }

  /** The newest key the account stated, held by the ring and post-quantum: what everything is sealed again to. Refused
   *  for a classical account and while encryption is off (the server's RESEAL_NOT_PQ and RESEAL_OFF). */
  private target(): { EK: C.Bytes } {
    if (!this.view.pq || !this.ring.pq) throw new C.E2EError(RESEAL_NOT_PQ);
    if (!this.view.on) throw new C.E2EError(RESEAL_OFF);
    needsMlkem(this.ring, this.view);                 // each batch goes with the code's hybrid proof
    checkCurrent(this.ring, this.view, true);
    const newest = newestStated(this.view);
    const held = typeof newest?.key_id === "string" ? this.ring.keys.get(newest.key_id) : undefined;
    if (!held || !held.ek.pq) throw new C.E2EError(RESEAL_NO_KEY);
    return { EK: held.EK };
  }

  /** Everything re-sealed, page by page: `rk`, the recovery code's key (codeKey, or that of keys unlocked with the
   *  code), proves each batch; `progress` is told after each, and as each page ends. Before each text, page and batch, it
   *  stops (RESEAL_STOPPED) once stop() was called or `alive` says the keys it holds are no longer the tab's (the tab
   *  locked): it never goes on with keys put away. A page whose batch the server swaps none of because something in it
   *  changed since it was read (changedSinceRead: 404 RESEAL_GONE or 409 RESEAL_CHANGED, in the server's words; any other
   *  refusal ends it) is read again, RESEAL_READS times in all at most; what it leaves is counted once the page ends. It
   *  ends where the server gives no next page, or one it gave before. */
  async run(rk: Priv, progress: (r: Reseal) => void = () => {}, alive: () => boolean = () => true): Promise<void> {
    const { EK } = this.target();
    const go = () => {
      if (this.stopped || !alive()) throw new C.E2EError(RESEAL_STOPPED);
    };
    const seen = new Set<string>();
    let after: string | null = null;
    for (;;) {
      let page: { items: ResealItem[]; next: string | null };
      let left = 0;
      for (let read = 1; ; read++) {
        go();
        page = await E2EApi.resealPage(after);
        left = 0;
        const swaps: ResealSwap[] = [];
        for (const item of Array.isArray(page.items) ? page.items : []) {
          go();
          const swap = await this.sealAgain(item, EK);
          if (swap) swaps.push(swap);
          else left++;
        }
        try {
          for (const batch of batchesOf(swaps)) {
            go();
            aboveFloor(this.ring);
            const proof = await resealProof(rk, batch);
            await E2EApi.reseal({ items: batch, recovery_proof: proof, ...KEM });
            this.sealed += batch.length;
            this.batches++;
            progress(this);
          }
          break;
        } catch (e) {
          // none of that batch was swapped, since something in it changed since the page was read, and what was before
          // it stays swapped: read the page again, whose texts that were swapped are no longer served. Any other
          // refusal ends it
          if (!changedSinceRead(e) || read >= RESEAL_READS) throw e;
        }
      }
      this.skipped += left;
      progress(this);
      if (typeof page.next !== "string" || !page.next || seen.has(page.next)) return;
      seen.add(page.next);
      after = page.next;
    }
  }

  /** One item sealed again; null when it does not open (it is left as it is). */
  private async sealAgain(item: ResealItem, EK: C.Bytes): Promise<ResealSwap | null> {
    const old_sha256 = await storedDigest(item.value);
    if (typeof item.value === "string") {
      if (typeof item.slot !== "string") return null;
      const bytes = await this.plain(item.value, item.slot);
      if (!bytes) return null;
      return { where: item.where, old_sha256, new: await C.sealEnvelope2(EK, bytes, item.slot) };
    }
    const talk = item.value;
    if (!talk || !Array.isArray(talk.messages) || typeof talk.link_id !== "string" || typeof talk.session_id !== "string") return null;
    const { account } = this.ring;
    const count = talk.messages.length;
    const plains: C.Bytes[] = [];
    for (let i = 0; i < count; i++) {
      const m = talk.messages[i];
      const bytes = await this.message(m.text, this.slots.talk(account, talk.link_id, talk.session_id, i, count, talk.earlier, m.who, m.at, m.ms));
      if (!bytes) return null;                                // a conversation is never a mix: whole, or left as it is
      plains.push(bytes);
    }
    const tkBytes = C.random(C.TALK_KEY_LEN);
    try {
      const tk = await C.talkKeyOf(tkBytes);
      const key = await C.sealTalkKey(EK, tkBytes, this.slots.talkKey(account, talk.link_id, talk.session_id, count, talk.earlier));
      const messages = await Promise.all(talk.messages.map(async (m, i) => ({ ...m,
        text: await C.sealTalkMessage(tk, plains[i], this.slots.talk(account, talk.link_id, talk.session_id, i, count, talk.earlier, m.who, m.at, m.ms)) })));
      return { where: item.where, old_sha256, new: { messages, key, key_id: tk.id } };
    } finally {
      C.zero(tkBytes);
    }
  }

  /** A conversation's message as it is sealed again: one in form 1 opened (plain); one in the clear, which a
   *  conversation holds where a client other than a link sent it half sealed while encryption was off and a turn-on did
   *  not seal it in place, as its words, {"text"} in RFC 8785 form, sealed for the first time (the server holds it to
   *  what they come to, e2e_keys._message_held). Null for anything else, a text that claims to be an envelope and is not
   *  form 1 among them. */
  private async message(text: unknown, slot: string): Promise<C.Bytes | null> {
    if (typeof text === "string" && !text.startsWith(C.PREFIX)) return C.canonicalBytes({ text });
    return this.plain(text, slot);
  }

  /** A form-1 text's plaintext, opened with the key it names and `slot`, as the bytes it was sealed from (its RFC 8785
   *  JSON, which a plaintext is); null when it is not form 1 or does not open. */
  private async plain(envelope: unknown, slot: string): Promise<C.Bytes | null> {
    if (typeof envelope !== "string" || C.envelopeForm(envelope)?.form !== 1) return null;
    const opened = await openWith(this.ring, envelope, slot);
    return opened.ok ? C.canonicalBytes(opened.value) : null;
  }
}

/** Swaps cut into batches whose JSON stays under RESEAL_BATCH_BYTES, of RESEAL_BATCH_ITEMS at most, each of one swap at
 *  least. */
function batchesOf(swaps: ResealSwap[]): ResealSwap[][] {
  const out: ResealSwap[][] = [];
  let batch: ResealSwap[] = [];
  let size = 0;
  for (const s of swaps) {
    const n = JSON.stringify(s).length;
    if (batch.length && (size + n > RESEAL_BATCH_BYTES || batch.length >= RESEAL_BATCH_ITEMS)) {
      out.push(batch);
      batch = [];
      size = 0;
    }
    batch.push(s);
    size += n;
  }
  if (batch.length) out.push(batch);
  return out;
}

// ---- cancelling the change that waits --------------------------------------------------------------------------------

export const NONE_CANCELS = "No passkey of yours cancels this change: use the recovery code.";
export const NO_CODE_CANCELS = "This account has no recovery code to cancel it with: use one of your passkeys.";
/** The recovery code asked to cancel a change on a post-quantum account where the server cannot use ML-KEM. */
export const CODE_NEEDS_MLKEM = "The recovery code cannot cancel it while the server cannot use ML-KEM: on a post-quantum account the code proves itself by the hybrid proof alone, which needs ML-KEM.";

/** Cancelling the change that waits, which the sign-in alone does not do (the server holds whoever has a stolen device
 *  to that), with exactly what the server lists as cancelling it: one touch of a passkey the view's cancel_by names,
 *  which confirms it, or, where cancel_by_code says so, the recovery code's proof for purpose cancel. The
 *  confirmation's challenge is fetched first (prepare), so a click reaches the browser's prompt with nothing awaited. */
export class CancelChange {
  private ceremony: Ceremony | null = null;

  constructor(readonly account: string, readonly view: E2EView, readonly pending: E2EPending) {}

  /** The passkeys whose touch cancels it, as the account lists them. */
  passkeys(): E2EPasskey[] {
    const may = new Set(this.pending.cancel_by);
    return this.view.passkeys.filter((p) => may.has(p.passkey_id));
  }

  /** Whether the recovery code cancels it, as the server says. */
  byCode(): boolean {
    return this.pending.cancel_by_code;
  }

  /** Whether the recovery code can cancel it now: the server lists it (byCode), and on a post-quantum account the server
   *  can use ML-KEM, which the code's proof needs there (e2e_keys.proof_challenge and use_proof answer 503 without it,
   *  though cancellers still lists the code). */
  codeCancels(): boolean {
    return this.byCode() && !(this.view.pq && !this.view.mlkem);
  }

  async prepare(): Promise<void> {
    this.ceremony = await confirmCeremony("e2e-cancel");
  }

  /** One touch of a passkey that cancels it, the only ones the browser is offered, over the challenge derived from the
   *  server's, user verification required whatever the server said: call it first thing in the click's handler. */
  async withPasskey(): Promise<{ cancelled_at: number }> {
    const c = this.ceremony;
    if (!c) throw new C.E2EError("Cancelling is being prepared: try again in a moment.");
    const allow = this.passkeys().map((p) => ({ type: "public-key" as const, id: p.credential_id }));
    if (!allow.length) throw new C.E2EError(NONE_CANCELS);
    const { credential } = await assertionWith({ challenge: c.ask.challenge, rpId: c.ask.rpId, allowCredentials: allow });
    this.ceremony = null;
    this.prepare().catch(() => {});          // a fresh challenge, should this cancel be refused and tried again
    const { confirmation } = await E2EApi.confirm(c.challenge_id, credential);
    return E2EApi.cancel(this.pending.id, { confirmation });
  }

  /** The recovery code's proof: with the keys unlocked by the code, its key; else the code as typed. */
  async withCode(from: { ring?: Ring | null; code?: string }): Promise<{ cancelled_at: number }> {
    if (!this.byCode()) throw new C.E2EError(NO_CODE_CANCELS);
    if (!this.codeCancels()) throw new C.E2EError(CODE_NEEDS_MLKEM);       // before any proof is asked for
    return E2EApi.cancel(this.pending.id, { recovery_proof: await recoveryProof(this.account, "cancel", { ...from, pq: this.view.pq }) });
  }
}

// ---- opening a text ------------------------------------------------------------------------------------------------

export const LOCKED = "Encrypted: unlock to read it.";
export const UNREADABLE = "This text could not be read: it does not open where it is shown.";
export const NOT_HELD = "This text was sealed to a key this page does not hold.";
export const UNREACHED = "This text was sealed to a content key the server lists under a master key that does not lead back to yours: it is not opened.";
export const KEY_UNOPENED = "This text was sealed to a content key that does not open with these keys: it is not opened.";
export const NOT_ITS_FORM = "This is not an encrypted text the ADE takes.";
export const NOT_AN_OBJECT = "This text could not be read: what it holds is not in its form.";

/** A sealed text opened with the ring's content keys and bound to `slot`: its plaintext object, or why not. Opened by
 *  its form: form 1 under a classical content key, form 2 under a post-quantum one,
 *  never the one under the other's kind (a hybrid record is never tried with X25519 alone); form 3, a conversation's
 *  message, only through its conversation's key (openTalkWith), never here. */
export async function openWith(ring: Ring, envelope: string, slot: string): Promise<{ ok: true; value: Record<string, unknown> } | { ok: false; why: string }> {
  const read = C.envelopeForm(envelope);
  if (!read || read.form === 3) return { ok: false, why: NOT_ITS_FORM };
  const keyId = C.hex(read.body.subarray(1, 17));
  const held = ring.keys.get(keyId);
  if (!held) return { ok: false, why: ring.unreached.includes(keyId) ? UNREACHED : ring.unopened.includes(keyId) ? KEY_UNOPENED : NOT_HELD };
  if ((read.form === 2) !== held.ek.pq) return { ok: false, why: UNREADABLE };
  let plain: C.Bytes;
  try {
    plain = held.ek.pq ? await C.openEnvelope2(held.ek.key, envelope, slot) : await C.openEnvelope(held.ek.key, held.EK, envelope, slot);
  } catch {
    return { ok: false, why: UNREADABLE };
  }
  const value = C.plaintextObject(plain);
  return value ? { ok: true, value } : { ok: false, why: NOT_AN_OBJECT };
}

/** A conversation's key as the server keeps it beside its messages: its form-2
 *  envelope, and in the clear the key_id that names it. */
export interface TalkKeyRef { key: string; key_id: string }

/** A conversation as its messages are opened: its key and key_id as served (none for a conversation sealed message by
 *  message in form 1), and the slot its key is bound to (talkKeySlot). */
export interface TalkRef { key?: string | null; key_id?: string | null; keySlot: string }

/** A conversation's message opened, by what the conversation holds: beside a key, form 3 under that key alone
 *  (openTalkWith); with no key, form 1 alone, as an older release sealed it. A conversation is never a mix, and never
 *  of form-2 messages: anything else is not opened. */
export async function openTalkText(ring: Ring, talk: TalkRef, envelope: string, slot: string): Promise<{ ok: true; value: Record<string, unknown> } | { ok: false; why: string }> {
  if (typeof talk.key === "string" && typeof talk.key_id === "string") {
    return openTalkWith(ring, { key: talk.key, key_id: talk.key_id }, talk.keySlot, envelope, slot);
  }
  if (talk.key != null || talk.key_id != null) return { ok: false, why: UNREADABLE };
  return C.envelopeForm(envelope)?.form === 1 ? openWith(ring, envelope, slot) : { ok: false, why: UNREADABLE };
}

/** What `ring` opened of conversations' keys, by the key's envelope and slot, so a key is decapsulated once however
 *  many messages are opened under it. Kept with the ring alone: a ring followed or locked starts again. */
const TALK_KEYS = new WeakMap<Ring, Map<string, Promise<C.TalkKey>>>();

/** A conversation's message (form 3) opened under its conversation's key (`talk`), itself opened once from its form-2
 *  envelope bound to `keySlot` (talkKeySlot), checked to be exactly 1,197 bytes holding 32 whose key_id is the one the
 *  conversation names; the message bound to `slot`. A message that names another key_id, or of another form, is not
 *  opened. */
export async function openTalkWith(ring: Ring, talk: TalkKeyRef, keySlot: string, envelope: string, slot: string): Promise<{ ok: true; value: Record<string, unknown> } | { ok: false; why: string }> {
  const read = C.envelopeForm(envelope);
  if (!read || read.form !== 3) return { ok: false, why: NOT_ITS_FORM };
  if (C.hex(read.body.subarray(1, 17)) !== talk.key_id) return { ok: false, why: UNREADABLE };
  const sealedKey = C.envelopeForm(talk.key);
  if (!sealedKey || sealedKey.form !== 2) return { ok: false, why: UNREADABLE };
  const keyId = C.hex(sealedKey.body.subarray(1, 17));
  const held = ring.keys.get(keyId);
  if (!held) return { ok: false, why: ring.unreached.includes(keyId) ? UNREACHED : ring.unopened.includes(keyId) ? KEY_UNOPENED : NOT_HELD };
  if (!held.ek.pq) return { ok: false, why: UNREADABLE };
  const hybrid = held.ek.key;
  let opened = TALK_KEYS.get(ring);
  if (!opened) TALK_KEYS.set(ring, (opened = new Map()));
  const cacheKey = `${keySlot}\n${talk.key_id}\n${talk.key}`;
  let tk = opened.get(cacheKey);
  if (!tk) {
    tk = C.openTalkKey(hybrid, talk.key, keySlot, talk.key_id);
    opened.set(cacheKey, tk);
  }
  let plain: C.Bytes;
  try {
    plain = await C.openTalkMessage(await tk, envelope, slot);
  } catch {
    return { ok: false, why: UNREADABLE };
  }
  const value = C.plaintextObject(plain);
  return value ? { ok: true, value } : { ok: false, why: NOT_AN_OBJECT };
}
