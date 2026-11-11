// What the end-to-end encryption tests stand in for, and nothing the page imports: a browser's authenticator that
// gives WebAuthn's PRF (navigator.credentials.get), and the server's routes for the account's keys, keeping the rows as
// the server keeps them (ade/e2e_keys.py) and checking what it checks of each body: a statement as the next of the
// chain over the challenge it was signed with, naming a key no statement named before; the master key current; the
// grants to exactly the credentials that stay and the recovery key; every content key wrapped again, or left as it is
// (abandon); a passkey's confirmation or the recovery code's proof; and, to cancel the change that waits, the recovery
// code's proof (the code in force, always) or a confirmation from a passkey the server lists as one that may
// (cancellers, the second version: a change the code made, or whose proof came with it, only the code cancels;
// otherwise any passkey it does not remove, and those it removes too when it leaves no passkey but its signer; while
// high security is on only hardware keys count). The passkey that signs a rotation, a revocation or turning off, and
// the one that confirms adding a passkey or renewed anchors, unlocks the keys and still does after the change, unless
// the code's proof comes with it. Every change waits on its hold, turning encryption on among them (a day when it
// starts again), turning off and a revocation that removes no passkey a day too, and one whose time has come takes
// effect before anything else is asked (and at the next read of the keys), as the server settles it, checked again
// then, its signer or confirmer among it, and dropped, its reason kept, when it no longer holds. A change of any kind
// takes the place of the one that waits only when the passkey that signed or confirmed it, or the recovery code's proof
// with it, could cancel that one. Renewed anchors each take a fresh salt and, but the confirmer's own, a confirmation
// their own passkey made, and may renew the recovery code's anchor too; a routine rotation is at most one a day
// (rotateEvery). What a change is asked with (its statement's nonce, a proof or a confirmation) is used up only as it
// is placed, so one the server refuses leaves it unused, as the server's transaction does. While high security is on,
// only a hardware key's confirmation counts, and turning it on takes a hardware key's signature. A passkey removed, or
// left out by high security, while encryption is off takes its credential and grants with it, and a new master key is
// then due before it is turned on again (rekey_due, said only while it is off), which takes a revocation's body; a
// removed passkey ends the change it made that waits. Not stood in for: a link's statements sent in (the server's
// tests hold those).
import * as C from "./e2eCrypto";
import { canonicalJson } from "./passkey";
import type { E2EChainLink, E2EContentKey, E2ECredential, E2EGrant, E2ELink, E2ERecovery } from "./e2eKeys";

const subtle = () => globalThis.crypto.subtle;

async function hmac(key: Uint8Array, data: Uint8Array): Promise<C.Bytes> {
  const k = await subtle().importKey("raw", key as C.Bytes, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await subtle().sign("HMAC", k, data as C.Bytes));
}

function bytesOf(v: unknown): C.Bytes {
  if (v instanceof ArrayBuffer) return new Uint8Array(v.slice(0));
  const u = v as ArrayBufferView;
  return new Uint8Array(u.buffer.slice(u.byteOffset, u.byteOffset + u.byteLength) as ArrayBuffer);
}

/** The confirmations' purposes, and the challenge a passkey signs for one the server picked, as the server checks it
 *  (passkeys.derived_challenge), written here from its words: sha256("proofpane-confirm/1" 0 purpose 0 challenge). */
const CONFIRM_PURPOSES = ["confirm", "e2e-cancel", "e2e-enrol-touch", "e2e-enrol-own", "e2e-anchor-touch", "e2e-upgrade-touch"];
const derived = (purpose: string, challenge: Uint8Array) =>
  C.sha256(C.concat(C.utf8("proofpane-confirm/1"), new Uint8Array([0]), C.utf8(purpose), new Uint8Array([0]), challenge));
/** A step's challenge, as the server checks it (passkeys.step_challenge), written here from its words:
 *  sha256("proofpane-step/1" 0 canonical JSON). */
const stepped = (text: string) => C.sha256(C.concat(C.utf8("proofpane-step/1"), new Uint8Array([0]), C.utf8(text)));

/** WebAuthn's hash of a PRF input, as a browser takes it before an authenticator's hmac-secret does, written here from
 *  its words (WebAuthn level 3, the prf extension): sha256("WebAuthn PRF" 0 input). */
export const webauthnPrfHash = (input: Uint8Array) => C.sha256(C.concat(C.utf8("WebAuthn PRF"), new Uint8Array([0]), input));

export interface FakeCredential { id: string; secret: C.Bytes; unstable?: boolean; noPrf?: boolean }
/** A ceremony as the authenticator saw it: `salts`, each credential's first salt, and `seconds`, the second where one
 *  was asked. */
export interface Ceremony { challenge: C.Bytes; allow: string[]; salts: Record<string, string>; seconds: Record<string, string>; userVerification?: string; rpId?: string }

/** An authenticator: each credential's PRF is HMAC-SHA256 under a secret of its own (or random each time, `unstable`,
 *  as some security keys in Safari give it). Every ceremony and every PRF output it gave are kept. */
export class FakeAuthenticator {
  readonly creds = new Map<string, FakeCredential>();
  readonly ceremonies: Ceremony[] = [];
  readonly outputs: ArrayBuffer[] = [];
  /** which of the allowed credentials the person touches */
  choose: (allowed: string[]) => string = (allowed) => allowed[0];
  /** The path the passkey is reached by hashes each PRF input once more (webauthnPrfHash) before the credential's own
   *  HMAC: a passkey may give, reached one way, what it gives reached another for that hash of the input (a phone's
   *  passkey from another device's browser over its QR code, and in the phone's own browser). */
  hashesOnceMore = false;
  /** What the browser says of the authenticator that answered (PublicKeyCredential.authenticatorAttachment); null, it
   *  says nothing. */
  attachment: string | null = null;
  /** The flags byte of the authenticator's data (user present 0x01, user verified 0x04). */
  flags = 0;
  /** The path gives the PRF's first output alone, a second asked for or not (a browser or
   *  an authenticator that evaluates one salt). */
  firstOnly = false;

  add(id: string, opts: Partial<FakeCredential> = {}) {
    this.creds.set(id, { id, secret: C.random(32), ...opts });
    return this;
  }

  /** What the credential's PRF gives for a salt: what the page derives its wrap's key from. */
  prf(id: string, salt: Uint8Array): Promise<C.Bytes> {
    return hmac(this.creds.get(id)!.secret, salt);
  }

  get = async (options: CredentialRequestOptions): Promise<unknown> => {
    const pk = options.publicKey!;
    const allow = (pk.allowCredentials ?? []).map((c) => C.b64u(bytesOf(c.id)));
    const byCredential = (pk.extensions?.prf?.evalByCredential ?? {}) as Record<string, { first: BufferSource; second?: BufferSource }>;
    this.ceremonies.push({
      challenge: bytesOf(pk.challenge), allow, userVerification: pk.userVerification, rpId: pk.rpId,
      salts: Object.fromEntries(Object.entries(byCredential).map(([k, v]) => [k, C.b64u(bytesOf(v.first))])),
      seconds: Object.fromEntries(Object.entries(byCredential).filter(([, v]) => v.second).map(([k, v]) => [k, C.b64u(bytesOf(v.second!))])),
    });
    const id = this.choose(allow.length ? allow : [...this.creds.keys()]);
    const cred = this.creds.get(id);
    if (!cred) throw new DOMException("No such credential here.", "NotAllowedError");
    const salts = byCredential[id];
    let results: AuthenticationExtensionsClientOutputs = {};
    if (salts?.first && !cred.noPrf) {
      const out = async (salt: BufferSource) => {
        const input = this.hashesOnceMore ? await webauthnPrfHash(bytesOf(salt)) : bytesOf(salt);
        const o = cred.unstable ? C.random(32) : await hmac(cred.secret, input);
        this.outputs.push(o.buffer);
        return o.buffer;
      };
      results = { prf: { results: { first: await out(salts.first), ...(salts.second && !this.firstOnly ? { second: await out(salts.second) } : {}) } } };
    }
    const clientData = C.utf8(JSON.stringify({ type: "webauthn.get", challenge: C.b64u(bytesOf(pk.challenge)) }));
    const authenticatorData = new Uint8Array(37);
    authenticatorData[32] = this.flags;
    return {
      id, type: "public-key",
      response: { clientDataJSON: clientData.buffer, authenticatorData: authenticatorData.buffer, signature: new Uint8Array(64).buffer, userHandle: null },
      getClientExtensionResults: () => results,
      ...(this.attachment !== null ? { authenticatorAttachment: this.attachment } : {}),
    };
  };

  /** navigator.credentials, and PublicKeyCredential, as this browser's. */
  install() {
    Object.defineProperty(navigator, "credentials", { value: { get: this.get, create: async () => { throw new Error("not here"); } }, configurable: true });
  }
}

/** One of the account's passkeys; `judge_again`, one still waiting to be judged (it could become a hardware key); `kind`
 *  and `maker`, what the server says it is (passkeys.kind_of: hardware, synced, security-key or device) and, for a
 *  hardware key, its model as the FIDO metadata names it, served only where set, as a server before they were is. */
export interface FakePasskey { id: string; credential_id: string; name: string; tier: "hardware" | "device"; prf: boolean; created_at: number; fingerprint: string; judge_again?: boolean;
  kind?: string; maker?: string | null }
interface Call { method: string; path: string; body: Record<string, unknown> | undefined }
type Reply = [number, unknown];
/** What a change does as its hold ends: checked again first, and a refusal, nothing of it made, when it no longer
 *  holds. */
type Apply = () => Reply | void;
/** A statement checked, and what uses up its nonce as its change is placed. */
interface Statement { payload: Record<string, unknown>; text: string; signer: FakePasskey; use: () => void }
/** A change's word checked (by: "recovery", or the passkey that confirmed it), and what uses it up as it is placed. */
interface Word { by: string; use: () => void }

/** A statement as a waiting change's body keeps it (e2e_keys._stored): its canonical text, its assertion as compact
 *  JSON, its passkey. */
const keptStatement = (st: Statement, b: Record<string, unknown>) => ({
  payload: st.text, credential: JSON.stringify((b.statement as { credential?: unknown } | undefined)?.credential ?? null), passkey_id: st.signer.id });
/** A waiting change's body as GET serves it (e2e_keys._served_body): as kept, but the assertion (`credential`) of each
 *  statement in it, which the statements served go without too. A change's own `credential`, an
 *  enrolment's, is the passkey's record and stays. */
const servedBody = (body: Record<string, unknown>) => {
  const unsigned = (s: unknown) => s && typeof s === "object" && !Array.isArray(s)
    ? Object.fromEntries(Object.entries(s as Record<string, unknown>).filter(([k]) => k !== "credential")) : s;
  const served: Record<string, unknown> = { ...body };
  if (body.statement && typeof body.statement === "object" && !Array.isArray(body.statement)) served.statement = unsigned(body.statement);
  if (Array.isArray(body.statements)) served.statements = body.statements.map(unsigned);
  return served;
};
/** A list of objects as the server keeps it, by one field (grants by `to`, content keys by key_id): {key: value}. */
const byKey = (list: unknown, key: string, value: string) =>
  Object.fromEntries((Array.isArray(list) ? list as Record<string, unknown>[] : []).map((x) => [String(x[key]), x[value]]));
/** A new master key's parts as a waiting change's body keeps them (e2e_keys._revocation_body): the chain's link apart
 *  from the key it replaces, the grants by `to`, the content keys wrapped again by key_id. */
const keptNewMaster = (b: Record<string, unknown>) => {
  const chain = (b.chain ?? {}) as { previous?: unknown; wrapped?: unknown };
  return { mk_id: b.mk_id, previous: chain.previous, chain: chain.wrapped, grants: byKey(b.grants, "to", "sealed"), roster: b.roster,
    content_key: b.content_key, content_keys: byKey(b.content_keys, "key_id", "wrapped"), abandon: b.abandon ?? [],
    remove: b.remove ?? [], high_security: b.high_security === true, recovery: b.recovery ?? null };
};
/** A credential as a waiting change's body keeps it (e2e_keys._credential). */
const keptCredential = (passkey: FakePasskey, c: { salt: string; public_key: string; wrapped: string; anchor?: string }) => ({
  passkey_id: passkey.id, credential_id: passkey.credential_id, name: passkey.name, salt: c.salt, public_key: c.public_key, wrapped: c.wrapped,
  // a post-quantum credential's anchor, kept as e2e_keys._credential keeps it
  ...(typeof c.anchor === "string" ? { anchor: c.anchor } : {}) });

const HEX32 = /^[0-9a-f]{32}$/;
const fail = (status: number, detail: string): Reply => [status, { detail }];
// the ceremonies a confirmation is made for (passkeys.CONFIRM_PURPOSES): an account change's, and encryption's touches
const CONFIRM = "confirm";
const TOUCH_ENROL = "e2e-enrol-touch", TOUCH_ENROL_OWN = "e2e-enrol-own", TOUCH_ANCHOR = "e2e-anchor-touch", TOUCH_CANCEL = "e2e-cancel";

// the forms the server takes what the page sends in (e2e_keys._bytes, _key, _sealed; e2e.form, e2e.usable_key), so a
// page that regressed on them fails its tests as it would against the server
const WRAP_MIN = 28, WRAP_MAX = 1024, ROSTER_MAX = 131072, GRANT_MAX = 512;
/** an account's passkeys at most (passkeys.MAX_PASSKEYS), and so renewed anchors in one change */
const MAX_PASSKEYS = 20;
/** a statement's created_at is the time its nonce was given: 2 seconds before it to NONCE_DRIFT after (link_commands) */
const NONCE_DRIFT = 30;
/** and within CLOCK_SKEW seconds of the server's time as it comes in (link_commands._check_payload) */
const CLOCK_SKEW = 300;
/** how long a nonce, a confirmation's challenge, a confirmation and a recovery proof's challenge last
 *  (passkeys.CHALLENGE_TTL): one whose time is up is stale */
export const CHALLENGE_TTL = 300;
/** the changes a confirmation is made for (passkeys.CHANGE), written here from its pattern; anything else named is
 *  refused (passkeys.change_subject, BAD_CHANGE). An id is the server's 32 hex, or one of this server's passkey ids
 *  (`pk-` and the credential's), which stand in for the server's; the upgrade's digest is 32 hex alone. */
const CHANGE = new RegExp(`^(?:${["passkey-add", "passkey-remove:{i}", "high-security-(?:on|off)", "sign-out-everywhere",
  "link-ticket:(?:new|{i})", "link-revoke:{i}", "link-approve:{i}(?::{i})?", "connector-on", "connector-allow:{i}:(?:propose|read)",
  "request-approve:{i}", "signer-ticket", "e2e-upgrade:{h}"].join("|").replaceAll("{i}", "(?:[0-9a-f]{32}|pk-[A-Za-z0-9_-]+)").replaceAll("{h}", "[0-9a-f]{32}")})$`);
/** A request field FastAPI reads as pydantic's bool in lax mode (pydantic 2.13, as the server's request models declare
 *  it): its value, from true or false, 0 or 1, or a word it reads in any case; or the 422 entry that refuses it, for an
 *  integral number pydantic reads as a 64-bit integer (-2**63 to 2**63 - 1) or a string it cannot read (bool_parsing),
 *  for null, a number past those or anything else (bool_type), and, where the field has no default (`dflt`), for one
 *  not given (missing). JSON.parse leaves no difference at the edges, which pydantic sees: 9223372036854775807 lands
 *  on 2**63 here and is read as past it (pydantic: within), and -2**63 written as a float is read within (pydantic:
 *  past it, as every float from -2**63 out). */
function laxBool(body: Record<string, unknown>, field: string, dflt?: boolean): boolean | Record<string, unknown> {
  const v = body[field];
  if (v === undefined && dflt !== undefined) return dflt;
  if (v === undefined) return { type: "missing", loc: ["body", field], msg: "Field required", input: body };
  if (typeof v === "boolean") return v;
  const words = typeof v === "string" ? v.toLowerCase() : null;
  if (v === 1 || (words !== null && ["1", "on", "t", "true", "y", "yes"].includes(words))) return true;
  if (v === 0 || (words !== null && ["0", "off", "f", "false", "n", "no"].includes(words))) return false;
  return (typeof v === "number" && Number.isInteger(v) && -(2 ** 63) <= v && v < 2 ** 63) || words !== null
    ? { type: "bool_parsing", loc: ["body", field], msg: "Input should be a valid boolean, unable to interpret input", input: v }
    : { type: "bool_type", loc: ["body", field], msg: "Input should be a valid boolean", input: v };
}

/** The bytes of base64url without padding, written as its bytes give it (C.unb64u reads nothing else): `exact` bytes,
 *  or `least` to `most`; null otherwise. */
function sentBytes(value: unknown, exact?: number, least = WRAP_MIN, most = WRAP_MAX): Uint8Array | null {
  const raw = C.unb64u(value);
  return raw && (exact !== undefined ? raw.length === exact : least <= raw.length && raw.length <= most) ? raw : null;
}
/** Whether `value` is an X25519 public key a key can be sealed to: 32 bytes whose shared secret with a fresh key is not
 *  all zero (a point of low order gives zero with every key). */
async function usableKey(value: unknown): Promise<boolean> {
  const raw = sentBytes(value, 32);
  if (!raw) return false;
  try {
    const mine = (await subtle().generateKey({ name: "X25519" }, false, ["deriveBits"])) as CryptoKeyPair;
    const shared = new Uint8Array(await subtle().deriveBits({ name: "X25519", public: await C.x25519Public(raw) }, mine.privateKey, 256));
    return shared.some((x) => x !== 0);
  } catch {
    return false;                                       // WebCrypto refuses an all-zero result itself
  }
}
/** Whether `value` is a post-quantum public key a key can be sealed to (e2e.usable_hybrid_key): 1,216 bytes, its ML-KEM
 *  half passing FIPS 203's check, its X25519 half usable. */
async function usableHybrid(value: unknown): Promise<boolean> {
  const raw = sentBytes(value, C.HYBRID_KEY_LEN);
  return !!raw && C.usableHybridKey(raw);
}
/** A key as the server takes a new one in for an account (e2e_keys._key): a classical account's X25519 key, a
 *  post-quantum account's (`pq`) X-Wing key. */
const keyOk = (value: unknown, pq: boolean) => (pq ? usableHybrid(value) : usableKey(value));
/** Whether `value` is a grant of the master key as the server reads one (e2e_keys._sealed): an envelope (C.envelopeForm:
 *  e2e1. and base64url written as its bytes give it) of the form `form` (form 1 for a classical recipient, 2 for a
 *  post-quantum one: a grant's form is its recipient's); in form 1 at most GRANT_MAX bytes inside, in form 2 the master
 *  key's 32 bytes alone, exactly 1,197 bytes as it decodes. */
function sealedOk(value: unknown, form: 1 | 2 = 1): boolean {
  const read = C.envelopeForm(value);
  if (!read || read.form !== form) return false;
  return form === 1 ? read.body.length <= C.OVERHEAD + GRANT_MAX : read.body.length === C.TALK_KEY_SEALED_LEN;
}
/** A credential as the server takes one in (e2e_keys._credential): a salt of 32 bytes, a key that can be sealed to, and
 *  a wrap of WRAP_MIN to WRAP_MAX bytes; for a post-quantum account (`pq`), an X-Wing key, a wrap of exactly 60 bytes
 *  and its anchor beside it, the master key `mk`. */
async function credentialOk(c: { salt?: unknown; public_key?: unknown; wrapped?: unknown; anchor?: unknown }, pq = false, mk?: unknown): Promise<boolean> {
  if (pq) return !!sentBytes(c.salt, 32) && (await usableHybrid(c.public_key)) && !!sentBytes(c.wrapped, C.WRAP_LEN) && HEX32.test(String(c.anchor)) && c.anchor === mk;
  return !!sentBytes(c.salt, 32) && (await usableKey(c.public_key)) && !!sentBytes(c.wrapped) && c.anchor === undefined;
}
/** A recovery key as the server takes one in (e2e_keys._recovery): a key that can be sealed to, and its anchor's wrap. */
async function recoveryOk(r: { public_key?: unknown; anchor?: unknown } | null | undefined, pq = false): Promise<boolean> {
  return !!r && (await keyOk(r.public_key, pq)) && !!sentBytes(r.anchor);
}
/** TurnOn's option for an account whose keys an older release made (classical, X25519): what the tests of classical accounts
 *  stand them in with, since the page makes post-quantum keys alone now. */
export const CLASSICAL = { classical: true } as const;
/** What a server without ML-KEM answers what needs it, 503 (e2e.MLKEM_REFUSED). */
export const NO_MLKEM_WORDS = "This server's cryptography cannot use ML-KEM, which a post-quantum key needs. Post-quantum keys cannot be made, checked or sealed to here until it can: install a cryptography wheel of 48.0.0 or later from PyPI, or one built against OpenSSL 3.5 or later.";
/** passkeys.OLD_PAGE: what a post-quantum account answers a write that does not say `kem: "x-wing"`, 400. */
export const OLD_PAGE = "This page is older than the ADE it talks to: reload it, then try again.";
/** The server's words for its post-quantum rules (ade/e2e_keys.py). */
export const CLASSICAL_FORMS = "This account's keys are classical: they change in the classical forms (no kem) until the account is upgraded to post-quantum keys.";
export const NOT_CLASSICAL = "This account's keys are post-quantum already: there is nothing to upgrade.";
export const CLASSICAL_AFTER = "This account has stated a post-quantum key: no classical key is stated after it (a version-3 statement, a post-quantum key, or the upgrade).";
export const ERA_CHANGED = "The account's keys are no longer classical or post-quantum as they were when this change was made: read the keys again.";
/** e2e_keys.NOT_ON: a change to the keys while encryption is off (rotate, revoke, off by _ready; a passkey added to it,
 *  renewed anchors, the upgrade), 409. */
export const NOT_ON = "End-to-end encryption is not on for this account: turn it on first.";
/** auth._open_request's words (400 as main answers an AuthError): no device is waiting with the code a confirmation is
 *  asked for. */
export const NO_DEVICE_WAITING = "No device is waiting with that code: it may have been used, or have expired.";
/** passkeys.NO_PASSKEY: confirmation options asked for where the account has no passkey to offer, 409. */
export const NO_PASSKEY = "This account has no passkey to confirm with.";
export const UPGRADE_OWN = "Each passkey that stays in the upgrade comes with a confirmation its own passkey made at its touch, for this upgrade: touch that passkey too, or leave it out to remove it.";
export const KEPT_PROOFS = "An upgrade that keeps the recovery code proves it twice: the code in force (recovery_proof) and the post-quantum recovery key made from it (new_recovery_proof).";
export const UPGRADE_KEM = "An upgrade to post-quantum keys says so: kem x-wing.";
/** The fold's words (e2e_keys.FOLD_*): a classical account's turn-on again that makes its new master key as the
 *  upgrade does, in one change. */
export const FOLD_REKEY = "Turning encryption on again upgrades the keys only with a new master key made from the current one (mk_id, chain, grants, roster, content_keys).";
export const FOLD_EVERY = "Turning encryption on again with post-quantum keys gives every passkey that unlocks it a new key, made at its own touch: touch each one, or first remove from your account a passkey that is not at hand.";
export const FOLD_OWN = "Each passkey that unlocks encryption comes with a confirmation its own passkey made at its touch, for this change: touch that passkey too.";
/** What a turn-on with a new master key made from the current one names besides it (e2e_keys._turn_on_plan). */
export const REKEY_OWN = "Turning encryption on with a new master key made from the current one names no credential or recovery key of its own, and does not start again.";
export const PROOF_STALE = "That recovery proof has expired or was used: ask for a new one.";
export const PROOF_WRONG = "That is not the recovery code's proof.";
/** Re-sealing's words (e2e_keys.RESEAL_*), and a row's or a conversation's id as GET gives it: LINK_ID:SESSION_ID. */
export const RESEAL_NOT_PQ = "Only a post-quantum account's stored texts are sealed again: upgrade its keys first.";
export const RESEAL_OFF = "End-to-end encryption is not on for this account: nothing is sealed again.";
export const RESEAL_GONE = "That stored text is not one to seal again: it was sealed again, sent again or deleted since it was read, or it is a question that may yet be answered, whose answer is signed over its text as stored. Read them again.";
export const RESEAL_CHANGED = "What is stored there changed since it was read: read the stored texts again.";
export const RESEAL_WHERE = "A re-seal item's where is one GET /api/ade/e2e/reseal gave: {table, id, field?}.";
/** The plaintext bytes a link's envelope holds at most on each path (e2e.EVENT_PLAIN, HISTORY_PLAIN,
 *  HISTORY_WITHHELD_PLAIN, TALK_PLAIN): what a text sealed again holds at most, unless it was stored longer (held). */
export const EVENT_PLAIN = 16400;
export const HISTORY_PLAIN = 6000;
export const HISTORY_WITHHELD_PLAIN = 1300;
export const TALK_PLAIN = 16400;

/** As e2e_keys._held: the plaintext bytes a value sealed again may hold where `stored` was, the path's cap, or as many
 *  as the stored form-1 envelope holds where that is more (one the server sealed in place holds what a link sent in
 *  the clear, which can pass the cap), never more. */
function held(stored: unknown, cap: number): number {
  const read = C.envelopeForm(stored);
  return read?.form === 1 ? Math.max(cap, read.body.length - C.OVERHEAD) : cap;
}

/** Whether a stored value claims to be an envelope (e2e.sealed: it begins e2e1.); anything else a conversation holds in
 *  the clear. */
const claimsSealed = (v: unknown) => typeof v === "string" && v.startsWith(C.PREFIX);
/** A message's words as they are sealed, {"text"} in RFC 8785 form (e2e.plaintext). */
const wordsSealed = (text: unknown) => C.canonicalBytes({ text: text ?? null }).length;

/** As e2e_keys._message_held: the plaintext bytes a conversation's message sealed again may hold where `stored` was:
 *  one sealed as held has it; one in the clear (a client sent the conversation half sealed while encryption was off, and
 *  a turn-on did not seal it in place), sealed for the first time, the cap or its words as they are sealed where that is
 *  more. */
function messageHeld(stored: unknown): number {
  return claimsSealed(stored) ? held(stored, TALK_PLAIN) : Math.max(TALK_PLAIN, wordsSealed(stored));
}

/** As e2e_keys._talk_size: a stored conversation's size, each message as it comes to sealed: one sealed as it decodes,
 *  one in the clear as form 3 holds its words (their bytes and the form's 45). */
function talkSize(messages: Record<string, unknown>[]): number {
  return messages.reduce((n, m) => n + (claimsSealed(m.text) ? (C.envelopeForm(m.text)?.body.length ?? 0) : C.OVERHEAD3 + wordsSealed(m.text)), 0);
}

/** As e2e.read: why `value` is not an envelope of form `form` holding at most `most` plaintext bytes, in the server's
 *  words (a text that is no envelope at all in one of them, where the server's are finer); null where it is one. */
function envelopeWhy(value: unknown, most: number, form: 2 | 3): string | null {
  const read = C.envelopeForm(value);
  return !read ? "An encrypted text is e2e1. and base64url without padding."
    : read.form !== form ? `An encrypted text here is of form ${form}.`
    : read.body.length > (form === 2 ? C.OVERHEAD2 : C.OVERHEAD3) + most ? `An encrypted text here holds at most ${most} bytes.`
    : null;
}

/** As e2e_keys._sealed_again: `value` an envelope of form `form` holding at most `most` plaintext bytes (envelopeWhy)
 *  and naming `keyId`; else 422 naming `what`. */
function sealedAgain(value: unknown, most: number, form: 2 | 3, keyId: unknown, what: string): Reply | null {
  const why = envelopeWhy(value, most, form);
  if (why) return fail(422, `${what} is not sealed again in its form: ${why}`);
  if (C.hex(C.envelopeForm(value)!.body.subarray(1, 17)) !== keyId) return fail(422, `${what} is not sealed again under the key it must be.`);
  return null;
}
const RESEAL_ID = /^[0-9a-f]{32}:[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/;
/** Re-sealing's tables in the order GET serves them (e2e_keys.RESEAL_TABLES), the bytes of values a page holds past its
 *  first item (RESEAL_PAGE_BYTES), and a page's cursor as GET gives it (_RESEAL_AFTER). */
const RESEAL_TABLES = ["events", "rows", "talks"];
const RESEAL_PAGE_BYTES = 512 * 1024;
const RESEAL_AFTER = /^(?:0:([1-9][0-9]{0,15})|([12]):([0-9a-f]{32}:[A-Za-z0-9][A-Za-z0-9_-]{0,99}))$/;
/** Where a stored text is in GET's order: its table's place, and its id. */
type Place = [number, number | string];
const placeOf = (x: { where: Record<string, unknown> }): Place => {
  const t = RESEAL_TABLES.indexOf(String(x.where.table));
  return [t, t === 0 ? Number(x.where.id) : String(x.where.id)];
};
const byPlace = (a: Place, b: Place) => a[0] - b[0] || (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0);
/** Whether a stored value is one the server still stores in form 1 (e2e_keys._form1): a text in form 1, or a
 *  conversation holding a message in form 1. */
function storedForm1(value: unknown): boolean {
  const form1 = (v: unknown) => C.envelopeForm(v)?.form === 1;
  if (typeof value === "string") return form1(value);
  const messages = (value as { messages?: unknown } | null)?.messages;
  return Array.isArray(messages) && messages.some((m) => form1((m as { text?: unknown } | null)?.text));
}
const HYBRID_KEY_WORDS = "A post-quantum key is 1,216 bytes: an ML-KEM-768 key whose every coefficient is below 3,329, then an X25519 key a secret can be agreed with.";
/** The purposes a recovery proof is asked for (e2e_keys.PROOF_PURPOSES), re-sealing's apart: turn-on for the fold that
 *  keeps the code. */
const PROOF_PURPOSES = ["rotate", "revoke", "credentials", "off", "anchors", "passkey", "cancel", "turn-on"];

// the server's words (ade/e2e_keys.py, passkeys.py), where a test reads them
export const WORDS = {
  stale: "The account's master key has changed since: read the keys again.",
  rekeyDue: "A passkey that unlocked encryption was removed, or left out by high security, while it was off: turning it on again makes a new master key, as a revocation does (mk_id, chain, grants, roster, content_keys).",
  newMaster: "A revocation makes a new master key.",
  noHardware: "This account has no hardware key: add one first (two is better, one of them kept apart).",
  lastHardware: "That is the account's last hardware key, and high security is on: add another hardware key first, or turn high security off.",
  keyWaiting: "A passkey of this account still waits to be judged, and could become a hardware key without a hardware key's word: remove it, or turn high security on once it is judged.",
  needsHardware: "High security is on: confirm it with one of this account's hardware keys. A password or another passkey does not confirm it.",
  needsHardwareToTurn: "Turning high security on or off takes one of this account's hardware keys.",
  onlyHardwareSigns: "High security is on: only one of this account's hardware keys signs a command to a session.",
  confirmGone: "The passkey that gave that confirmation is not this account's now, or not a hardware key: confirm again.",
  confirmOther: "That confirmation was made for something else: confirm this one again.",
  confirmStale: "That confirmation has expired or was used; confirm again.",
  confirmForNothing: "That confirmation names no change of its own, as a page from an older release makes them: reload the page and confirm again.",
  badChange: "That is not a change a confirmation is made for: reload the page and try again.",
  nonceStale: "the nonce is not one this server gave this account for this purpose, or it expired",
  passkeyRequestStale: "This passkey request has expired or was used; start again.",
  codeChanged: "The recovery code has changed since: read the keys again.",
  needsPasskey: "End-to-end encryption is on: confirm it with one of this account's passkeys, or with the recovery code. The password does not confirm it.",
  needsConfirm: "Confirm it is you first, with one of this account's passkeys or its password: a sign-in alone does not add or remove a passkey, or sign every device out.",
  revokesOnly: "End-to-end encryption is on: a passkey is removed, and high security turned on, from Account, Encryption, which replaces the master key so that what is removed opens nothing.",
  oneChange: "Name one change: a device's code, or a change.",
};
/** What a statement that is not the account's next is told (e2e_keys._check_next): the seq it must have, and the
 *  statement its prev is the sha256 of. */
export const nextWords = (seq: number) =>
  `A statement is the account's next: seq ${seq}, and prev the sha256 of statement ${seq - 1}${seq === 1 ? " (none: this is the first)." : "."}`;
export const NO_PLACE = "A change waits already, and neither the passkey that made this one nor a recovery code's proof with it could cancel that one: cancel it with one that can, or let it take effect, first.";
export const NOT_STAYING = "This change is signed by a passkey that unlocks end-to-end encryption and still does after it, or it comes with the recovery code's proof.";
export const CONFIRM_STAYING = "This change is confirmed with a passkey that unlocks end-to-end encryption already and still does after it (not the one being added), or with the recovery code's proof.";
export const ANCHOR_OWN = "Each renewed anchor but the confirming passkey's own comes with a confirmation its own passkey made: touch that passkey too.";
export const ANCHOR_CODE = "The recovery code's anchor is written again only with the recovery code's proof: a passkey's confirmation does not cover it.";
export const ENROL_OWN = "A passkey is added to end-to-end encryption only with a confirmation it made itself, at its own touch: add it again, touching it.";
export const SALT_SAME = "A renewed anchor, and a passkey's new key in the upgrade, is wrapped under a fresh salt, not the one its record has.";
export const CANCEL_TAKES = "Cancelling a change to end-to-end encryption takes a touch of one of this account's passkeys that may cancel it, or the recovery code: signing in alone does not cancel it.";
export const CANCEL_REMOVED = "That passkey may not cancel this change (one it removes, or one made with the recovery code, which only the recovery code cancels): use another passkey, or the recovery code.";

/** The server's routes for the account's keys, over rows it keeps as the server does. */
export class FakeServer {
  account = "acct-1";
  deployment = "pp.example";
  now = 1790000000;
  passkeys: FakePasskey[] = [];
  high = false;
  statements: { seq: number; id: string; purpose: string; key_id: string | null; public_key: string | null; retires: string | null;
    payload: string; passkey_id: string; created_at: number; retired_at: number | null }[] = [];
  credentials: (E2ECredential & { created_at: number })[] = [];
  recovery: E2ERecovery | null = null;
  grants: E2EGrant[] = [];
  chain: E2EChainLink[] = [];
  roster: { mk_id: string; sealed: string } | null = null;
  contentKeys: E2EContentKey[] = [];
  links: E2ELink[] = [];
  /** the change that waits; `removes`, the passkeys a revocation names, and `turnsHigh`, whether it turns high security
   *  on (R, what it removes, follows from them as the server judges it: removedBy); `removesCode`, whether it replaces
   *  the recovery code (said, as replaces_code, and no part of who may cancel it: the code is the root); `madeBy`, the
   *  passkey that signed or confirmed it ("recovery" for the recovery code's proof), as the server's made_by; `byCode`,
   *  whether the recovery code's proof came with it; `body`, what was asked, as GET serves it. Who may cancel it
   *  follows from them (cancellers). */
  pending: { id: string; change: string; takes_effect_at: number; apply: Apply; removes?: string[]; turnsHigh?: boolean;
    removesCode?: boolean; madeBy?: string; byCode?: boolean; body?: Record<string, unknown>; told?: number } | null = null;
  /** why each change whose hold ended did not take effect, as the server keeps it on the hold */
  readonly refused: string[] = [];
  /** how long a change waits, in seconds: confirmed by a passkey; and by the recovery code, or one that turns
   *  encryption off, replaces the keys, the recovery code or every other credential, or removes no passkey */
  delays = { change: 180, recovery: 86400 };
  /** how many devices a hold's notice is sent toward, as the server counts them and keeps on the hold (holds.tell: -1
   *  when it could not be sent) */
  told = 1;
  /** routine rotations are at most one in this many seconds (0: none), as OPEN_EDITION_ADE_E2E_ROTATE_EVERY */
  rotateEvery = 86400;
  /** a passkey that unlocked the keys was removed, or left out by high security, while encryption was off */
  rekey = false;
  /** what the last sealing in place sealed */
  sealed: Record<string, number> | null = null;
  /** whether the account is post-quantum (ade_e2e_state.pq): set as a post-quantum turn-on or the upgrade takes effect,
   *  never cleared */
  pq = false;
  /** whether this server can use ML-KEM (its start-up probe) */
  mlkem = true;
  /** whether a turn-on that makes the account's keys anew (its whole body: a first turn-on, or a start again with
   *  replace) is taken without kem, making a classical account, as older releases took it. The server refuses it
   *  (e2e_keys._whole_era), 400 in the words for an older page (OLD_PAGE), as it is asked and again as its hold ends.
   *  Taken here unless a test says otherwise: the page's tests of classical accounts stand in for accounts made before
   *  that, and TurnOn's CLASSICAL option makes one so. */
  classicalWholeTurnOn = true;
  /** what re-sealing reads (a test puts the account's stored form-1 texts here; withheld, a History row's, which its
   *  cap follows: GET serves it inside the slot alone), how many a page serves (the server's 100, e2e_keys.RESEAL_PAGE,
   *  which a test may lower for small pages), and each swap made, by its hashes (ade_e2e_reseal_log) */
  stored: { where: Record<string, unknown>; value: unknown; slot?: string; withheld?: boolean; open?: boolean }[] = [];
  resealPageSize = 100;
  /** what a conversation's envelopes come to at most where it held less (link.TALK_SEALED_TOTAL), which a test may
   *  lower as the server's tests do */
  talkSealedTotal = 300000;
  readonly resealLog: { where: unknown; old_sha256: string; new_sha256: string }[] = [];
  /** how many form-1 texts a post-quantum account still stores, by where (events, History rows, conversations holding
   *  one) and in all, as GET serves them (e2e_keys.classical_texts; null for a classical account): counted from `stored`
   *  as the server counts them, an open question among them, unless a test sets it */
  classicalTexts?: { events: number; rows: number; talks: number; total: number } | null;
  readonly calls: Call[] = [];
  /** a test's own answer to a route, before the server's */
  answer: ((call: Call) => Reply | null) | null = null;
  private counter = 0;
  // each confirmation: the passkey that made it, the ceremony it was made for (passkeys.CONFIRM_PURPOSES: the server
  // spends it on no other's change), and whether a hardware key made it
  private confirmations = new Map<string, { by: string; purpose: string; hardware: boolean; subject?: string; expires: number; used?: boolean }>();
  /** each confirmation's challenge, and the purpose the page asked it for, whose derived challenge it checks */
  private challenges = new Map<string, { challenge: C.Bytes; purpose: string; subject?: string; expires: number; used?: boolean }>();
  /** each recovery proof's challenge: a classical one's server key, or a post-quantum one's shared secret and the recovery
   *  key it was encapsulated to */
  private proofs = new Map<string, { priv?: CryptoKey; ss?: C.Bytes; key?: string; nonce: C.Bytes; purpose: string; digest?: string; expires: number }>();
  private nonces = new Map<string, { purpose: string; issued: number }>();   // each nonce, its purpose and when given

  addPasskey(credentialId: string, name: string, tier: "hardware" | "device" = "device",
             extra: { kind?: string; maker?: string | null; prf?: boolean } = {}): FakePasskey {
    const p: FakePasskey = { id: `pk-${credentialId}`, credential_id: credentialId, name, tier, prf: extra.prf ?? true, created_at: 1700000000 + this.passkeys.length,
      fingerprint: C.hex(C.utf8(credentialId)).padEnd(32, "0").slice(0, 32),
      ...(extra.kind !== undefined ? { kind: extra.kind } : {}), ...(extra.maker !== undefined ? { maker: extra.maker } : {}) };
    this.passkeys.push(p);
    return p;
  }

  private id(prefix: string) { return `${prefix}-${++this.counter}`; }
  private currentMk() { return this.roster?.mk_id ?? null; }
  private on() { return this.statements.at(-1)?.purpose === "e2e-key"; }

  /** What GET /api/ade/e2e serves: a copy, taken now, as one read of the database is. */
  view() {
    return JSON.parse(JSON.stringify(this.served()));
  }

  private served() {
    const enrolled = new Set(this.credentials.map((c) => c.passkey_id));
    const p = this.pending;
    const gone = p ? this.removedBy(p.removes, p.turnsHigh) : new Set<string>();
    return {
      on: this.on(), newest: this.statements.length ? { seq: this.statements.at(-1)!.seq, key_id: this.statements.at(-1)!.key_id } : null,
      statements: this.statements.map((s) => ({ ...s, digest: "" })),
      mk_id: this.currentMk(), credentials: this.credentials, recovery: this.recovery, grants: this.grants, chain: this.chain,
      roster: this.roster, content_keys: this.contentKeys, high_security: this.high,
      passkeys: this.passkeys.map((k) => ({ id: k.id, credential_id: k.credential_id, name: k.name, tier: k.tier, prf: k.prf, created_at: k.created_at,
        enrolled: enrolled.has(k.id), can_enrol: !enrolled.has(k.id) && (!this.high || k.tier === "hardware"),
        ...(k.kind !== undefined ? { kind: k.kind } : {}), ...(k.maker !== undefined ? { maker: k.maker } : {}) })),
      links: this.links, pending: p ? { id: p.id, change: p.change, takes_effect_at: p.takes_effect_at,
        cancel_by: this.cancellers().map((k) => k.id), cancel_by_code: !!this.recovery, made_by: p.madeBy ?? null,
        by_code: this.codeBacked(), removes: this.passkeys.filter((k) => gone.has(k.id)).map((k) => k.id),
        replaces_code: !!p.removesCode, body: servedBody(p.body ?? {}), told: p.told ?? null } : null,
      to_seal: this.on() ? null : { events: 3, left: 1, rows: 2, talks: 1 },
      rekey_due: !this.on() && !!this.currentMk() && this.rekeyDue(), compaction_due: false, sealed: this.sealed,
      holds: { change: this.delays.change, recovery: this.delays.recovery, long: this.delays.recovery },
      pq: this.pq, mlkem: this.mlkem, classical_texts: this.pq ? this.classicalCounts() : null,
    };
  }

  /** The enrolled credentials that stay as the keys stand, by credential id: their passkey the account's, and a hardware
   *  key while high security is (or will be) on; but those whose passkeys `remove` names. */
  private staying(remove: string[] = [], high = this.high): Set<string> {
    const kept = this.passkeys.filter((p) => !remove.includes(p.id) && (!high || p.tier === "hardware"));
    return new Set(this.credentials.filter((c) => kept.some((p) => p.id === c.passkey_id)).map((c) => c.credential_id));
  }

  /** Whether turning on again must make a new master key: one was dropped (rekey), or a credential no longer stays. */
  private rekeyDue(): boolean {
    return this.rekey || this.staying().size !== this.credentials.length;
  }

  /** Inside the change that removes passkeys or turns high security on, while encryption is off: their credentials no
   *  longer unlock the keys, and go with their grants; a new master key is then due (e2e_keys.credentials_dropped). */
  private credentialsDropped(passkeyIds: string[]) {
    const gone = this.credentials.filter((c) => passkeyIds.includes(c.passkey_id));
    if (!gone.length) return;
    this.grants = this.grants.filter((g) => !gone.some((c) => c.credential_id === g.to));
    this.credentials = this.credentials.filter((c) => !gone.includes(c));
    this.rekey = true;
  }

  /** Whether the change that waits is code-backed: made by the recovery code, or with its proof. */
  private codeBacked(): boolean {
    return !!this.pending && (this.pending.madeBy === "recovery" || !!this.pending.byCode);
  }

  /** The passkeys whose confirmation cancels the change that waits, as e2e_keys.cancellers decides, the recovery code the
   *  root: none when it is code-backed; else those it does not remove (R), and those it removes too when it was signed by
   *  a passkey and leaves no passkey besides its signer. Only passkeys that could cancel count, here and in R: the
   *  hardware keys alone while high security is on. (The recovery code in force cancels any change, whenever the
   *  account has one.) */
  private cancellers(): FakePasskey[] {
    const p = this.pending;
    const keys = this.passkeys.filter((k) => !this.high || k.tier === "hardware");
    if (this.codeBacked()) return [];
    const gone = new Set([...this.removedBy(p?.removes, p?.turnsHigh)].filter((id) => keys.some((k) => k.id === id)));
    const signed = !!p?.madeBy && p.madeBy !== "recovery";
    const alone = signed && keys.every((k) => k.id === p!.madeBy || gone.has(k.id));
    return keys.filter((k) => alone || !gone.has(k.id));
  }

  /** R, as e2e_keys.removed_by judges it now: the passkeys a revocation names, and, when it really turns high security
   *  on (it is off now), every one but the hardware keys; a flag that repeats high security already on removes none. */
  private removedBy(remove: string[] = [], turnsHigh = false): Set<string> {
    const gone = new Set(remove);
    if (turnsHigh && !this.high) for (const k of this.passkeys) if (k.tier !== "hardware") gone.add(k.id);
    return gone;
  }

  /** Whether `passkeyId` unlocks the keys and goes on doing so as they stand: its credential enrolled, its passkey the
   *  account's, a hardware key while high security is on (e2e_keys._staying). */
  private staysEnrolled(passkeyId: string | undefined): boolean {
    const p = this.passkeys.find((k) => k.id === passkeyId);
    return !!p && this.credentials.some((c) => c.passkey_id === p.id) && (!this.high || p.tier === "hardware");
  }

  /** As the server asks again as a signed change's hold ends (_may_sign, then _signer_still): while high security is on
   *  only a hardware key signs; and, unless the code's proof came with it, its signer still unlocks the keys and stays
   *  (in `stay`, the credentials a revocation leaves; as the keys stand otherwise). A refusal, or null. */
  private signerStill(signer: FakePasskey, byCode: boolean, stay?: Set<string>): Reply | null {
    if (this.high && signer.tier !== "hardware") return fail(403, WORDS.onlyHardwareSigns);
    if (byCode) return null;
    const unlocks = stay ? this.credentials.some((c) => c.passkey_id === signer.id && stay.has(c.credential_id)) : this.staysEnrolled(signer.id);
    return unlocks ? null : fail(403, NOT_STAYING);
  }

  /** As holds.settle as a change is asked: one whose time has come takes effect first, not taken the place of. */
  private settleDue() {
    if (this.pending && this.pending.takes_effect_at <= this.now) this.settle();
  }

  /** The held change takes effect, as the server's settling does when its time has come: checked again first, and
   *  dropped, its reason kept (refused), when it no longer holds. */
  settle() {
    const p = this.pending;
    this.pending = null;
    const refused = p?.apply();
    if (refused) this.refused.push(String((refused[1] as { detail?: unknown }).detail));
  }

  /** As the server stores a statement when its hold ends (_store_statement): still the account's next (the newest
   *  statement the one it was made after), naming a key no statement named, and no classical key after a post-quantum
   *  one. */
  private stillNext(st: Statement, after: string | null): Reply | null {
    if ((this.statements.at(-1)?.payload ?? null) !== after) return fail(409, nextWords((this.statements.at(-1)?.seq ?? 0) + 1));
    if (st.payload.purpose === "e2e-key" && this.statements.some((s) => s.key_id === st.payload.key_id)) return fail(409, "That key is stated already: make a new one.");
    return this.classicalAfter(st);
  }

  /** The passkey whose confirmation `c` is, made for `purpose`, while it may still give one, in the server's order
   *  (passkeys._use_confirmation): none given, the words for a hardware key's where one is needed (`hardware`, the
   *  change's own words for that, or high security's while it is on), else `refused`; one this server did not give, or
   *  whose 300 seconds are up, stale; one not a hardware key's where one is needed; then made for this purpose, else
   *  refused and left unused, and for `subject` (a string: that change; null: none, as encryption's
   *  touches but the upgrade's are made; undefined: not checked), one made for no change offered for one being told it
   *  names none; then its passkey still the account's, and still a hardware key where one made it; and last, used
   *  already (spend), stale. A refusal otherwise. */
  private confirmer(c: string, refused: string, purpose = CONFIRM, subject?: string | null, hardware?: string): FakePasskey | Reply {
    const needs = hardware ?? (this.high ? WORDS.needsHardware : undefined);
    if (!c) return fail(403, needs ?? refused);
    const held = this.confirmations.get(c);
    if (!held || held.expires <= this.now) return fail(403, WORDS.confirmStale);
    if (needs && !held.hardware) return fail(403, needs);
    const madeFor = held.subject ?? null;
    if (held.purpose !== purpose || (subject !== undefined && madeFor !== subject)) {
      return fail(403, held.purpose === purpose && madeFor === null ? WORDS.confirmForNothing : WORDS.confirmOther);
    }
    const p = this.passkeys.find((k) => k.id === held.by);
    if (!p || (held.hardware && p.tier !== "hardware")) return fail(403, WORDS.confirmGone);
    if (held.used) return fail(403, WORDS.confirmStale);
    return p;
  }

  /** A confirmation used up as its change is placed (passkeys._use_challenge): kept, marked used, so a second use is
   *  told it was used only after every other check, as the server tells it. */
  private spend(c: unknown) {
    const held = this.confirmations.get(String(c ?? ""));
    if (held) held.used = true;
  }

  // ---- checks, as e2e_keys.py and link_commands.py make them ----

  /** A statement checked; its nonce is used (`use`) only as its change is placed (hold), as commit_statement uses it in
   *  the transaction that places it, so a change refused before then leaves it unused. */
  private async statement(value: unknown, purposes: string[]): Promise<Statement | Reply> {
    const v = value as { payload?: Record<string, unknown>; credential?: { id?: string; response?: { clientDataJSON?: string } } } | undefined;
    const p = v?.payload;
    if (!p || !purposes.includes(String(p.purpose))) return fail(422, "The statement's purpose is not one of these.");
    const want = ["v", "deployment", "purpose", "account", "nonce", "id", "seq", "prev", "created_at", "expires_at",
      ...(p.purpose === "e2e-key" ? ["key", "key_id", "retires"] : [])].sort();
    if (JSON.stringify(Object.keys(p).sort()) !== JSON.stringify(want)) return fail(422, `A statement has exactly the fields ${want.join(", ")}.`);
    // version 3 for a post-quantum key, version 2 for a classical key and for e2e-off
    if (!(p.v === 2 || (p.v === 3 && p.purpose === "e2e-key")) || p.account !== this.account || p.deployment !== this.deployment
        || !HEX32.test(String(p.id))) return fail(422, "A statement's fields are wrong.");
    // as link_commands._check_payload, before the nonce is opened: its time within 5 minutes of the server's, its life,
    // not expired, then its key (a version-3 key is checked with ML-KEM, which a server without it cannot do: 503)
    if (Math.abs(Number(p.created_at) - this.now) > CLOCK_SKEW) return fail(422, "The payload's time is more than 5 minutes from the ADE's.");
    const life = Number(p.expires_at) - Number(p.created_at);
    if (life < 60 || life > 600) return fail(422, `A ${p.purpose} expires 60 to 600 seconds after it is made.`);
    if (Number(p.expires_at) <= this.now) return fail(422, "The payload has expired.");
    if (p.purpose === "e2e-key") {
      const hybrid = p.v === 3;
      if (hybrid && sentBytes(p.key, C.HYBRID_KEY_LEN) && !this.mlkem) return fail(503, NO_MLKEM_WORDS);
      const key = sentBytes(p.key, hybrid ? C.HYBRID_KEY_LEN : 32);
      if (!key || !(await keyOk(p.key, hybrid)) || p.key_id !== (await C.keyIdOf(key)) || !["all", "grace"].includes(String(p.retires))) return fail(422, "A statement's key is wrong.");
    }
    const given = this.nonces.get(String(p.nonce));
    if (!given || given.purpose !== p.purpose || given.issued + CHALLENGE_TTL <= this.now) return fail(400, WORDS.nonceStale);
    // made when its nonce came, from the server's time then (link_commands._verified), so no page dates a statement later
    const made = Number(p.created_at);
    if (!(given.issued - 2 <= made && made <= given.issued + NONCE_DRIFT)) {
      return fail(422, `The payload's created_at is not the time its nonce was given (within ${NONCE_DRIFT} seconds after it).`);
    }
    const text = canonicalJson(p);
    const client = JSON.parse(new TextDecoder().decode(C.unb64u(v?.credential?.response?.clientDataJSON ?? "") ?? new Uint8Array()) || "{}");
    if (client.challenge !== C.b64u(await stepped(text))) return fail(422, "the page and the server do not agree on the canonical form");
    const signer = this.passkeys.find((k) => k.credential_id === v?.credential?.id);
    if (!signer) return fail(401, "the credential is not one of this account's passkeys");
    if (this.high && signer.tier !== "hardware") return fail(403, "High security is on: only one of this account's hardware keys signs.");
    const top = this.statements.at(-1);
    const seq = top ? top.seq + 1 : 1;
    const prev = top ? C.hex(await C.sha256(C.utf8(top.payload))) : "";
    if (p.seq !== seq || p.prev !== prev) return fail(409, nextWords(seq));
    if (p.purpose === "e2e-key" && this.statements.some((s) => s.key_id === p.key_id)) return fail(409, "That key is stated already: make a new one.");
    const nonce = String(p.nonce);
    return { payload: p, text, signer, use: () => { this.nonces.delete(nonce); } };
  }

  private keep(st: Statement) {
    const p = st.payload;
    if (p.purpose === "e2e-key") {
      const until = p.retires === "all" ? this.now : this.now + 3600;
      for (const s of this.statements) if (s.purpose === "e2e-key" && (s.retired_at === null || s.retired_at > until)) s.retired_at = until;
    }
    this.statements.push({ seq: Number(p.seq), id: String(p.id), purpose: String(p.purpose), key_id: (p.key_id as string) ?? null,
      public_key: (p.key as string) ?? null, retires: (p.retires as string) ?? null, payload: st.text, passkey_id: st.signer.id,
      created_at: this.now, retired_at: null });
  }

  /** As e2e_keys._era reads a write's era, then its key's form, for `pq`, the account
   *  as the write leaves it (post-quantum, or made so by a whole turn-on that says kem, or the upgrade): any kem but
   *  x-wing, 422; a post-quantum account's write without kem, 400 in the words for a page older than the server
   *  (passkeys.OLD_PAGE); a classical account's write with kem, 422 (CLASSICAL_FORMS); a post-quantum write where this
   *  server has no ML-KEM, 503, every one, turning encryption off and renewing anchors too, though they make and check no
   *  key (the recovery code cancels any change, and to a post-quantum key it proves itself by the hybrid
   *  proof alone, which needs ML-KEM); for a turn-on that makes the account's keys anew (`whole`), no kem, 400 in the
   *  same words, unless this server takes it (classicalWholeTurnOn; e2e_keys._whole_era, right after _era); then a key
   *  statement of another version than the account's keys, 422 as the server's form check of its key words it
   *  (_content_key: the key is the statement's). Then, as _check_key_new: a version-2 key after any version-3
   *  statement, 409 (CLASSICAL_AFTER). A refusal, or null. */
  private kemChecks(b: Record<string, unknown>, st?: Statement, pq = this.pq, whole = false): Reply | null {
    if (b.kem !== undefined && b.kem !== null && b.kem !== "x-wing") return fail(422, "A write's kem is x-wing, or none.");
    const kem = b.kem === "x-wing";
    if (pq && !kem) return fail(400, OLD_PAGE);
    if (!pq && kem) return fail(422, CLASSICAL_FORMS);
    if (kem && !this.mlkem) return fail(503, NO_MLKEM_WORDS);
    if (whole && !kem && !this.classicalWholeTurnOn) return fail(400, OLD_PAGE);
    if (st?.payload.purpose === "e2e-key" && (st.payload.v === 3) !== pq) {
      return fail(422, `The content key is ${pq ? C.HYBRID_KEY_LEN : 32} bytes, in base64url without padding.`);
    }
    return this.classicalAfter(st);
  }

  /** As e2e_keys._check_key_new: no version-2 key statement once the account is post-quantum or any of its statements
   *  states a post-quantum key (a database put back to before an upgrade keeps the statements made since), 409. */
  private classicalAfter(st?: Statement): Reply | null {
    if (st?.payload.purpose !== "e2e-key" || st.payload.v === 3) return null;
    const hybrid = this.pq || this.statements.some((s) => s.purpose === "e2e-key" && (s.public_key?.length ?? 0) > 43);
    return hybrid ? fail(409, CLASSICAL_AFTER) : null;
  }

  /** As e2e_keys._era_still as a change's hold ends, from the era it was placed in (`kem`, its body's): a post-quantum
   *  change where this server has no ML-KEM now, 503, so one placed before it lost ML-KEM, which the recovery code could
   *  not cancel since, fails; then that era no longer the account's, 409 (ERA_CHANGED), but where the
   *  change makes the account post-quantum (`begins`: a whole turn-on that says kem, or the upgrade). A refusal, or
   *  null. */
  private eraStill(kem: boolean, begins = false): Reply | null {
    if (kem && !this.mlkem) return fail(503, NO_MLKEM_WORDS);
    if (kem !== this.pq && !(kem && begins)) return fail(409, ERA_CHANGED);
    return null;
  }

  private contentKey(value: unknown, payload: Record<string, unknown>, pq = this.pq): E2EContentKey | Reply {
    const v = value as E2EContentKey | undefined;
    if (!v || v.public_key !== payload.key || v.key_id !== payload.key_id || !HEX32.test(v.mk_id) || !sentBytes(v.wrapped)) {
      return fail(422, "The content key is the one its statement states (key and key_id).");
    }
    // a post-quantum account's content key: its seed in exactly 60 bytes (e2e_keys._key_wrap)
    if (pq && !sentBytes(v.wrapped, C.WRAP_LEN)) return fail(422, `The content key's wrap is ${C.WRAP_LEN} bytes, in base64url without padding.`);
    if (this.contentKeys.some((k) => k.key_id === v.key_id)) return fail(409, "That content key is stated already: make a new one.");
    return { key_id: v.key_id, public_key: v.public_key, mk_id: v.mk_id, wrapped: v.wrapped };
  }

  /** The word that confirms a change, checked: the recovery code's proof for `purpose` (by "recovery"), or a
   *  confirmation a passkey made for `touch` (by that passkey's id; e2e_keys._confirm), made for `subject` (none for
   *  encryption's touches; "passkey-add" for adding a passkey, passkeys.register_options), with `use`, which uses it up
   *  as the change is placed: the server uses it in the transaction that places the change, so a change refused before
   *  then, a wrong proof among them, leaves it unused. A refusal otherwise. */
  private async confirmed(body: Record<string, unknown>, purpose: string, touch: string, digest?: string, subject: string | null = null): Promise<Word | Reply> {
    if (body.recovery_proof) {
      const rp = body.recovery_proof as { challenge_id: string; proof: string };
      // as e2e_keys.use_proof: a post-quantum key's proof needs this server's ML-KEM; then a challenge this server gave
      // for this purpose, unexpired, and of the recovery key's own kind (a hybrid one for a post-quantum key, the
      // classical one for a classical one: the server cannot open the other kind's id, so it is stale, not wrong)
      if (this.pq && this.recovery && !this.mlkem) return fail(503, NO_MLKEM_WORDS);
      const held = this.proofs.get(rp.challenge_id);
      if (!held || held.purpose !== purpose || !this.recovery || held.expires <= this.now || !!held.ss !== this.pq) return fail(403, PROOF_STALE);
      // a re-seal's proof is good for the batch whose digest its challenge named alone, and no other proof names one
      // (e2e_keys.use_proof)
      if (held.digest !== digest) return fail(403, PROOF_WRONG);
      if (!(await this.proofHolds(held, rp.proof, purpose, this.recovery.public_key))) return fail(403, PROOF_WRONG);
      return { by: "recovery", use: () => { this.proofs.delete(rp.challenge_id); } };
    }
    const c = String(body.confirmation ?? "");
    // a passkey's confirmation alone, never the password's (passkeys.NEEDS_PASSKEY), made for this change
    const by = this.confirmer(c, WORDS.needsPasskey, touch, subject);
    if (Array.isArray(by)) return by;
    return { by: by.id, use: () => { this.spend(c); } };
  }

  /** Whether `proof` answers the challenge `held` for `purpose`, as the server checks it: a classical one's, the X25519
   *  secret of its key and the stored recovery key; a post-quantum one's, the secret it encapsulated, and only while
   *  `key` (the recovery key it is checked against: the account's, or an upgrade's new one) is the key it was made
   *  for (e2e_keys._proof_key). */
  private async proofHolds(held: { priv?: CryptoKey; ss?: C.Bytes; key?: string; nonce: C.Bytes }, proof: string, purpose: string, key: string): Promise<boolean> {
    let secret: C.Bytes;
    let info: string;
    if (held.ss) {
      if (held.key !== key) return false;
      secret = held.ss.slice();
      info = C.INFO2.recoveryProof;
    } else {
      const RK = sentBytes(key, 32);
      if (!RK || !held.priv) return false;
      secret = new Uint8Array(await subtle().deriveBits({ name: "X25519", public: await C.x25519Public(RK) }, held.priv, 256));
      info = C.INFO.recoveryProof;
    }
    const mac = await C.hkdfBytes(await C.hkdfKey(secret), info, 32);
    return C.b64u(await hmac(mac, C.concat(held.nonce, C.utf8(purpose)))) === proof;
  }

  /** The change held: a day (delays.recovery) when the recovery code confirmed it (`recovery`) or it is `long`, else
   *  delays.change; with what it removes (and whether it turns high security on), what made it (madeBy), whether the
   *  code's proof came with it, and its body as the server keeps it (`body`: keptStatement, keptNewMaster and the
   *  rest), for who may cancel it and what GET serves. It takes the place of the change that waits only as (v) allows:
   *  the code's proof with it, or the passkey that signed or confirmed it could cancel that one; else 409, and what it
   *  was asked with (its statement's nonce, its proof or confirmation) stays unused, as it is used up only as the change
   *  is placed (`use`). */
  private hold(change: string, apply: Apply,
    o: { recovery?: boolean; madeBy?: string; removes?: string[]; turnsHigh?: boolean; long?: boolean; removesCode?: boolean;
      use?: (() => void)[]; body?: Record<string, unknown> } = {}): Reply {
    // a waiting upgrade is a revocation here as everywhere (e2e_keys._in_place_of)
    if (this.pending && !(o.recovery && this.recovery) && !(o.madeBy && this.cancellers().some((k) => k.id === o.madeBy))) {
      return fail(409, NO_PLACE);
    }
    for (const used of o.use ?? []) used();
    const id = this.id("hold");
    const takes = this.now + (o.recovery || o.long ? this.delays.recovery : this.delays.change);
    this.pending = { id, change, takes_effect_at: takes, apply, removes: o.removes ?? [], turnsHigh: !!o.turnsHigh,
      removesCode: !!o.removesCode, madeBy: o.madeBy, byCode: !!o.recovery, body: o.body, told: this.told };
    return [200, { hold: { id, takes_effect_at: takes, told: this.told }, told: this.told }];
  }

  // ---- the routes ----

  fetch = async (url: string, init: RequestInit = {}): Promise<Response> => {
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    const call = { method, path: String(url), body };
    this.calls.push(call);
    const [status, data] = this.answer?.(call) ?? await this.route(call);
    return new Response(JSON.stringify(data), { status });
  };

  private async route({ method, path, body }: Call): Promise<Reply> {
    const b = (body ?? {}) as Record<string, unknown>;
    const key = `${method} ${path}`;
    if (key === "GET /api/ade/e2e") {
      this.settleDue();                                         // its time has come
      return [200, this.view()];
    }
    if (key === "GET /api/account/passkeys") return [200, { passkeys: this.passkeys.map((p) => ({ ...p, last_used_at: null, backed_up: false })), high_security: this.high }];
    if (key === "GET /api/account/confirm") {
      return [200, { needed: true, password: true, passkeys: this.passkeys.length, hardware: this.passkeys.filter((p) => p.tier === "hardware").length, high_security: this.high }];
    }
    if (key === "POST /api/ade/link/nonce") {
      const nonce = this.id("nonce");
      this.nonces.set(nonce, { purpose: String(b.purpose), issued: this.now });
      return [200, { nonce, rp_id: this.deployment, deployment: this.deployment, account: this.account, server_time: this.now,
        credentials: this.high ? this.passkeys.filter((p) => p.tier === "hardware").map((p) => p.credential_id) : [] }];
    }
    if (key === "POST /api/account/confirm/options") {
      // in the server's order (main.confirm_options): the body's own types (ConfirmOptionsIn, FastAPI's 422, its detail
      // a list of each refused field in the model's order: hardware, a bool read as pydantic reads one, then a purpose,
      // device or change given but not text); then the change it is asked for, named before the purpose is read
      // (_subject: a device's code and a change both, 422; a device's code alone, 400 as auth.approval_subject answers
      // one no device waits with, which is every code here, since no device asks to be signed in on this server; a
      // change not of passkeys.CHANGE's forms, BAD_CHANGE); then the purpose; then the keys it offers
      // (passkeys.confirm_options: the hardware keys alone while high security is on or `hardware` asks for them, and
      // 409 where it has none to offer)
      const hardware = laxBool(b, "hardware", false);
      const notText = (["purpose", "device", "change"] as const).filter((f) => b[f] !== undefined && b[f] !== null && typeof b[f] !== "string");
      const refused = [...(typeof hardware === "boolean" ? [] : [hardware]),
        ...notText.map((f) => ({ type: "string_type", loc: ["body", f], msg: "Input should be a valid string", input: b[f] }))];
      if (refused.length) return [422, { detail: refused }];
      if (b.device !== undefined && b.device !== null && b.change !== undefined && b.change !== null) return fail(422, WORDS.oneChange);
      if (typeof b.device === "string") return fail(400, NO_DEVICE_WAITING);
      if (typeof b.change === "string" && !CHANGE.test(b.change)) return fail(422, WORDS.badChange);
      if (b.purpose === undefined || b.purpose === null) return fail(400, "This page is older than the ADE it talks to: reload it, then try again.");
      const purpose = String(b.purpose);
      if (!CONFIRM_PURPOSES.includes(purpose)) return fail(422, `A confirmation's purpose is ${CONFIRM_PURPOSES.join(", ")}.`);
      const only = hardware === true || this.high;
      const offered = this.passkeys.filter((p) => !only || p.tier === "hardware");
      if (!offered.length) return fail(409, only ? WORDS.noHardware : NO_PASSKEY);
      const id = this.id("confirm");
      const challenge = C.random(32);
      this.challenges.set(id, { challenge, purpose, expires: this.now + CHALLENGE_TTL, ...(typeof b.change === "string" ? { subject: b.change } : {}) });
      return [200, { challenge_id: id, publicKey: { challenge: C.b64u(challenge), rpId: this.deployment, timeout: 300000, userVerification: "required",
        allowCredentials: offered.map((p) => ({ type: "public-key", id: p.credential_id })) } }];
    }
    if (key === "POST /api/account/confirm") {
      // as passkeys.confirm: a challenge this server did not give, or whose 300 seconds are up, is stale (400, as
      // _open_challenge words it); one used already, or an answer that is not this account's passkey's over the
      // challenge derived for its purpose, confirms nothing (403); the challenge is used once the answer holds, before
      // high security refuses a device passkey's
      const asked = this.challenges.get(String(b.challenge_id));
      if (!asked || asked.expires <= this.now) return fail(400, WORDS.passkeyRequestStale);
      const cred = b.credential as { id: string; response: { clientDataJSON: string } };
      const client = JSON.parse(new TextDecoder().decode(C.unb64u(cred?.response?.clientDataJSON) ?? new Uint8Array()) || "{}");
      if (asked.used || client.challenge !== C.b64u(await derived(asked.purpose, asked.challenge)) || !this.passkeys.some((p) => p.credential_id === cred.id)) {
        return fail(403, "That did not confirm it: use one of this account's own passkeys, or its password.");
      }
      asked.used = true;
      const by = this.passkeys.find((p) => p.credential_id === cred.id)!;
      // while high security is on, a device passkey's signature confirms nothing (passkeys.confirm)
      if (this.high && by.tier !== "hardware") return fail(403, WORDS.needsHardware);
      const c = this.id("confirmation");
      this.confirmations.set(c, { by: by.id, purpose: asked.purpose, hardware: by.tier === "hardware", subject: asked.subject, expires: this.now + CHALLENGE_TTL });
      return [200, { confirmation: c, hardware: by.tier === "hardware" }];
    }
    if (key === "POST /api/account/passkeys/options") {
      // as passkeys.registration_options: first, an account keeps at most MAX_PASSKEYS passkeys (409); then, while
      // encryption is on, a passkey's confirmation or the recovery code's proof (purpose "passkey"), never nothing;
      // while it is off, an account change's confirmation made for adding a passkey ("passkey-add"; a hardware key's
      // while high security is on), since the account has a way to make one, and a recovery proof counts for nothing
      // (the password's confirmation is not modelled here: a passkey's stands for it)
      if (this.passkeys.length >= MAX_PASSKEYS) return fail(409, `An account keeps at most ${MAX_PASSKEYS} passkeys; remove one first.`);
      if (this.on()) {
        const word = await this.confirmed(b, "passkey", CONFIRM, undefined, "passkey-add");
        if (Array.isArray(word)) return word;
        word.use();
      } else {
        const by = this.confirmer(String(b.confirmation ?? ""), WORDS.needsConfirm, CONFIRM, "passkey-add");
        if (Array.isArray(by)) return by;
        this.spend(b.confirmation);
      }
      return [200, { challenge_id: this.id("register"), publicKey: { rp: { id: this.deployment, name: "Proofpane Community" },
        user: { id: "dQ", name: "a@example.org", displayName: "a@example.org" }, challenge: C.b64u(C.random(32)),
        pubKeyCredParams: [{ type: "public-key", alg: -7 }], timeout: 300000, attestation: "direct",
        authenticatorSelection: { userVerification: "required" }, excludeCredentials: [] } }];
    }
    if (key === "POST /api/ade/e2e/recovery-proof") {
      // as e2e_keys.proof_challenge: one of its purposes; a re-seal's names its batch's digest (64 lowercase hex), and no
      // other names one; `public_key` only for the upgrade's new recovery key (purpose revoke, or turn-on for the fold,
      // on a classical account)
      const purpose = String(b.purpose);
      if (![...PROOF_PURPOSES, "reseal"].includes(purpose)) return fail(422, `A recovery proof's purpose is ${[...PROOF_PURPOSES, "reseal"].join(", ")}.`);
      const digest = b.digest ?? undefined;
      if ((purpose === "reseal") !== (digest !== undefined) || (digest !== undefined && !/^[0-9a-f]{64}$/.test(String(digest)))) {
        return fail(422, "A recovery proof for re-sealing names its batch's digest (64 lowercase hex), and no other proof names one.");
      }
      if (!this.recovery) return fail(409, "This account has no recovery code: end-to-end encryption was never turned on.");
      // a classical account has nothing to seal again to
      if (purpose === "reseal" && !this.pq) return fail(409, RESEAL_NOT_PQ);
      const pk = b.public_key ?? undefined;
      if (pk !== undefined && (!["revoke", "turn-on"].includes(purpose) || this.pq)) {
        return fail(422, "A recovery proof for a new recovery key is the upgrade's, purpose revoke (or turn-on, as encryption is turned on again), on a classical account.");
      }
      const id = this.id("proof");
      const nonce = C.random(32);
      const bound = digest !== undefined ? { digest: String(digest) } : {};
      // a post-quantum recovery key (the account's, or `public_key`, an upgrade's new one): X-Wing encapsulated to it,
      // its secret kept with the key it was made for
      const forKey = pk ?? (this.pq ? this.recovery.public_key : undefined);
      if (forKey !== undefined) {
        // as e2e_keys._key reads a new one: its length, then this server's ML-KEM, then the key check
        if (pk !== undefined && !sentBytes(pk, C.HYBRID_KEY_LEN)) return fail(422, `The new recovery key is ${C.HYBRID_KEY_LEN} bytes, in base64url without padding.`);
        if (!this.mlkem) return fail(503, NO_MLKEM_WORDS);
        if (pk !== undefined && !(await usableHybrid(pk))) return fail(422, `The new recovery key is not a post-quantum key anything can be sealed to: ${HYBRID_KEY_WORDS}`);
        const { ct, ss } = await C.xwingEncapsulate(sentBytes(forKey, C.HYBRID_KEY_LEN)!);
        this.proofs.set(id, { ss, key: String(forKey), nonce, purpose, expires: this.now + CHALLENGE_TTL, ...bound });
        return [200, { challenge_id: id, server_ct: C.b64u(ct), nonce: C.b64u(nonce), purpose, expires_at: this.now + 300, kem: "x-wing" }];
      }
      const kp = (await subtle().generateKey({ name: "X25519" }, false, ["deriveBits"])) as CryptoKeyPair;
      this.proofs.set(id, { priv: kp.privateKey, nonce, purpose, expires: this.now + CHALLENGE_TTL, ...bound });
      return [200, { challenge_id: id, server_key: C.b64u(new Uint8Array(await subtle().exportKey("raw", kp.publicKey))), nonce: C.b64u(nonce),
        purpose, expires_at: this.now + 300 }];
    }
    const removing = /^DELETE \/api\/account\/passkeys\/([^/]+)$/.exec(key);
    if (removing) return this.removePasskey(decodeURIComponent(removing[1]), b);
    if (key === "PUT /api/account/high-security") return this.highSecurity(b);
    if (key === "POST /api/ade/e2e/turn-on") return this.turnOn(b);
    if (key === "POST /api/ade/e2e/rotate") return this.rotate(b);
    if (key === "POST /api/ade/e2e/revoke") return this.revoke(b);
    if (key === "POST /api/ade/e2e/credentials") return this.enrol(b);
    if (key === "POST /api/ade/e2e/off") return this.off(b);
    if (key === "POST /api/ade/e2e/anchors") return this.anchors(b);
    if (method === "GET" && (path === "/api/ade/e2e/reseal" || path.startsWith("/api/ade/e2e/reseal?"))) return this.resealPage(path);
    if (key === "POST /api/ade/e2e/reseal") return this.reseal(b);
    const cancel = /^POST \/api\/ade\/holds\/([^/]+)\/cancel$/.exec(key);
    if (cancel && this.pending?.id === decodeURIComponent(cancel[1])) {
      const refused = await this.mayCancel(b);
      if (refused) return refused;
      this.pending = null;
      return [200, { cancelled_at: this.now }];
    }
    return fail(404, `no route for ${key}`);
  }

  /** As the server's guard on a change's hold: the recovery code's proof for purpose cancel, whenever the account has a
   *  code (the one in force, a change that replaces it included), or a confirmation made with a passkey cancellers
   *  names; never the sign-in alone. A refusal, or null. */
  private async mayCancel(b: Record<string, unknown>): Promise<Reply | null> {
    if (b.recovery_proof) {
      if (!this.recovery) return fail(409, "This account has no recovery code: end-to-end encryption was never turned on.");
      const proved = await this.confirmed(b, "cancel", TOUCH_CANCEL);
      if (Array.isArray(proved)) return proved;
      proved.use();
      return null;
    }
    const by = this.confirmer(String(b.confirmation ?? ""), CANCEL_TAKES, TOUCH_CANCEL, null);
    if (Array.isArray(by)) return by;
    if (!this.cancellers().some((p) => p.id === by.id)) return fail(403, CANCEL_REMOVED);
    this.spend(b.confirmation);
    return null;
  }

  /** Removing a passkey from Account: refused while encryption is on (a revocation removes it then); while it is off,
   *  its credential and the grants to it go with it, and a new master key is due (credentialsDropped); and a change it
   *  made that waits ends, never taking effect (e2e_keys.passkey_removed). */
  private async removePasskey(id: string, b: Record<string, unknown>): Promise<Reply> {
    this.settleDue();                     // as passkeys.remove settles first: a turning-off whose time has come is in force
    if (this.on()) return fail(409, WORDS.revokesOnly);
    // in the server's order (passkeys.remove): an account change's confirmation, made for removing this passkey, then
    // remove_in's own refusals (none such; the last hardware key while high security is on), which leave it unused
    const by = this.confirmer(String(b.confirmation ?? ""), WORDS.needsConfirm, CONFIRM, `passkey-remove:${id}`);
    if (Array.isArray(by)) return by;
    const gone = this.passkeys.find((k) => k.id === id);
    if (!gone) return fail(404, "No such passkey.");
    if (this.high && gone.tier === "hardware" && this.passkeys.filter((k) => k.tier === "hardware").length <= 1) {
      return fail(409, WORDS.lastHardware);
    }
    this.spend(b.confirmation);
    this.passkeys = this.passkeys.filter((k) => k.id !== id);
    this.credentialsDropped([id]);
    if (this.pending?.madeBy === id) this.pending = null;
    return [200, { removed: id, withdrawn: 0, stopped: [], cancelled: [] }];
  }

  /** High security switched from Account: turning it on is refused while encryption is on (a revocation turns it on
   *  then); while it is off, every credential but the hardware keys' goes (credentialsDropped). */
  private async highSecurity(b: Record<string, unknown>): Promise<Reply> {
    // as main.high_security reads its body (HighSecurityIn: on, a bool, FastAPI's 422 for one not given or not one; its
    // confirmation's type is not modelled), then passkeys.set_high_security: turning it on settles first, so a
    // turning-off whose time has come is in force
    const turning = laxBool(b, "on");
    if (typeof turning !== "boolean") return [422, { detail: [turning] }];
    b = { ...b, on: turning };
    if (turning) this.settleDue();
    if (b.on === true && this.on()) return fail(409, WORDS.revokesOnly);
    // as passkeys.set_high_security: turning it on keeps a hardware key, and either way takes a hardware key's word
    if (b.on === true && !this.passkeys.some((p) => p.tier === "hardware")) return fail(409, WORDS.noHardware);
    if (b.on === true && this.passkeys.some((p) => p.judge_again)) return fail(409, WORDS.keyWaiting);
    // a hardware key's confirmation of an account change, made for turning it on or off (_use_confirmation with
    // NEEDS_HARDWARE_TO_TURN), left unused when refused
    const by = this.confirmer(String(b.confirmation ?? ""), WORDS.needsHardwareToTurn, CONFIRM,
      b.on === true ? "high-security-on" : "high-security-off", WORDS.needsHardwareToTurn);
    if (Array.isArray(by)) return by;
    this.spend(b.confirmation);
    this.high = b.on === true;
    if (this.high) this.credentialsDropped(this.passkeys.filter((p) => p.tier !== "hardware").map((p) => p.id));
    return [200, { high_security: this.high, withdrawn: 0, stopped: [], cancelled: [] }];
  }

  /** Turning encryption on, held as every change is (a day when it starts again: replace): the whole body for an account
   *  with no keys, or with replace; the content key alone under the current master key, signed by a passkey that
   *  unlocks it, while no new master key is due; or a new master key made as a revocation makes it. Checked as it is
   *  asked and again as its hold ends (_turn_on_checks): a passkey removed meanwhile makes a new master key due. */
  private async turnOn(b: Record<string, unknown>): Promise<Reply> {
    this.settleDue();
    const st = await this.statement(b.statement, ["e2e-key"]);
    if (Array.isArray(st)) return st;
    // its shape, as the server reads it (_turn_on_plan): a chain, a new master key; nothing but the content key (and no
    // replace), the content key alone; else the whole body
    const has = ["mk_id", "credential", "recovery", "grants", "roster", "content_keys"].some((k) => b[k] !== undefined && b[k] !== null);
    const shape = b.chain !== undefined && b.chain !== null ? "rekey" : !b.replace && !has ? "again" : "whole";
    // the request's upgrade, as _flag reads it: with it, the fold (a new master key made as the upgrade makes one)
    if (b.upgrade !== undefined && b.upgrade !== null && typeof b.upgrade !== "boolean") return fail(422, "The request's upgrade is true or false.");
    if (b.upgrade === true) return this.fold(b, st);
    // the keys it makes: a whole body's post-quantum when it says kem (a first turn-on or starting again; a
    // post-quantum account keeps it so), else the account's as they are
    const pq = shape === "whole" ? b.kem === "x-wing" || this.pq : this.pq;
    const refused = this.kemChecks(b, st, pq, shape === "whole");
    if (refused) return refused;
    const content = this.contentKey(b.content_key, st.payload, pq);
    if (Array.isArray(content)) return content;
    let whole: (() => void) | null = null;
    const cred = b.credential as { passkey_id: string; salt: string; public_key: string; wrapped: string; anchor?: string } | undefined;
    const passkey = this.passkeys.find((p) => p.id === cred?.passkey_id);
    if (shape === "rekey") {
      if (b.replace || b.credential || b.recovery) return fail(422, REKEY_OWN);
      // the new master key's parts, as _turn_on_plan reads them (_revocation_body), before anything is asked of the
      // account's keys (_turn_on_checks)
      const forms = this.masterForms(b, content, pq);
      if (forms) return forms;
    } else if (shape === "whole") {
      if (!HEX32.test(String(b.mk_id)) || content.mk_id !== b.mk_id) return fail(422, "The content key is wrapped under the master key being turned on.");
      if (!cred || !passkey || !(await credentialOk(cred, pq, b.mk_id))) return fail(422, "The credential is wrong.");
      const grants = b.grants as { to: string; sealed: string }[];
      if (!Array.isArray(grants) || JSON.stringify(grants.map((g) => g.to).sort()) !== JSON.stringify([passkey.credential_id, "recovery"].sort())
          || !grants.every((g) => sealedOk(g.sealed, pq ? 2 : 1))) return fail(422, "Turning encryption on grants the master key to the credential in use and to the recovery key.");
      const recovery = b.recovery as E2ERecovery;
      if (!(await recoveryOk(recovery, pq)) || !sentBytes(b.roster, undefined, WRAP_MIN, ROSTER_MAX)) return fail(422, "The recovery key or the roster is wrong.");
      whole = () => {
        this.credentials = [{ passkey_id: passkey.id, credential_id: passkey.credential_id, salt: cred.salt, public_key: cred.public_key, wrapped: cred.wrapped,
          ...(pq ? { anchor: cred.anchor } : {}), created_at: this.now }];
        if (pq) this.pq = true;
        this.recovery = { public_key: recovery.public_key, anchor: recovery.anchor, created_at: this.now };
        this.grants = grants.map((g) => ({ mk_id: String(b.mk_id), to: g.to, sealed: g.sealed, created_at: this.now }));
        this.chain = [];
        this.roster = { mk_id: String(b.mk_id), sealed: String(b.roster) };
        this.contentKeys = [];
        this.rekey = false;
      };
    }
    const after = this.statements.at(-1)?.payload ?? null;
    // what it makes, after the checks made as it is asked and again as its hold ends, or a refusal
    const checked = (): Reply | (() => void) => {
      if (this.on()) return fail(409, "End-to-end encryption is on already.");
      const next = this.stillNext(st, after);
      if (next) return next;
      if ((this.statements.length || b.replace) && st.payload.retires !== "all") return fail(422, "Turning encryption on again retires every earlier key at once (retires: all).");
      const mk = this.currentMk();
      if (shape === "whole") {
        if (mk && !b.replace) return fail(409, "This account keeps keys from when encryption was on.");
        if (!passkey || !this.passkeys.includes(passkey)) return fail(422, "That is not one of this account's passkeys.");
        if (this.high && passkey.tier !== "hardware") return fail(409, "High security is on: only a hardware key unlocks end-to-end encryption.");
        if (passkey.id !== st.signer.id) return fail(422, "The credential enrolled as encryption is turned on is the passkey that signed its statement.");
        return whole!;
      }
      if (!mk) return fail(409, "This account keeps no keys from before: turning encryption on sends the whole body.");
      if (shape === "again") {
        if (this.rekeyDue()) return fail(409, WORDS.rekeyDue);
        if (content.mk_id !== mk) return fail(409, WORDS.stale);
        if (!this.credentials.some((c) => c.passkey_id === st.signer.id)) return fail(409, "Turning encryption on again is signed by a passkey that unlocks it.");
        return () => {};
      }
      const master = this.newMaster(b, [], false);
      if (Array.isArray(master)) return master;
      if (!this.credentials.some((c) => c.passkey_id === st.signer.id && this.staying().has(c.credential_id))) return fail(409, "Turning encryption on again is signed by a passkey that unlocks it.");
      return master;
    };
    const first = checked();
    if (Array.isArray(first)) return first;
    // its body as the server keeps it (_turn_on_plan), with its statement and whether it seals in place
    const kept = shape === "rekey" ? { ...keptNewMaster({ ...b, remove: [], high_security: false, recovery: null }), shape }
      : shape === "again" ? { shape, content_key: content }
      : { shape, replace: !!b.replace, content_key: content, mk_id: b.mk_id, credential: keptCredential(passkey!, cred!), recovery: b.recovery,
        grants: byKey(b.grants, "to", "sealed"), roster: b.roster };
    return this.hold("turn-on", () => {
      // as _apply_turn_on: first, a whole body without kem placed while this server took one ends failed once it does
      // not (_whole_era); then its checks again (_turn_on_checks), then its era (_era_still: a whole body that says
      // kem makes the account post-quantum)
      if (shape === "whole" && !pq && !this.classicalWholeTurnOn) return fail(400, OLD_PAGE);
      const made = checked();
      if (Array.isArray(made)) return made;
      const era = this.eraStill(pq, shape === "whole" && pq);
      if (era) return era;
      made();
      this.contentKeys.push({ ...content, created_at: this.now });
      this.keep(st);
      if (b.seal !== false) this.sealed = { events: 3, left: 1, rows: 2, talks: 1, at: this.now };
    }, { madeBy: st.signer.id, long: !!b.replace, removesCode: !!b.replace, use: [st.use],
      body: { ...kept, statement: keptStatement(st, b), seal: b.seal !== false } });
  }

  /** The fold: a classical account's turn-on again with a new master key made as the
   *  upgrade makes one (`upgrade: true` with a chain), after which the account is post-quantum. Read and checked in the
   *  server's order (e2e_keys.turn_on): a chain, no credential, no start again; the account classical; kem, which it
   *  must say; its new master key's parts as a post-quantum revocation's, and the upgrade's own (a new recovery key,
   *  every passkey's new key, kept); the code's proof for turning on, which makes it code-backed and holds it the code's
   *  day, else the long hold; its place, as rule (v) allows any change; a turn-on's checks with a new master key
   *  (encryption off, the statement the next and retiring every key, the master key current, its signer staying), and
   *  the upgrade's in the fold's words (the account classical, a new key under a fresh salt for every passkey that
   *  stays: it removes none); a code kept proved twice, for turning on; each touch but the signer's, in the fold's
   *  words. As its hold ends: the same checks again, then its era (the server's ML-KEM), then the new master key, each
   *  passkey's new key, the recovery key and the account post-quantum written. */
  private async fold(b: Record<string, unknown>, st: Statement): Promise<Reply> {
    if (b.chain === undefined || b.chain === null) return fail(422, FOLD_REKEY);
    if (b.replace || (b.credential !== undefined && b.credential !== null)) return fail(422, REKEY_OWN);
    if (this.pq) return fail(409, NOT_CLASSICAL);
    if (b.kem !== undefined && b.kem !== null && b.kem !== "x-wing") return fail(422, "A write's kem is x-wing, or none.");
    if (b.kem === "x-wing" && !this.mlkem) return fail(503, NO_MLKEM_WORDS);
    if (b.kem !== "x-wing") return fail(422, UPGRADE_KEM);
    if (st.payload.v !== 3) return fail(422, `The content key is ${C.HYBRID_KEY_LEN} bytes, in base64url without padding.`);
    const content = this.contentKey(b.content_key, st.payload, true);
    if (Array.isArray(content)) return content;
    const forms = this.masterForms(b, content, true);
    if (forms) return forms;
    const recovery = b.recovery as E2ERecovery | null | undefined;
    if (recovery !== undefined && recovery !== null && !(await recoveryOk(recovery, true))) {
      return fail(422, "The new recovery key is {public_key, anchor}: a post-quantum key, and its anchor.");
    }
    if (!recovery) return fail(422, "The upgrade makes the recovery key post-quantum too: it carries recovery, from the code kept or a new one.");
    const creds = b.credentials as { passkey_id: string; salt: string; public_key: string; wrapped: string; anchor: string; confirmation?: string }[];
    if (!Array.isArray(creds) || !creds.length) {
      return fail(422, "The upgrade's credentials are a list: each enrolled passkey that stays, with its post-quantum key made at its touch.");
    }
    if (new Set(creds.map((c) => c.passkey_id)).size !== creds.length || creds.some((c) => typeof c.passkey_id !== "string")) {
      return fail(422, "Each credential of the upgrade names one passkey, once.");
    }
    for (const c of creds) {
      if (!(await credentialOk(c, true, b.mk_id))) return fail(422, "A post-quantum credential's anchor is the master key the change names.");
      if (c.confirmation !== undefined && c.confirmation !== null && typeof c.confirmation !== "string") {
        return fail(422, "A credential's confirmation is the one POST /api/account/confirm gave.");
      }
    }
    if (b.kept !== undefined && b.kept !== null && typeof b.kept !== "boolean") return fail(422, "The request's kept is true or false.");
    const kept = b.kept === true;
    // _signed_or_proof: a recovery proof given beside it, a classical one against the stored key, for turning on
    const word = b.recovery_proof ? await this.confirmed(b, "turn-on", CONFIRM) : null;
    if (Array.isArray(word)) return word;
    const byCode = word?.by === "recovery";
    // _in_place_of, before the turn-on's checks as the server asks it
    if (this.pending && !(byCode && this.recovery) && !this.cancellers().some((k) => k.id === st.signer.id)) return fail(409, NO_PLACE);
    const after = this.statements.at(-1)?.payload ?? null;
    let keptAgainst: string | null = null;
    // _turn_on_checks with a new master key, and _upgrade_checks in the fold's words: the same as it is asked and as
    // its hold ends
    const checks = (): Reply | (() => void) => {
      if (this.on()) return fail(409, "End-to-end encryption is on already.");
      const next = this.stillNext(st, after);
      if (next) return next;
      if (st.payload.retires !== "all") return fail(422, "Turning encryption on again retires every earlier key at once (retires: all).");
      if (!this.currentMk()) return fail(409, "This account keeps no keys from before: turning encryption on sends the whole body.");
      const master = this.newMaster(b, [], false);
      if (Array.isArray(master)) return master;
      const stay = this.staying();
      if (!this.credentials.some((c) => c.passkey_id === st.signer.id && stay.has(c.credential_id))) return fail(409, "Turning encryption on again is signed by a passkey that unlocks it.");
      if (this.pq) return fail(409, NOT_CLASSICAL);
      const staying = this.credentials.filter((c) => stay.has(c.credential_id)).map((c) => c.passkey_id);
      if (JSON.stringify(creds.map((c) => c.passkey_id).sort()) !== JSON.stringify([...staying].sort())) return fail(422, FOLD_EVERY);
      if (creds.some((c) => this.credentials.find((x) => x.passkey_id === c.passkey_id)!.salt === c.salt)) return fail(422, SALT_SAME);
      if (keptAgainst !== null && keptAgainst !== this.recovery?.public_key) return fail(409, WORDS.codeChanged);
      return master;
    };
    const first = checks();
    if (Array.isArray(first)) return first;
    // _upgrade_proofs, for turning on: a code kept proved twice, the hybrid proof to the body's new recovery key
    const uses: (() => void)[] = word ? [word.use] : [];
    const newProof = b.new_recovery_proof as { challenge_id?: string; proof?: string } | undefined | null;
    if (!kept && newProof !== undefined && newProof !== null) {
      return fail(422, "A new_recovery_proof proves the code kept: an upgrade with a new code (kept false) carries none.");
    }
    if (kept) {
      if (!byCode || !newProof) return fail(403, KEPT_PROOFS);
      const held = this.proofs.get(String(newProof.challenge_id));
      if (!held || held.purpose !== "turn-on" || !held.ss || held.expires <= this.now) return fail(403, PROOF_STALE);
      if (held.digest !== undefined || !(await this.proofHolds(held, String(newProof.proof), "turn-on", recovery.public_key))) return fail(403, PROOF_WRONG);
      uses.push(() => { this.proofs.delete(String(newProof.challenge_id)); });
      keptAgainst = this.recovery?.public_key ?? null;
    }
    // _upgrade_touches, in the fold's words: each passkey but the statement's signer, with a confirmation its own
    // passkey made for the upgrade's touch and for its subject
    const statement = C.hex(await C.sha256(C.utf8(st.text)));
    for (const c of creds) {
      if (c.passkey_id === st.signer.id) continue;
      const row = this.credentials.find((x) => x.passkey_id === c.passkey_id)!;
      const bound = { credential_id: row.credential_id, mk_id: b.mk_id, public_key: c.public_key, salt: c.salt, statement };
      const subject = `e2e-upgrade:${C.hex((await C.sha256(C.utf8(canonicalJson(bound)))).subarray(0, 16))}`;
      const own = this.confirmer(String(c.confirmation ?? ""), FOLD_OWN, "e2e-upgrade-touch", subject);
      if (Array.isArray(own)) return own;
      if (own.id !== c.passkey_id) return fail(403, FOLD_OWN);
      uses.push(() => { this.spend(c.confirmation); });
    }
    return this.hold("turn-on", () => {
      // as _apply_turn_on the fold: its checks again (_turn_on_checks, the upgrade's among them), then its era
      // (_era_still: this server's ML-KEM; the fold makes the account post-quantum), then what it writes
      const made = checks();
      if (Array.isArray(made)) return made;
      const era = this.eraStill(true, true);
      if (era) return era;
      made();
      for (const c of creds) Object.assign(this.credentials.find((x) => x.passkey_id === c.passkey_id)!,
        { salt: c.salt, public_key: c.public_key, wrapped: c.wrapped, anchor: c.anchor });
      this.recovery = { public_key: recovery.public_key, anchor: recovery.anchor, created_at: this.now };
      this.pq = true;
      this.contentKeys.push({ ...content, created_at: this.now });
      this.keep(st);
      if (b.seal !== false) this.sealed = { events: 3, left: 1, rows: 2, talks: 1, at: this.now };
    }, { recovery: byCode, madeBy: st.signer.id, long: true, removesCode: !kept, use: [st.use, ...uses],
      body: { ...keptNewMaster({ ...b, remove: [], high_security: false }), shape: "rekey", upgrade: true, kept, kem: true,
        credentials: Object.fromEntries(creds.map((c) => [c.passkey_id, { salt: c.salt, public_key: c.public_key, wrapped: c.wrapped, anchor: c.anchor }])),
        statement: keptStatement(st, b), seal: b.seal !== false } });
  }

  /** A new master key's parts read for their forms, as e2e_keys._revocation_body reads them after its content key and
   *  before anything is asked of the account's keys, in its words: its id, the one the new content key is wrapped
   *  under; every content key's wrap again (on a post-quantum account each one 32-byte value in one 60-byte wrap: its
   *  X-Wing keys' seeds, and a retired classical key's scalar); the chain's wrap; each grant an envelope
   *  of the account's form (form 2 to a post-quantum account's X-Wing keys and form 1 to a classical one's: a grant's
   *  form is its recipient's); the roster's wrap. A refusal, or null. */
  private masterForms(b: Record<string, unknown>, content: E2EContentKey, pq = this.pq): Reply | null {
    const mk = String(b.mk_id);
    if (!HEX32.test(mk) || content.mk_id !== mk) return fail(422, "The new content key is wrapped under the new master key.");
    const rewrapped = b.content_keys as { key_id: string; wrapped: string }[] | undefined;
    if (Array.isArray(rewrapped) && rewrapped.some((k) => !sentBytes(k?.wrapped))) return fail(422, `The content key's wrap is ${WRAP_MIN} to ${WRAP_MAX} bytes, in base64url without padding.`);
    if (pq && Array.isArray(rewrapped) && rewrapped.some((k) => !sentBytes(k.wrapped, C.WRAP_LEN))) return fail(422, `The content key's wrap is ${C.WRAP_LEN} bytes, in base64url without padding.`);
    const chain = b.chain as { wrapped?: string } | undefined;
    if (!sentBytes(chain?.wrapped)) return fail(422, `The chain's wrap is ${WRAP_MIN} to ${WRAP_MAX} bytes, in base64url without padding.`);
    const grants = b.grants as { sealed?: string }[] | undefined;
    if (Array.isArray(grants) && !grants.every((g) => sealedOk(g?.sealed, pq ? 2 : 1))) return fail(422, `The grant is sealed as an envelope is (e2e1. and base64url), at most ${GRANT_MAX} bytes inside.`);
    if (!sentBytes(b.roster, undefined, WRAP_MIN, ROSTER_MAX)) return fail(422, `The roster is ${WRAP_MIN} to ${ROSTER_MAX} bytes, in base64url without padding.`);
    return null;
  }

  /** The checks of a new master key made from the current one, a revocation's, the upgrade's or a turn-on's while one is
   *  due, against the account's keys as they are, as the server makes them (_revocation), its parts' forms read before
   *  (masterForms, where its request is read for its forms): a refusal, or what makes it take effect (the content key
   *  and the statement apart). Made from the current master key; a new master key indeed, none the chain names; a
   *  hardware key kept while high security is or will be on, and, turning it on, no passkey waiting to be judged; the
   *  grants to every credential that stays and the recovery key; every content key wrapped again, but those it leaves
   *  as they are (abandon), each once. */
  private newMaster(b: Record<string, unknown>, remove: string[], highSecurity: boolean): Reply | (() => void) {
    const mk = String(b.mk_id);
    const chain = b.chain as { previous: string; wrapped: string };
    if (chain.previous !== this.currentMk()) return fail(409, WORDS.stale);
    if (mk === chain.previous || this.chain.some((l) => l.mk_id === mk || l.previous === mk)) return fail(422, WORDS.newMaster);
    if (remove.some((id) => !this.passkeys.some((p) => p.id === id))) return fail(404, "No such passkey.");
    const kept = this.passkeys.filter((p) => !remove.includes(p.id));
    const high = highSecurity || this.high;
    if (high && !kept.some((p) => p.tier === "hardware")) return fail(409, highSecurity ? WORDS.noHardware : WORDS.lastHardware);
    if (highSecurity && kept.some((p) => p.judge_again)) return fail(409, WORDS.keyWaiting);
    const stay = this.staying(remove, high);
    const grants = b.grants as { to: string; sealed: string }[];
    if (!Array.isArray(grants) || JSON.stringify(grants.map((g) => g.to).sort()) !== JSON.stringify([...stay, "recovery"].sort())) {
      return fail(422, "A new master key is granted to every credential that stays enrolled and to the recovery key, and to nothing else.");
    }
    const rewrapped = b.content_keys as { key_id: string; wrapped: string }[];
    const abandon = (b.abandon ?? []) as string[];
    const all = Array.isArray(rewrapped) && Array.isArray(abandon) ? [...rewrapped.map((k) => k.key_id), ...abandon] : null;
    if (!all || new Set(all).size !== all.length || JSON.stringify([...all].sort()) !== JSON.stringify(this.contentKeys.map((k) => k.key_id).sort())) {
      return fail(422, "A new master key wraps every content key of the account again, but those it leaves as they are (abandon), each once.");
    }
    return () => {
      this.passkeys = this.passkeys.filter((p) => !remove.includes(p.id));
      if (highSecurity) this.high = true;
      this.credentials = this.credentials.filter((c) => stay.has(c.credential_id));
      this.grants = grants.map((g) => ({ mk_id: mk, to: g.to, sealed: g.sealed, created_at: this.now }));
      this.chain.push({ mk_id: mk, previous: chain.previous, wrapped: chain.wrapped, created_at: this.now });
      this.roster = { mk_id: mk, sealed: String(b.roster) };
      for (const k of this.contentKeys) {
        const again = rewrapped.find((r) => r.key_id === k.key_id);
        if (!again) continue;                 // abandoned: left as it is
        k.mk_id = mk;
        k.wrapped = again.wrapped;
      }
      this.rekey = false;
    };
  }

  private async rotate(b: Record<string, unknown>): Promise<Reply> {
    this.settleDue();
    const st = await this.statement(b.statement, ["e2e-key"]);
    if (Array.isArray(st)) return st;
    if (st.payload.retires !== "grace") return fail(422, "A routine rotation retires the earlier keys after a grace.");
    // as e2e_keys.rotate: its era (_era) and its content key's forms (_content_key) before whether encryption is on
    // (_ready)
    const kem = this.kemChecks(b, st);
    if (kem) return kem;
    const content = this.contentKey(b.content_key, st.payload);
    if (Array.isArray(content)) return content;
    if (!this.on()) return fail(409, NOT_ON);
    if (content.mk_id !== this.currentMk()) return fail(409, WORDS.stale);
    // as _rotation_bound: at most one in rotateEvery's seconds, after the last that took effect
    const every = this.rotateEvery;
    const last = Math.max(-Infinity, ...this.statements.filter((s) => s.retires === "grace").map((s) => s.created_at));
    if (every && this.now - last < every) {
      const span = every >= 3600 && every % 3600 === 0 ? `${every / 3600} hours` : `${every} seconds`;
      return fail(429, `A routine rotation is made at most once in ${span}, and the last took effect less than that ago. To replace a key that must go now, replace the keys (a revocation), which has no such bound.`);
    }
    const word = b.recovery_proof ? await this.confirmed(b, "rotate", CONFIRM) : null;
    if (Array.isArray(word)) return word;
    const byCode = word?.by === "recovery";
    if (!byCode && !this.staysEnrolled(st.signer.id)) return fail(403, NOT_STAYING);
    const after = this.statements.at(-1)?.payload ?? null;
    return this.hold("rotate", () => {
      // as _apply_rotate: the master key it is wrapped under still current, its era, its signer asked again, the
      // statement still the next
      if (content.mk_id !== this.currentMk()) return fail(409, WORDS.stale);
      const era = this.eraStill(b.kem === "x-wing");
      if (era) return era;
      const still = this.signerStill(st.signer, byCode);
      if (still) return still;
      const next = this.stillNext(st, after);
      if (next) return next;
      this.contentKeys.push({ ...content, created_at: this.now });
      this.keep(st);
    }, { recovery: byCode, madeBy: st.signer.id, use: [st.use, ...(word ? [word.use] : [])],
      body: { statement: keptStatement(st, b), content_key: content } });
  }

  private async revoke(b: Record<string, unknown>): Promise<Reply> {
    this.settleDue();
    const st = await this.statement(b.statement, ["e2e-key"]);
    if (Array.isArray(st)) return st;
    if (st.payload.retires !== "all") return fail(422, "A revocation retires every earlier key at once (retires: all).");
    if (b.upgrade === true) return this.upgrade(b, st);
    const kem = this.kemChecks(b, st);
    if (kem) return kem;
    // as e2e_keys.revoke: the body read for its forms (_revocation_body: its content key, the new master key's parts,
    // a new recovery code's key), then the hardware key's signature where it turns high security on, then whether
    // encryption is on (_ready), then the checks against the account's keys (_revocation, newMaster)
    const content = this.contentKey(b.content_key, st.payload);
    if (Array.isArray(content)) return content;
    const forms = this.masterForms(b, content);
    if (forms) return forms;
    const remove = (b.remove ?? []) as string[];
    // a new recovery code's key, as _recovery reads it: one that can be sealed to, and its anchor's wrap
    if (b.recovery !== undefined && b.recovery !== null && !(await recoveryOk(b.recovery as E2ERecovery, this.pq))) {
      return fail(422, "The new recovery key is {public_key, anchor}: a key that can be sealed to, and its anchor.");
    }
    // turning high security on takes a hardware key's signature, while it is off too (passkeys.NEEDS_HARDWARE_TO_TURN)
    if (b.high_security === true && st.signer.tier !== "hardware") return fail(403, WORDS.needsHardwareToTurn);
    if (!this.on()) return fail(409, NOT_ON);
    const master = this.newMaster(b, remove, b.high_security === true);
    if (Array.isArray(master)) return master;
    const word = b.recovery_proof ? await this.confirmed(b, "revoke", CONFIRM) : null;
    if (Array.isArray(word)) return word;
    const byCode = word?.by === "recovery";
    const stay = this.staying(remove, b.high_security === true || this.high);
    // signed by a passkey that unlocks the keys and stays after it, unless the code's proof comes with it
    if (!byCode && !this.credentials.some((c) => c.passkey_id === st.signer.id && stay.has(c.credential_id))) {
      return fail(403, NOT_STAYING);
    }
    // a day when it replaces the code, removes no passkey (R: what it names, and what high security leaves out where it
    // really turns it on), or leaves no credential but the signer's where there were others
    const others = this.credentials.filter((c) => c.passkey_id !== st.signer.id);
    const long = !!b.recovery || !this.removedBy(remove, b.high_security === true).size
      || (others.length > 0 && !others.some((c) => stay.has(c.credential_id)));
    const after = this.statements.at(-1)?.payload ?? null;
    return this.hold("revoke", () => {
      // as _apply_revoke: its era, the new master key checked again (_revocation) from the keys as they are then, its
      // signer asked again against the credentials it leaves
      const era = this.eraStill(b.kem === "x-wing");
      if (era) return era;
      const made = this.newMaster(b, remove, b.high_security === true);
      if (Array.isArray(made)) return made;
      const still = this.signerStill(st.signer, byCode, this.staying(remove, b.high_security === true || this.high));
      if (still) return still;
      const next = this.stillNext(st, after);
      if (next) return next;
      this.keep(st);
      made();
      this.contentKeys.push({ ...content, created_at: this.now });
      if (b.recovery) this.recovery = { ...(b.recovery as E2ERecovery), created_at: this.now };
    }, { recovery: byCode, madeBy: st.signer.id, long, removesCode: !!b.recovery, use: [st.use, ...(word ? [word.use] : [])],
      removes: remove, turnsHigh: b.high_security === true, body: { ...keptNewMaster(b), statement: keptStatement(st, b) } });
  }

  /** The upgrade to post-quantum keys, a revocation with `upgrade: true`, checked as
   *  the server was asked to check it: the account classical and its encryption on; kem and a version-3 statement; every
   *  passkey that unlocks encryption either staying, in `credentials` with a fresh salt, an X-Wing key, a 60-byte wrap,
   *  its anchor the new master key and a confirmation its own passkey made for this touch (e2e-upgrade-touch) and for
   *  the subject bound to it (upgradeSubject's, written here from its words), or removed; the new master key granted in
   *  form 2 to exactly those and the new recovery key, every content key wrapped again in 60 bytes; a new recovery key;
   *  with `kept`, both proofs (the classical one against the stored key, the hybrid one to the new key), else signed by
   *  a passkey that stays. Held a day (the code's hold with the code's proof); as its hold ends checked again, then the
   *  account post-quantum. */
  private async upgrade(b: Record<string, unknown>, st: Statement): Promise<Reply> {
    // as e2e_keys.revoke reads an upgrade, in its order: the account classical; its era (kem x-wing, which it must
    // say, and this server's ML-KEM); its forms as a post-quantum revocation's; its own parts (_upgrade_plan)
    if (this.pq) return fail(409, NOT_CLASSICAL);
    if (b.kem !== undefined && b.kem !== null && b.kem !== "x-wing") return fail(422, "A write's kem is x-wing, or none.");
    if (b.kem === "x-wing" && !this.mlkem) return fail(503, NO_MLKEM_WORDS);
    if (b.kem !== "x-wing") return fail(422, "An upgrade to post-quantum keys says so: kem x-wing.");
    if (st.payload.v !== 3) return fail(422, `The content key is ${C.HYBRID_KEY_LEN} bytes, in base64url without padding.`);
    const content = this.contentKey(b.content_key, st.payload, true);
    if (Array.isArray(content)) return content;
    // the new master key's parts, as _revocation_body reads them, before its own parts and the hardware key's word
    const forms = this.masterForms(b, content, true);
    if (forms) return forms;
    const recovery = b.recovery as E2ERecovery | null | undefined;
    if (recovery !== undefined && recovery !== null && !(await recoveryOk(recovery, true))) {
      return fail(422, "The new recovery key is {public_key, anchor}: a post-quantum key, and its anchor.");
    }
    if (!recovery) return fail(422, "The upgrade makes the recovery key post-quantum too: it carries recovery, from the code kept or a new one.");
    const creds = b.credentials as { passkey_id: string; salt: string; public_key: string; wrapped: string; anchor: string; confirmation?: string }[];
    if (!Array.isArray(creds) || !creds.length) {
      return fail(422, "The upgrade's credentials are a list: each enrolled passkey that stays, with its post-quantum key made at its touch.");
    }
    if (new Set(creds.map((c) => c.passkey_id)).size !== creds.length || creds.some((c) => typeof c.passkey_id !== "string")) {
      return fail(422, "Each credential of the upgrade names one passkey, once.");
    }
    for (const c of creds) {
      if (!(await credentialOk(c, true, b.mk_id))) return fail(422, "A post-quantum credential's anchor is the master key the change names.");
      if (c.confirmation !== undefined && c.confirmation !== null && typeof c.confirmation !== "string") {
        return fail(422, "A credential's confirmation is the one POST /api/account/confirm gave.");
      }
    }
    if (b.kept !== undefined && b.kept !== null && typeof b.kept !== "boolean") return fail(422, "The request's kept is true or false.");
    const kept = b.kept === true;
    if (b.high_security === true && st.signer.tier !== "hardware") return fail(403, WORDS.needsHardwareToTurn);
    if (!this.on()) return fail(409, NOT_ON);
    const remove = (b.remove ?? []) as string[];
    const master = this.newMaster(b, remove, b.high_security === true);
    if (Array.isArray(master)) return master;
    // _upgrade_checks: its credentials exactly those that stay (each enrolled passkey not removed), each under a fresh
    // salt
    const stay = this.staying(remove, b.high_security === true || this.high);
    const staying = this.credentials.filter((c) => stay.has(c.credential_id)).map((c) => c.passkey_id);
    if (JSON.stringify(creds.map((c) => c.passkey_id).sort()) !== JSON.stringify([...staying].sort())) {
      return fail(422, "The upgrade gives each enrolled passkey that stays a post-quantum key made at its own touch, and no other: a passkey not touched is removed by it (remove).");
    }
    if (creds.some((c) => this.credentials.find((x) => x.passkey_id === c.passkey_id)!.salt === c.salt)) return fail(422, SALT_SAME);
    // _signed_or_proof: a recovery proof given beside it, a classical one against the stored key, makes it the code's
    const word = b.recovery_proof ? await this.confirmed(b, "revoke", CONFIRM) : null;
    if (Array.isArray(word)) return word;
    const byCode = word?.by === "recovery";
    const uses: (() => void)[] = word ? [word.use] : [];
    // _upgrade_proofs: keeping the code proves it twice, the hybrid proof to the body's new recovery key, made for it
    const newProof = b.new_recovery_proof as { challenge_id?: string; proof?: string } | undefined | null;
    if (!kept && newProof !== undefined && newProof !== null) {
      return fail(422, "A new_recovery_proof proves the code kept: an upgrade with a new code (kept false) carries none.");
    }
    if (kept) {
      if (!byCode || !newProof) return fail(403, KEPT_PROOFS);
      const held = this.proofs.get(String(newProof.challenge_id));
      if (!held || held.purpose !== "revoke" || !held.ss || held.expires <= this.now) return fail(403, PROOF_STALE);
      if (held.digest !== undefined || !(await this.proofHolds(held, String(newProof.proof), "revoke", recovery.public_key))) return fail(403, PROOF_WRONG);
      uses.push(() => { this.proofs.delete(String(newProof.challenge_id)); });
    }
    // _upgrade_touches: each passkey that stays but the statement's signer, whose touch is its signature, with a
    // confirmation its own passkey made for the upgrade's touch and for its subject (upgrade_touch_subject's, written
    // here from its words)
    const statement = C.hex(await C.sha256(C.utf8(st.text)));
    for (const c of creds) {
      if (c.passkey_id === st.signer.id) continue;
      const row = this.credentials.find((x) => x.passkey_id === c.passkey_id)!;
      const bound = { credential_id: row.credential_id, mk_id: b.mk_id, public_key: c.public_key, salt: c.salt, statement };
      const subject = `e2e-upgrade:${C.hex((await C.sha256(C.utf8(canonicalJson(bound)))).subarray(0, 16))}`;
      const own = this.confirmer(String(c.confirmation ?? ""), UPGRADE_OWN, "e2e-upgrade-touch", subject);
      if (Array.isArray(own)) return own;
      if (own.id !== c.passkey_id) return fail(403, UPGRADE_OWN);
      uses.push(() => { this.spend(c.confirmation); });
    }
    if (!byCode && !staying.includes(st.signer.id)) return fail(403, NOT_STAYING);
    const after = this.statements.at(-1)?.payload ?? null;
    return this.hold("revoke", () => {
      // as _apply_revoke an upgrade: its era first (_era_still: it makes the account post-quantum), then the account
      // still classical
      const era = this.eraStill(true, true);
      if (era) return era;
      if (this.pq) return fail(409, NOT_CLASSICAL);
      const made = this.newMaster(b, remove, b.high_security === true);
      if (Array.isArray(made)) return made;
      const still = this.signerStill(st.signer, byCode, this.staying(remove, b.high_security === true || this.high));
      if (still) return still;
      const next = this.stillNext(st, after);
      if (next) return next;
      this.keep(st);
      made();
      for (const c of creds) Object.assign(this.credentials.find((x) => x.passkey_id === c.passkey_id)!,
        { salt: c.salt, public_key: c.public_key, wrapped: c.wrapped, anchor: c.anchor });
      this.contentKeys.push({ ...content, created_at: this.now });
      this.recovery = { public_key: recovery.public_key, anchor: recovery.anchor, created_at: this.now };
      this.pq = true;
    }, { recovery: byCode, madeBy: st.signer.id, long: true, removesCode: !kept, use: [st.use, ...uses], removes: remove,
      turnsHigh: b.high_security === true,
      body: { ...keptNewMaster(b), upgrade: true, kept, kem: true, statement: keptStatement(st, b),
        credentials: Object.fromEntries(creds.map((c) => [c.passkey_id, { salt: c.salt, public_key: c.public_key, wrapped: c.wrapped, anchor: c.anchor }])) } });
  }

  /** As e2e_keys._reseal_target: the newest key's key_id, what everything is sealed again to; 409 for a classical
   *  account (RESEAL_NOT_PQ), one whose encryption is off (RESEAL_OFF), or one whose newest key is not post-quantum. */
  private resealTarget(): string | Reply {
    if (!this.pq) return fail(409, RESEAL_NOT_PQ);
    const top = this.statements.at(-1);
    if (!top || top.purpose !== "e2e-key") return fail(409, RESEAL_OFF);
    if (C.unb64u(top.public_key)?.length !== C.HYBRID_KEY_LEN) return fail(409, RESEAL_NOT_PQ);
    return String(top.key_id);
  }

  /** The texts stored in form 1, by where, as e2e_keys.classical_texts counts them (classicalTexts): every one in form 1
   *  (storedForm1), a question that may yet be answered too, which re-sealing leaves. */
  private classicalCounts(): { events: number; rows: number; talks: number; total: number } | null {
    if (this.classicalTexts !== undefined) return this.classicalTexts;
    const count = (table: string) => this.stored.filter((x) => x.where.table === table && storedForm1(x.value)).length;
    const [events, rows, talks] = RESEAL_TABLES.map(count);
    return { events, rows, talks, total: events + rows + talks };
  }

  /** GET /api/ade/e2e/reseal, as e2e_keys.reseal_page serves it: {items, next}; 409 as resealTarget says. The texts
   *  still stored in form 1 (storedForm1), but a question its link sealed while it may yet be answered (`open`, whose
   *  answer is signed over its text as stored); events by id, then History rows, then conversations, each by id (a
   *  row's and a conversation's LINK_ID:SESSION_ID, their order as its link and session's); after the cursor `after`,
   *  the page before's next ("0:" and an event's id, "1:" or "2:" and a row's or a conversation's; 422 otherwise); at
   *  most min(resealPageSize, 100) items, and past the first item at most 512 KiB of values (a text's characters, a
   *  conversation's RFC 8785 bytes). next is the last item's cursor where a page is cut, null on the last. */
  private resealPage(path: string): Reply {
    const target = this.resealTarget();
    if (typeof target !== "string") return target;
    const after = new URLSearchParams(path.split("?")[1] ?? "").get("after") ?? "";
    const cut = after === "" ? null : RESEAL_AFTER.exec(after);
    if (after !== "" && !cut) return fail(422, "A re-seal page's after is the next the page before it gave.");
    const from: Place | null = !cut ? null : cut[1] !== undefined ? [0, Number(cut[1])] : [Number(cut[2]), cut[3]];
    const shown = this.stored.filter((x) => !x.open && storedForm1(x.value)).sort((a, b) => byPlace(placeOf(a), placeOf(b)))
      .filter((x) => !from || byPlace(placeOf(x), from) > 0);
    const most = Math.min(this.resealPageSize, 100);
    const page: typeof shown = [];
    let size = 0;
    let next: string | null = null;
    for (const x of shown) {
      const n = typeof x.value === "string" ? x.value.length : C.canonicalBytes(x.value).length;
      if (page.length && (page.length >= most || size + n > RESEAL_PAGE_BYTES)) {
        next = placeOf(page.at(-1)!).join(":");
        break;
      }
      page.push(x);
      size += n;
    }
    const items = page.map((x) => ({ where: x.where, value: x.value, ...(x.slot === undefined ? {} : { slot: x.slot }) }));
    return [200, { items: JSON.parse(JSON.stringify(items)), next }];
  }

  /** POST /api/ade/e2e/reseal, as e2e_keys.reseal takes it, in its order: the batch (1 to 200 items, each exactly
   *  {where, old_sha256, new}, where as GET gave it, each stored text once: 422); the account (resealTarget, 409); its
   *  era (kemChecks); the recovery code's proof made for the batch's digest (the hex sha256 of the RFC 8785 JSON of
   *  [[where, old_sha256, sha256 of new], ...], written here from its words); then each item against what is stored
   *  (404 RESEAL_GONE where it holds no form-1 text, or a question that may yet be answered; 409 RESEAL_CHANGED where it
   *  changed since it was read) and its new
   *  value against its form (422: a text in form 2 under the newest key; a conversation's key in form 2 of exactly
   *  1,197 bytes under it, the same messages with their fields as stored and each text in form 3 naming its key_id and
   *  holding at most what its message held (messageHeld: one in the clear, its words as they are sealed where that is
   *  more than the cap), and in all at most what its messages come to sealed as stored (talkSize) and its key's, or
   *  300,000 bytes where that is more). All or none; every swap logged by its hashes; {swapped}. Not held: the code
   *  alone makes it. */
  private async reseal(b: Record<string, unknown>): Promise<Reply> {
    const items = b.items as { where: unknown; old_sha256: unknown; new: unknown }[];
    if (!Array.isArray(items) || !items.length || items.length > 200) {
      return fail(422, "A re-seal batch is a list of 1 to 200 items, each {where, old_sha256, new}.");
    }
    const places = new Set<string>();
    for (const s of items) {
      if (!s || typeof s !== "object" || JSON.stringify(Object.keys(s).sort()) !== JSON.stringify(["new", "old_sha256", "where"])) {
        return fail(422, "A re-seal item is {where, old_sha256, new}.");
      }
      const w = s.where as Record<string, unknown> | null;
      const keys = w && typeof w === "object" ? Object.keys(w).sort().join(",") : "";
      const ok = w?.table === "events" ? keys === "field,id,table" && w.field === "text" && Number.isInteger(w.id) && (w.id as number) > 0
        : w?.table === "rows" ? keys === "field,id,table" && w.field === "sealed" && RESEAL_ID.test(String(w.id)) && typeof w.id === "string"
        : w?.table === "talks" ? keys === "id,table" && typeof w.id === "string" && RESEAL_ID.test(w.id) : false;
      if (!ok) return fail(422, RESEAL_WHERE);
      if (typeof s.old_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(s.old_sha256)) {
        return fail(422, "A re-seal item's old_sha256 is the sha256 of the value as read, 64 lowercase hex.");
      }
      const n = s.new as Record<string, unknown> | null;
      if (w!.table === "talks" && !(n && typeof n === "object" && JSON.stringify(Object.keys(n).sort()) === JSON.stringify(["key", "key_id", "messages"]))) {
        return fail(422, "A conversation sealed again is {messages, key, key_id}.");
      }
      if (w!.table !== "talks" && typeof s.new !== "string") return fail(422, "A text sealed again is an envelope's text.");
      const place = canonicalJson(w);
      if (places.has(place)) return fail(422, "A re-seal batch names each stored text once.");
      places.add(place);
    }
    const digest = async (v: unknown) => C.hex(await C.sha256(typeof v === "string" ? C.utf8(v) : C.utf8(canonicalJson(v))));
    const rows = await Promise.all(items.map(async (s) => [s.where, s.old_sha256, await digest(s.new)]));
    const bound = C.hex(await C.sha256(C.utf8(canonicalJson(rows))));
    const keyId = this.resealTarget();
    if (typeof keyId !== "string") return keyId;
    const kem = this.kemChecks(b);
    if (kem) return kem;
    if (!b.recovery_proof || typeof b.recovery_proof !== "object") return fail(422, "A recovery proof is {challenge_id, proof}.");
    const word = await this.confirmed(b, "reseal", CONFIRM, bound);
    if (Array.isArray(word)) return word;
    const swaps: (() => void)[] = [];
    const form1 = (v: unknown) => C.envelopeForm(v)?.form === 1;
    for (const [i, s] of items.entries()) {
      const at = this.stored.findIndex((x) => canonicalJson(x.where) === canonicalJson(s.where));
      const stored = at < 0 ? undefined : this.stored[at].value;
      const storedTalk = stored && typeof stored === "object" ? stored as { messages?: Record<string, unknown>[] } : null;
      if (stored === undefined || this.stored[at].open || (typeof stored === "string" ? !form1(stored) : !storedTalk?.messages?.some((m) => form1(m?.text)))) {
        return fail(404, RESEAL_GONE);
      }
      if (await digest(stored) !== s.old_sha256) return fail(409, RESEAL_CHANGED);
      if (typeof s.new === "string") {
        const events = (s.where as { table: unknown }).table === "events";
        const cap = events ? EVENT_PLAIN : this.stored[at].withheld ? HISTORY_WITHHELD_PLAIN : HISTORY_PLAIN;
        const bad = sealedAgain(s.new, held(stored, cap), 2, keyId, events ? "An event" : "A History row");
        if (bad) return bad;
      } else {
        const t = s.new as { messages: unknown; key: unknown; key_id: unknown };
        // e2e.read_talk_key: form 2 of exactly 1,197 bytes (its 32 inside), then under the newest key
        const keyWhy = envelopeWhy(t.key, C.TALK_KEY_LEN, 2)
          ?? (C.envelopeForm(t.key)!.body.length !== C.TALK_KEY_SEALED_LEN ? `A conversation's key is sealed in exactly ${C.TALK_KEY_SEALED_LEN} bytes.` : null);
        if (keyWhy) return fail(422, `A conversation's key is not sealed again in its form: ${keyWhy}`);
        if (C.hex(C.envelopeForm(t.key)!.body.subarray(1, 17)) !== keyId) return fail(422, "A conversation's key is not sealed again under the key it must be.");
        if (typeof t.key_id !== "string" || !HEX32.test(t.key_id)) return fail(422, "The conversation's key_id is 32 lowercase hex characters.");
        const before = storedTalk!.messages!;
        if (!Array.isArray(t.messages) || t.messages.length !== before.length) {
          return fail(422, "A conversation is sealed again whole: each of its messages, in their order.");
        }
        let total = C.TALK_KEY_SEALED_LEN;
        for (const [j, m] of (t.messages as Record<string, unknown>[]).entries()) {
          const was = before[j];
          if (!m || typeof m !== "object" || JSON.stringify(Object.keys(m).sort()) !== JSON.stringify(Object.keys(was).sort())
              || Object.keys(was).some((f) => f !== "text" && JSON.stringify(m[f]) !== JSON.stringify(was[f]))) {
            return fail(422, "A conversation sealed again keeps each message's place, whose it is and its times as they were.");
          }
          const bad = sealedAgain(m.text, messageHeld(was.text), 3, t.key_id, "A message");
          if (bad) return bad;
          total += C.envelopeForm(m.text)!.body.length;
        }
        // the larger of link.TALK_SEALED_TOTAL and what it holds as stored (_talk_size) and its key's (e2e_keys._reseal_swap)
        const most = Math.max(this.talkSealedTotal, talkSize(before) + C.TALK_KEY_SEALED_LEN);
        if (total > most) return fail(422, `A conversation's envelopes come to at most ${most} bytes: ${this.talkSealedTotal}, or what it holds now and its key's where that is more.`);
      }
      const newSha = String(rows[i][2]);
      swaps.push(() => {
        this.stored[at] = { ...this.stored[at], value: s.new };
        this.resealLog.push({ where: s.where, old_sha256: String(s.old_sha256), new_sha256: newSha });
      });
    }
    word.use();
    for (const swap of swaps) swap();
    return [200, { swapped: items.length }];
  }

  /** A passkey added to encryption, as e2e_keys.enrol takes it: confirmed by a passkey that unlocks the keys already
   *  (never the one added) or the code's proof, and its credential with a confirmation the passkey added made itself
   *  (ENROL_OWN), both used up as the change is placed. (The server also refuses as its hold ends one placed before
   *  that rule, without own_touch in its body, which this server never holds.) */
  private async enrol(b: Record<string, unknown>): Promise<Reply> {
    this.settleDue();
    // as e2e_keys.enrol: encryption on, its era (_era), the master key's id, the credential's forms (_credential: its
    // passkey, one the account may enrol, its salt, key and wrap), then whether that passkey is enrolled already and
    // the master key current, then the grant's and the roster's forms
    if (!this.on()) return fail(409, NOT_ON);
    const kem = this.kemChecks(b);
    if (kem) return kem;
    if (!HEX32.test(String(b.mk_id))) return fail(422, "The master key's id is 32 lowercase hex characters.");
    const cred = b.credential as { passkey_id: string; salt: string; public_key: string; wrapped: string; confirmation?: string } | undefined;
    const passkey = this.passkeys.find((p) => p.id === cred?.passkey_id);
    if (!cred || !passkey) return fail(422, "That is not one of this account's passkeys.");
    if (this.high && passkey.tier !== "hardware") return fail(409, "High security is on: only a hardware key unlocks end-to-end encryption.");
    if (!(await credentialOk(cred, this.pq, b.mk_id))) return fail(422, "The credential is wrong.");
    if (this.credentials.some((c) => c.passkey_id === passkey.id)) return fail(409, "That passkey unlocks end-to-end encryption already.");
    if (b.mk_id !== this.currentMk()) return fail(409, WORDS.stale);
    if (!sealedOk(b.grant, this.pq ? 2 : 1) || !sentBytes(b.roster, undefined, WRAP_MIN, ROSTER_MAX)) return fail(422, "The grant or the roster is wrong.");
    const word = await this.confirmed(b, "credentials", TOUCH_ENROL);
    if (Array.isArray(word)) return word;
    // confirmed by a passkey that unlocks the keys already (never the one being added), or the code's proof
    if (word.by !== "recovery" && !this.staysEnrolled(word.by)) return fail(403, CONFIRM_STAYING);
    // and the passkey added: its own confirmation (passkeys.use_passkey_confirmation, only_by that passkey)
    const own = this.confirmer(String(cred.confirmation ?? ""), ENROL_OWN, TOUCH_ENROL_OWN, null);
    if (Array.isArray(own)) return own;
    if (own.id !== passkey.id) return fail(403, ENROL_OWN);
    return this.hold("credentials", () => {
      // as _apply_enrol: the master key still current, its era, the confirmer asked again, the passkey still the
      // account's, one it may enrol, not enrolled
      if (b.mk_id !== this.currentMk()) return fail(409, WORDS.stale);
      const era = this.eraStill(b.kem === "x-wing");
      if (era) return era;
      if (word.by !== "recovery" && !this.staysEnrolled(word.by)) return fail(403, CONFIRM_STAYING);
      if (!this.passkeys.includes(passkey)) return fail(422, "That is not one of this account's passkeys.");
      if (this.high && passkey.tier !== "hardware") return fail(409, "High security is on: only a hardware key unlocks end-to-end encryption.");
      if (this.credentials.some((c) => c.passkey_id === passkey.id)) return fail(409, "That passkey unlocks end-to-end encryption already.");
      this.credentials.push({ passkey_id: passkey.id, credential_id: passkey.credential_id, salt: cred.salt, public_key: cred.public_key, wrapped: cred.wrapped,
        ...(this.pq ? { anchor: (cred as { anchor?: string }).anchor } : {}), created_at: this.now });
      this.grants.push({ mk_id: String(b.mk_id), to: passkey.credential_id, sealed: String(b.grant), created_at: this.now });
      this.roster = { mk_id: String(b.mk_id), sealed: String(b.roster) };
    }, { recovery: word.by === "recovery", madeBy: word.by, use: [word.use, () => { this.spend(cred.confirmation); }],
      body: { credential: keptCredential(passkey, cred), mk_id: b.mk_id, grant: b.grant, roster: b.roster, own_touch: true } });
  }

  /** Turning encryption off: held a day (the long hold), signed by a passkey that unlocks the keys unless the code's
   *  proof comes with it. */
  private async off(b: Record<string, unknown>): Promise<Reply> {
    this.settleDue();
    const st = await this.statement(b.statement, ["e2e-off"]);
    if (Array.isArray(st)) return st;
    const kem = this.kemChecks(b, st);
    if (kem) return kem;
    if (!this.on()) return fail(409, NOT_ON);
    const word = b.recovery_proof ? await this.confirmed(b, "off", CONFIRM) : null;
    if (Array.isArray(word)) return word;
    const byCode = word?.by === "recovery";
    if (!byCode && !this.staysEnrolled(st.signer.id)) return fail(403, NOT_STAYING);
    const after = this.statements.at(-1)?.payload ?? null;
    return this.hold("off", () => {
      // as _apply_off: its era, its signer asked again, the statement still the next
      const era = this.eraStill(b.kem === "x-wing");
      if (era) return era;
      const still = this.signerStill(st.signer, byCode);
      if (still) return still;
      const next = this.stillNext(st, after);
      if (next) return next;
      this.keep(st);
    }, { recovery: byCode, madeBy: st.signer.id, long: true, use: [st.use, ...(word ? [word.use] : [])],
      body: { statement: keptStatement(st, b) } });
  }

  /** Renewed anchors, as e2e_keys.anchors takes them: passkeys' own wraps, each under a fresh salt, never the one its
   *  record has, and the recovery code's anchor (recovery_anchor, with the recovery key it is of, which must be the
   *  account's then and when the hold ends); one of them at least. Confirmed by a passkey that unlocks the keys, or the
   *  code's proof, and the code's anchor only by the code's proof (ANCHOR_CODE, refused before the confirmation is
   *  used; the server also refuses as its hold ends one placed before that rule, which this server never holds); and
   *  each renewed passkey's anchor but the confirming passkey's own with a confirmation its own passkey made
   *  (`confirmation` in its entry), used up as the change is placed. */
  private async anchors(b: Record<string, unknown>): Promise<Reply> {
    // in e2e_keys.anchors' order, in its words: the list's shape (at most 20 entries, and they or the code's anchor);
    // then, a change whose time has come taking effect first, the write's era (kemChecks) and the master key's id; each
    // entry in turn (an object, its passkey once, its salt's 32 bytes, its wrap in the era's form, then a post-quantum
    // one's anchor the master key it names, a classical one's none); the code's anchor (an object, its recovery key in
    // the era's form, its anchor's wrap); and only then encryption on, the master key current, each passkey enrolled
    // and under a fresh salt, the code the account's, and the code's anchor only with the code's proof
    const list = b.anchors as unknown;
    const code = b.recovery_anchor as unknown;
    if (!Array.isArray(list) || list.length > MAX_PASSKEYS || !(list.length || (code !== undefined && code !== null))) {
      return fail(422, "Renewed anchors are a list of {passkey_id, salt, wrapped, anchor?, confirmation?}, and the recovery code's {public_key, anchor}: one of them at least.");
    }
    this.settleDue();
    const kem = this.kemChecks(b);
    if (kem) return kem;
    const pq = this.pq;
    if (typeof b.mk_id !== "string" || !HEX32.test(b.mk_id)) return fail(422, "The master key's id is 32 lowercase hex characters.");
    const given: { passkey_id: string; salt: string; wrapped: string; anchor?: string; confirmation?: string }[] = [];
    for (const entry of list as unknown[]) {
      if (!entry || typeof entry !== "object" || Array.isArray(entry)) return fail(422, "The anchor is a JSON object.");
      const a = entry as Record<string, unknown>;
      if (typeof a.passkey_id !== "string" || given.some((x) => x.passkey_id === a.passkey_id)) return fail(422, "Each renewed anchor names one enrolled passkey, once.");
      if (!sentBytes(a.salt, 32)) return fail(422, "The renewed anchor's salt is 32 bytes, in base64url without padding.");
      // _key_wrap: a post-quantum credential's seed in exactly 60 bytes, a classical one's 28 to 1,024
      if (!(pq ? sentBytes(a.wrapped, C.WRAP_LEN) : sentBytes(a.wrapped))) {
        return fail(422, `The credential's wrap is ${pq ? `${C.WRAP_LEN} bytes` : `${WRAP_MIN} to ${WRAP_MAX} bytes`}, in base64url without padding.`);
      }
      if (pq) {
        if (typeof a.anchor !== "string" || !HEX32.test(a.anchor)) return fail(422, "The renewed anchor's anchor is 32 lowercase hex characters.");
        if (a.anchor !== b.mk_id) return fail(422, "A post-quantum credential's anchor is the master key the change names.");
      } else if (a.anchor !== undefined && a.anchor !== null) {
        return fail(422, CLASSICAL_FORMS);                  // a classical credential's names none: a post-quantum one's form
      }
      given.push({ passkey_id: a.passkey_id, salt: String(a.salt), wrapped: String(a.wrapped), ...(pq ? { anchor: String(a.anchor) } : {}),
        confirmation: a.confirmation as string | undefined });
    }
    let renewedCode: { public_key: string; anchor: string } | null = null;
    if (code !== undefined && code !== null) {
      if (typeof code !== "object" || Array.isArray(code)) return fail(422, "The recovery code's renewed anchor is a JSON object.");
      const r = code as Record<string, unknown>;
      // _key: the recovery key in the era's form, its length then whether anything can be sealed to it; _wrap: its anchor
      if (!sentBytes(r.public_key, pq ? C.HYBRID_KEY_LEN : 32)) return fail(422, `The recovery key is ${pq ? C.HYBRID_KEY_LEN : 32} bytes, in base64url without padding.`);
      if (pq && !(await usableHybrid(r.public_key))) return fail(422, `The recovery key is not a post-quantum key anything can be sealed to: ${HYBRID_KEY_WORDS}`);
      if (!pq && !(await usableKey(r.public_key))) return fail(422, "The recovery key is not an X25519 key anything can be sealed to.");
      if (!sentBytes(r.anchor)) return fail(422, `The recovery anchor is ${WRAP_MIN} to ${WRAP_MAX} bytes, in base64url without padding.`);
      renewedCode = { public_key: String(r.public_key), anchor: String(r.anchor) };
    }
    if (!this.on()) return fail(409, NOT_ON);
    if (b.mk_id !== this.currentMk()) return fail(409, WORDS.stale);
    for (const a of given) {
      const row = this.credentials.find((c) => c.passkey_id === a.passkey_id);
      if (!row) return fail(422, "A renewed anchor is of a passkey that unlocks end-to-end encryption.");
      if (row.salt === a.salt) return fail(422, SALT_SAME);
    }
    if (renewedCode && renewedCode.public_key !== this.recovery?.public_key) return fail(409, WORDS.codeChanged);
    if (renewedCode && !b.recovery_proof) return fail(403, ANCHOR_CODE);
    const word = await this.confirmed(b, "anchors", TOUCH_ANCHOR);
    if (Array.isArray(word)) return word;
    if (word.by !== "recovery" && !this.staysEnrolled(word.by)) return fail(403, CONFIRM_STAYING);
    // each other passkey's own confirmation, as passkeys.use_passkey_confirmation takes one (only_by): its passkey's,
    // and a hardware key's while high security is on
    const others = given.filter((a) => a.passkey_id !== word.by);
    for (const a of others) {
      const own = this.confirmer(String(a.confirmation ?? ""), ANCHOR_OWN, TOUCH_ANCHOR, null);
      if (Array.isArray(own)) return own;
      if (own.id !== a.passkey_id) return fail(403, ANCHOR_OWN);
    }
    return this.hold("anchors", () => {
      // as _apply_anchors: the master key still current, its era, the confirmer asked again, each passkey still
      // enrolled, the recovery code the same
      if (b.mk_id !== this.currentMk()) return fail(409, WORDS.stale);
      const era = this.eraStill(b.kem === "x-wing");
      if (era) return era;
      if (word.by !== "recovery" && !this.staysEnrolled(word.by)) return fail(403, CONFIRM_STAYING);
      if (given.some((a) => !this.credentials.some((c) => c.passkey_id === a.passkey_id))) return fail(409, "A passkey of the renewed anchors no longer unlocks end-to-end encryption.");
      if (renewedCode && renewedCode.public_key !== this.recovery?.public_key) return fail(409, WORDS.codeChanged);
      for (const a of given) Object.assign(this.credentials.find((c) => c.passkey_id === a.passkey_id)!, { salt: a.salt, wrapped: a.wrapped, ...(this.pq ? { anchor: a.anchor } : {}) });
      if (renewedCode && this.recovery) this.recovery = { ...this.recovery, anchor: renewedCode.anchor };
    }, { recovery: word.by === "recovery", madeBy: word.by,
      body: { mk_id: b.mk_id, anchors: Object.fromEntries(given.map((a) => [a.passkey_id, { salt: a.salt, wrapped: a.wrapped }])),
        recovery: renewedCode },
      use: [word.use, ...others.map((a) => () => { this.spend(a.confirmation); })] });
  }
}
