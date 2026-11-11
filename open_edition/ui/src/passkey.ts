// Signing in with a passkey (the server's passkeys.py): the browser's own prompt, over a challenge derived from the
// server's, nothing the page keeps but the sign-in token the server answers with.
import { api, type GrantScope, type User } from "./api";

export const passkeysHere = typeof window !== "undefined" && typeof window.PublicKeyCredential !== "undefined" && !!navigator.credentials;

/** How long a prompt waits, as long as the server's challenge lives (passkeys.CHALLENGE_TTL). */
const TIMEOUT = 300000;

function fromB64u(s: string): Uint8Array {
  const b = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4));
  return Uint8Array.from(b, (c) => c.charCodeAt(0));
}

function toB64u(buf: ArrayBuffer): string {
  let s = "";
  for (const b of new Uint8Array(buf)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** The server's options for a ceremony whose challenge it picks (signing in, a confirmation), as it sends them. Its
 *  challenge is never handed to the authenticator as it is: only derived (derivedChallenge, through ceremony). */
export type AssertionOptions = {
  challenge_id: string;
  publicKey: { challenge: string; rpId?: string; timeout: number; userVerification: UserVerificationRequirement; allowCredentials?: { type: "public-key"; id: string }[] };
};

/** A passkey's answer as the server reads it. */
export interface AssertionAnswer {
  id: string; type: string;
  response: { clientDataJSON: string; authenticatorData: string; signature: string; userHandle: string | null };
}

// ---- the challenge a passkey signs ----------------------------------------------------------------------------------
//
// A passkey signs whatever challenge the page hands it, and a linked session takes a signature as a step of the
// owner's (a command, a move, a statement of the encryption keys) when its challenge is the sha256 of
// "proofpane-step/1", a zero byte and the step's canonical JSON. A server that picked such a challenge for a ceremony
// of its own (signing in, a confirmation) would have the owner's touch sign a step the owner never saw. So the page
// hands the authenticator one of three challenges, each made only here: a step's (stepChallenge), one derived from the
// server's for the ceremony's purpose (derivedChallenge), whose bytes begin "proofpane-confirm/1" where a step's begin
// "proofpane-step/1", or 32 random bytes of the page's own for a touch whose answer is sent nowhere (pageChallenge).
// challengeAudit.test.ts checks that no other reaches navigator.credentials.get. In older releases a step's challenge
// was the sha256 of its canonical JSON alone, and a page signed in and confirmed over the server's challenge as it was;
// so nothing a passkey signed then is a step now.

declare const madeAs: unique symbol;
/** A challenge the page hands the authenticator, and how it was made: "step", "derived" or "page". */
export type Challenge<K extends "step" | "derived" | "page" = "step" | "derived" | "page"> = Uint8Array<ArrayBuffer> & { readonly [madeAs]: K };

/** What the page puts before a challenge the server picked, as it derives the one the authenticator signs. */
export const CEREMONY_TAG = "proofpane-confirm/1";
/** What the page puts before a step's canonical JSON, as it makes the challenge a linked session verifies. */
export const STEP_TAG = "proofpane-step/1";
/** The purposes of the confirmations the page asks for: a change's, and end-to-end encryption's touches (cancelling
 *  the change that waits, the touch that confirms adding a passkey to it, made with one that unlocks it already, the
 *  second touch of the passkey being added, its own confirmation, each touch of renewing its anchors, and each touch of
 *  the upgrade to post-quantum keys). */
export type ConfirmPurpose = "confirm" | "e2e-cancel" | "e2e-enrol-touch" | "e2e-enrol-own" | "e2e-anchor-touch"
  | "e2e-upgrade-touch";
/** The purposes of the ceremonies whose challenge the server picks. */
export type CeremonyPurpose = "sign-in" | ConfirmPurpose;

const sha256 = async (bytes: Uint8Array<ArrayBuffer>) => new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));

/** A step's challenge, which a session verifies: the sha256 of "proofpane-step/1", a zero byte, and the UTF-8 of its
 *  canonical JSON (RFC 8785, canonicalJson). The server and the link client check the same (passkeys.step_challenge,
 *  ade_link.step_challenge), all three held to tests/vectors/step_v1.json. */
export async function stepChallenge(payload: Record<string, unknown>): Promise<Challenge<"step">> {
  return (await sha256(new TextEncoder().encode(`${STEP_TAG}\0${canonicalJson(payload)}`))) as Challenge<"step">;
}

/** The challenge the authenticator signs for one the server picked (`server`, as its options give it, base64url) in a
 *  ceremony of `purpose`: the sha256 of "proofpane-confirm/1", a zero byte, the purpose, a zero byte, and the server's
 *  bytes. The server checks this one (passkeys.derived_challenge). */
export async function derivedChallenge(purpose: CeremonyPurpose, server: string): Promise<Challenge<"derived">> {
  const head = new TextEncoder().encode(`${CEREMONY_TAG}\0${purpose}\0`);
  const theirs = fromB64u(server);
  const all = new Uint8Array(head.length + theirs.length);
  all.set(head);
  all.set(theirs, head.length);
  return (await sha256(all)) as Challenge<"derived">;
}

/** 32 random bytes of the page's own, for a touch that only asks a passkey's PRF and whose answer goes nowhere
 *  (unlocking encryption, a second touch): made at once, so a click's handler reaches the prompt with nothing awaited. */
export function pageChallenge(): Challenge<"page"> {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return bytes as Challenge<"page">;
}

/** What one prompt asks for: its challenge, the relying party (the server's word, which the browser holds to this
 *  page's own address), and the credentials it offers (none named lets the browser offer any of this site's). */
export interface Ask { challenge: Challenge; rpId?: string; allowCredentials?: { type: "public-key"; id: string }[] }

/** A ceremony whose challenge the server picked, ready for its prompt: the challenge id to send back with the answer,
 *  and what the prompt asks, its challenge derived for the ceremony's purpose. */
export interface Ceremony { challenge_id: string; ask: Ask & { challenge: Challenge<"derived"> } }

/** The options of a ceremony of `purpose`, asked for at `path` naming that purpose (and `extra`), with the challenge
 *  derived for it. The server refuses options asked for without a purpose, as a page of an older release asked for
 *  them: such a page signs the server's challenge as it is. */
async function ceremony(purpose: CeremonyPurpose, path: string, extra: Record<string, unknown> = {}): Promise<Ceremony> {
  const o = await api<AssertionOptions>(path, { method: "POST", body: { ...extra, purpose } });
  return { challenge_id: o.challenge_id, ask: { challenge: await derivedChallenge(purpose, o.publicKey.challenge),
    rpId: o.publicKey.rpId, allowCredentials: o.publicKey.allowCredentials } };
}

/** What a confirmation is made for, which the server keeps in it and checks where it is spent, so it is spent on that
 *  change alone: `device`, the code of a device waiting to be signed in, for one that approves that device; or `change`,
 *  the change itself as the server names it (passkeys.CHANGE: "link-revoke:<link id>", "passkey-add", ...). */
export interface ConfirmFor { device?: string; change?: string }

const forBody = (target?: ConfirmFor) => ({ ...(target?.device ? { device: target.device } : {}), ...(target?.change ? { change: target.change } : {}) });

/** The server's options to confirm a change, or one of end-to-end encryption's touches (`purpose`), with the challenge
 *  derived for it; with `hardware`, offering the account's hardware keys alone; with `target`, what the confirmation
 *  is made for (ConfirmFor). */
export const confirmCeremony = (purpose: ConfirmPurpose, hardware = false, target?: ConfirmFor) =>
  ceremony(purpose, "/api/account/confirm/options", { ...(hardware ? { hardware: true } : {}), ...forBody(target) });

/** The browser's own prompt to sign with one of this site's passkeys (those `ask` offers, or any), user verification
 *  required whatever the server said, with `extensions` asked of it (the PRF's, for end-to-end encryption): its answer
 *  as the server reads it, what the extensions gave, and the browser's word on the authenticator that answered
 *  (`attachment`: "platform", this device's own, or "cross-platform", another device or a security key; null where
 *  the browser does not say). Nothing is awaited before the prompt, so a click's handler may call it first thing. */
export async function assertionWith(ask: Ask, extensions?: AuthenticationExtensionsClientInputs):
    Promise<{ credential: AssertionAnswer; results: AuthenticationExtensionsClientOutputs; attachment: string | null }> {
  let cred: PublicKeyCredential;
  try {
    cred = (await navigator.credentials.get({
      publicKey: {
        challenge: ask.challenge, rpId: ask.rpId, timeout: TIMEOUT, userVerification: "required",
        allowCredentials: (ask.allowCredentials || []).map((c) => ({ type: c.type, id: fromB64u(c.id) as BufferSource })),
        ...(extensions ? { extensions } : {}),
      },
    })) as PublicKeyCredential;
  } catch (e) {
    const name = e instanceof DOMException ? e.name : "";
    throw new Error(name === "NotAllowedError" || name === "AbortError" ? "The passkey request was cancelled or timed out." : String(e));
  }
  const r = cred.response as AuthenticatorAssertionResponse;
  return {
    credential: {
      id: cred.id, type: cred.type,
      response: {
        clientDataJSON: toB64u(r.clientDataJSON), authenticatorData: toB64u(r.authenticatorData),
        signature: toB64u(r.signature), userHandle: r.userHandle ? toB64u(r.userHandle) : null,
      },
    },
    results: typeof cred.getClientExtensionResults === "function" ? cred.getClientExtensionResults() : {},
    attachment: typeof cred.authenticatorAttachment === "string" ? cred.authenticatorAttachment : null,
  };
}

async function assertion(ask: Ask): Promise<AssertionAnswer> {
  return (await assertionWith(ask)).credential;
}

export async function signInWithPasskey(): Promise<{ token: string; user: User }> {
  const c = await ceremony("sign-in", "/api/auth/passkey/options");
  return api("/api/auth/passkey", { method: "POST", body: { challenge_id: c.challenge_id, credential: await assertion(c.ask) } });
}

/** What the account can confirm a change with: a change the sign-in alone does not make (adding or removing a passkey,
 *  signing out everywhere) needs one of its passkeys or its password, unless it has neither (this Mac's own). While
 *  its high security is on, only one of its hardware keys confirms such a change (`hardware` says how many it has). */
/** What the account confirms a change with (passkeys.confirm_ways): `e2e`, whether end-to-end encryption is on, when the
 *  password confirms no change to a passkey (absent from a server before). */
export interface ConfirmWays { needed: boolean; password: boolean; passkeys: number; hardware?: number; high_security?: boolean; e2e?: boolean }
export const confirmWays = () => api<ConfirmWays>("/api/account/confirm");

/** A confirmation, for the change `target` names (ConfirmFor) in the next 5 minutes, from one of the account's own
 *  passkeys; with `hardware`, from one of its hardware keys, the only ones the browser is offered. */
export async function confirmWithPasskey(hardware = false, target?: ConfirmFor): Promise<string> {
  const c = await confirmCeremony("confirm", hardware, target);
  const credential = await assertion(c.ask);
  return (await api<{ confirmation: string }>("/api/account/confirm", { method: "POST", body: { challenge_id: c.challenge_id, credential } })).confirmation;
}

/** A confirmation, for the change `target` names (ConfirmFor) in the next 5 minutes, from the account's password. */
export async function confirmWithPassword(password: string, target?: ConfirmFor): Promise<string> {
  return (await api<{ confirmation: string }>("/api/account/confirm", { method: "POST", body: { password, ...forBody(target) } })).confirmation;
}

export interface NewPasskeyOptions {
  challenge_id: string;
  publicKey: {
    rp: PublicKeyCredentialRpEntity; user: { id: string; name: string; displayName: string }; challenge: string;
    pubKeyCredParams: PublicKeyCredentialParameters[]; timeout: number; attestation: AttestationConveyancePreference;
    authenticatorSelection: AuthenticatorSelectionCriteria; excludeCredentials: { type: "public-key"; id: string }[];
  };
}

/** The server's options for a new passkey, which use up the confirmation, or the recovery code's proof (end-to-end
 *  encryption, for an owner who lost every passkey). Fetched before the person's click that makes the passkey, so that
 *  click reaches the browser's prompt with nothing awaited first (Safari asks for the click). */
export const newPasskeyOptions = (confirmation?: string, recoveryProof?: { challenge_id: string; proof: string }) =>
  api<NewPasskeyOptions>("/api/account/passkeys/options", {
    method: "POST", body: recoveryProof ? { recovery_proof: recoveryProof } : confirmation ? { confirmation } : {},
  });

/** How a passkey is to be made: `securityKey`, on a security key (Account's "Add a security key"). */
export interface MakeHow { securityKey?: boolean }

/** The browser's own prompt to make a passkey with these options, then the server keeping its public half, with its
 *  attestation and the transports the browser reports for it, which the server judges it by (hardware or device). It
 *  asks for WebAuthn's PRF extension and tells the server whether the browser says the passkey has one (`prf.enabled`,
 *  in its extension results, as a credential's JSON carries them): end-to-end encryption unlocks with that. It tells the
 *  server, too, what the browser says of the authenticator that made it (`authenticatorAttachment`, null where it says
 *  nothing), which the server names it by. With `how.securityKey`, it asks the browser for a roaming authenticator
 *  (cross-platform) and gives WebAuthn's "security-key" hint, which a browser that does not know hints passes over. An
 *  empty name is sent as it is: the server names the passkey by what it is. The server's answer is returned (while
 *  encryption is on, adding a passkey waits on a hold it names). */
export async function makePasskey(opts: NewPasskeyOptions, name: string, how: MakeHow = {}): Promise<Record<string, unknown>> {
  const pk = opts.publicKey;
  const selection: AuthenticatorSelectionCriteria = how.securityKey
    ? { ...pk.authenticatorSelection, authenticatorAttachment: "cross-platform" } : pk.authenticatorSelection;
  // hints is WebAuthn level 3's, not yet in TypeScript's DOM types for these options
  const publicKey = {
    rp: pk.rp, user: { id: fromB64u(pk.user.id) as BufferSource, name: pk.user.name, displayName: pk.user.displayName },
    challenge: fromB64u(pk.challenge) as BufferSource, pubKeyCredParams: pk.pubKeyCredParams, timeout: pk.timeout,
    attestation: pk.attestation, authenticatorSelection: selection,
    excludeCredentials: pk.excludeCredentials.map((c) => ({ type: c.type, id: fromB64u(c.id) as BufferSource })),
    extensions: { prf: {} },
    ...(how.securityKey ? { hints: ["security-key"] } : {}),
  } as PublicKeyCredentialCreationOptions;
  let cred: PublicKeyCredential;
  try {
    cred = (await navigator.credentials.create({ publicKey })) as PublicKeyCredential;
  } catch (e) {
    const n = e instanceof DOMException ? e.name : "";
    // asked of a security key, the passkey already there is on the key, or on a phone reached by its code
    if (n === "InvalidStateError") throw new Error(how.securityKey
      ? "The security key or phone you chose already holds a passkey for your account: it is in the list above."
      : "This device already has a passkey for your account.");
    throw new Error(n === "NotAllowedError" || n === "AbortError" ? "The passkey request was cancelled or timed out." : String(e));
  }
  const r = cred.response as AuthenticatorAttestationResponse;
  const transports = typeof r.getTransports === "function" ? r.getTransports() : [];
  const results = typeof cred.getClientExtensionResults === "function" ? cred.getClientExtensionResults() : {};
  const prf = results.prf?.enabled === true;
  const attachment = typeof cred.authenticatorAttachment === "string" ? cred.authenticatorAttachment : null;
  return (await api<Record<string, unknown> | null>("/api/account/passkeys", {
    method: "POST",
    body: {
      challenge_id: opts.challenge_id, name: name.trim(), prf,
      credential: {
        id: cred.id, type: cred.type, response: { clientDataJSON: toB64u(r.clientDataJSON), attestationObject: toB64u(r.attestationObject), transports },
        clientExtensionResults: { prf: { enabled: prf } }, authenticatorAttachment: attachment,
      },
    },
  })) ?? {};
}

/** One of the account's passkeys given another name, with the person's word made for that rename (`confirmation`,
 *  confirmWithPasskey or confirmWithPassword for passkey-rename:<id>: passkeys.rename); an empty name has the server
 *  name it by what it is again. The passkey as the server then lists it. */
export const renamePasskey = (id: string, name: string, confirmation?: string) =>
  api<Record<string, unknown>>(`/api/account/passkeys/${encodeURIComponent(id)}`, { method: "PATCH", body: { name, confirmation } });

/** What the server says a passkey is (passkeys.kind_of), with what a server from before that says none gives: its
 *  tier, and whether it is backed up. */
export interface KindOf { kind?: string | null; tier?: string | null; maker?: string | null; backed_up?: boolean }

/** A passkey's kind as a noun: "hardware key", "synced passkey", "security key", "device-bound passkey"; for a server
 *  from before that names no kind, "hardware key", "synced passkey" where it is backed up, else "device passkey" (its
 *  tier alone, which cannot tell a security key from this device's own). */
export function kindName(p: KindOf): string {
  const kind = p.kind ?? (p.tier === "hardware" ? "hardware" : p.backed_up ? "synced" : null);
  return kind === "hardware" ? "hardware key" : kind === "synced" ? "synced passkey" : kind === "security-key" ? "security key"
    : kind === "device" ? "device-bound passkey" : "device passkey";
}

/** A passkey's kind as Account and Encryption say it: a hardware key with its model where the server names one
 *  ("Hardware key: YubiKey 5 Series"), any other by its kind ("Synced passkey"). */
export function kindWords(p: KindOf): string {
  const noun = kindName(p);
  const said = noun.charAt(0).toUpperCase() + noun.slice(1);
  return noun === "hardware key" && p.maker ? `${said}: ${p.maker}` : said;
}

/** Whether the server says a passkey is a security key: a hardware key, or one the browser said another device made. */
export const isSecurityKey = (p: KindOf) => { const n = kindName(p); return n === "hardware key" || n === "security key"; };

// ---- the owner's word on a command to a linked session ------------------------------------------------------------

/** RFC 8785 for what a signed payload holds: objects, lists, strings, whole numbers within 2^53 - 1, booleans and null,
 *  object keys sorted by UTF-16 code units at every level (what Array.prototype.sort does by default). The server and the
 *  session canonicalise the same value themselves, so anything they would read another way is refused here: a lone
 *  surrogate, a fraction, a number past 2^53 - 1. */
export function canonicalJson(v: unknown): string {
  if (v === null) return "null";
  if (v === true || v === false) return v ? "true" : "false";
  if (typeof v === "number") {
    if (!Number.isSafeInteger(v)) throw new Error("A payload's numbers are whole and within 2^53 - 1.");
    return String(v);
  }
  if (typeof v === "string") {
    if (loneSurrogate(v)) throw new Error("The text holds half of a character (a lone surrogate).");
    return JSON.stringify(v);
  }
  if (Array.isArray(v)) return `[${v.map(canonicalJson).join(",")}]`;
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    return `{${Object.keys(o).sort().map((k) => `${canonicalJson(k)}:${canonicalJson(o[k])}`).join(",")}}`;
  }
  throw new Error(`A ${typeof v} is not a payload value.`);
}

function loneSurrogate(s: string): boolean {
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) {
      const d = s.charCodeAt(i + 1);
      if (!(d >= 0xdc00 && d <= 0xdfff)) return true;
      i++;
    } else if (c >= 0xdc00 && c <= 0xdfff) return true;
  }
  return false;
}

const hex = (bytes: Uint8Array) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");

/** "sha256:" and the hex sha256 of a text's UTF-8 bytes: what an answer carries of the question it answers. */
export async function textDigest(text: string): Promise<string> {
  return "sha256:" + hex(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text))));
}

export type SignedPurpose = "instruction" | "answer" | "resume" | "move";
/** `allow`: the credentials the prompt offers, the account's hardware keys while its high security is on (only those
 *  sign a command then), else none named, which lets the browser offer any of its passkeys. */
export interface Prepared { payload: Record<string, unknown>; challenge: Challenge<"step">; rpId: string; words: string; allow: string[] }
export interface Signed { payload: Record<string, unknown>; credential: unknown }

/** The longest a move lives, as the server holds it (link_commands.MOVE_LIFE_MAX): its link takes it within an hour. */
export const MOVE_LIFE = 3600;

/** How far ahead of this page's clock a signed payload may be dated: the clock skew the server allows (300 seconds). */
export const SERVER_AHEAD = 300;
export const SERVER_AHEAD_WORDS = "The server's clock is more than 5 minutes ahead of this device's, so nothing was signed. " +
  "If this device's clock is right, the server's time cannot be trusted.";

/** The time a payload is dated with (a nonce's server_time, or one second after the stop a resume follows), refused
 *  where it is not a whole number of seconds or runs more than SERVER_AHEAD ahead of this page's clock. A server that
 *  dated what the owner signs ahead could hold it and hand it to a link later, which takes a command or a statement as
 *  made when it says. A time behind this page's clock is taken: it only shortens how long a link takes what is signed. */
export function datedNow(t: number): number {
  if (!Number.isSafeInteger(t) || t <= 0) throw new Error("The server's time is not a time.");
  if (t > Math.floor(Date.now() / 1000) + SERVER_AHEAD) throw new Error(SERVER_AHEAD_WORDS);
  return t;
}

/** A command's payload, composed here around a nonce the server gave, and its challenge (the sha256 of its canonical
 *  bytes). Done before the click that signs, so that click reaches the browser's prompt with nothing awaited first
 *  (Safari asks for a click per ceremony). Its times are the server's, from the moment it gave the nonce, and refused
 *  where they run ahead of this page's clock (datedNow): the time the nonce took to arrive is not added, so a slow
 *  arrival (a phone's tab put to sleep) still dates the payload within the seconds after the nonce the server holds it
 *  to. A move carries the session it moves and no words. Its id is new
 *  and random, or, signing what a connected app proposed, the proposal's id (`fields.id`): the server knows by it which
 *  proposal the command is (the open edition's ade/proposals.py). */
export async function prepareSigned(purpose: SignedPurpose, to: string, fields: { text?: string; ref?: number; digest?: string; life?: number; after?: number; session?: string; id?: string } = {}): Promise<Prepared> {
  if (fields.id !== undefined && !/^[0-9a-f]{32}$/.test(fields.id)) throw new Error("A proposal's id is 32 hex digits.");
  const n = await api<{ nonce: string; rp_id: string; deployment: string; account: string; server_time: number; credentials?: string[] }>(
    "/api/ade/link/nonce", { method: "POST", body: { purpose } });
  const now = datedNow(Math.max(n.server_time, (fields.after ?? 0) + 1));
  const life = purpose === "resume" ? 600 : Math.min(Math.max(fields.life ?? 3600, 60), purpose === "move" ? MOVE_LIFE : 86400);
  const random = new Uint8Array(16);
  crypto.getRandomValues(random);
  const payload: Record<string, unknown> = {
    v: 2, deployment: n.deployment, purpose, account: n.account, nonce: n.nonce, id: fields.id ?? hex(random), to,
    created_at: now, expires_at: now + life,
  };
  if (purpose === "move") payload.session = fields.session ?? "";
  else payload.text = purpose === "resume" ? "" : fields.text ?? "";
  if (purpose === "answer") { payload.ref = fields.ref; payload.digest = fields.digest; }
  const allow = (n.credentials ?? []).filter((c) => typeof c === "string");
  return { payload, challenge: await stepChallenge(payload), rpId: n.rp_id, words: String(payload.text ?? ""), allow };
}

/** The longest a version of a task's grant may wait for the server, as it holds it (link_commands.GRANT_LIFE_MAX); the
 *  grant itself lasts until its own lease_end. */
export const GRANT_LIFE = 600;

/** What a version of a task's grant names besides its scope (the app's ade/task_grants.py): its task, its place after
 *  the version before it (`prev`, that version's digest, "" for the first), the task's goal pinned by digest (`ask`),
 *  and who asked for it: the owner composing it, or the agent or the linked session whose ask it signs (`widening`, that
 *  ask's id, "" for the owner's own). */
export interface GrantHead {
  task: string; version: number; prev: string; ask: string;
  requester: { kind: "owner" } | { kind: "agent" | "link"; id: string }; widening: string;
}

/** A version of a task's grant, composed here around a nonce of purpose task-grant, and its challenge, as prepareSigned
 *  composes a command: before the click that signs, its times the server's. Each member is named here, so nothing else
 *  that the caller holds (a requester's name the page shows) is signed. A `lease_end` of null asks for a lease of
 *  `leaseDays` from the nonce's time. */
export async function prepareGrant(head: GrantHead, scope: Omit<GrantScope, "lease_end"> & { lease_end: number | null }, leaseDays = 7): Promise<Prepared> {
  const n = await api<{ nonce: string; rp_id: string; deployment: string; account: string; server_time: number; credentials?: string[] }>(
    "/api/ade/link/nonce", { method: "POST", body: { purpose: "task-grant" } });
  const now = datedNow(n.server_time);
  const random = new Uint8Array(16);
  crypto.getRandomValues(random);
  const payload: Record<string, unknown> = {
    v: 2, deployment: n.deployment, purpose: "task-grant", account: n.account, nonce: n.nonce, id: hex(random),
    task: head.task, version: head.version, prev: head.prev, ask: head.ask,
    requester: head.requester.kind === "owner" ? { kind: "owner" } : { kind: head.requester.kind, id: head.requester.id },
    widening: head.widening,
    repositories: scope.repositories.map((r) => ({ repo: r.repo, push: [...r.push], merge: [...r.merge] })),
    environments: scope.environments.map((e) => ({ name: e.name, rung: e.rung })),
    secrets: scope.secrets.map((s) => ({ name: s.name, lease_seconds: s.lease_seconds })), egress: [...scope.egress],
    lease_end: scope.lease_end ?? now + leaseDays * 86400, rungs: { merge: scope.rungs.merge, deploy: scope.rungs.deploy },
    created_at: now, expires_at: now + GRANT_LIFE,
  };
  const allow = (n.credentials ?? []).filter((c) => typeof c === "string");
  return { payload, challenge: await stepChallenge(payload), rpId: n.rp_id, words: "", allow };
}

/** The settings a linked session holds the Manager's role under (the app's controller.HOLD_SETTINGS), as the page
 *  shows them before the owner signs. */
export interface HoldSettings { cap: number; models: string[]; efforts: string[]; confirm_plans: boolean }

/** "sha256:" and the hex sha256 of the settings' canonical bytes: the digest a holder's payload names, computed here
 *  from the values the review shows, never taken from the server beside them, so that what is signed is what the owner
 *  saw (the server refuses it where they are not its current settings). */
export async function settingsDigest(s: HoldSettings): Promise<string> {
  const value = { cap: s.cap, models: [...s.models], efforts: [...s.efforts], confirm_plans: s.confirm_plans };
  return "sha256:" + hex(new Uint8Array(await crypto.subtle.digest("SHA-256",
    new TextEncoder().encode(canonicalJson(value)))));
}

/** A linked session made the Manager (ade/manager_link.py): the purpose `manager`, its `to` the link,
 *  its `settings` the digest of those it holds the role under (settingsDigest, of the values the review showed), and a
 *  week's life at most, which a new signature renews. Signed with a passkey as a command is. */
export const MANAGER_LIFE = 7 * 86400;
type Nonce = { nonce: string; rp_id: string; deployment: string; account: string; server_time: number;
  credentials?: string[] };
export async function prepareHolder(to: string, settings: HoldSettings, life = MANAGER_LIFE): Promise<Prepared> {
  const n = await api<Nonce>("/api/ade/link/nonce", { method: "POST", body: { purpose: "manager" } });
  const now = datedNow(n.server_time);
  const random = new Uint8Array(16);
  crypto.getRandomValues(random);
  const payload: Record<string, unknown> = {
    v: 2, deployment: n.deployment, purpose: "manager", account: n.account, nonce: n.nonce, id: hex(random), to,
    settings: await settingsDigest(settings), created_at: now,
    expires_at: now + Math.min(Math.max(life, 60), MANAGER_LIFE),
  };
  const allow = (n.credentials ?? []).filter((c) => typeof c === "string");
  return { payload, challenge: await stepChallenge(payload), rpId: n.rp_id, words: "", allow };
}

/** The browser's prompt over a prepared payload: call it first thing in the click's handler. */
export async function signPrepared(p: Prepared): Promise<Signed> {
  const credential = await assertion({
    challenge: p.challenge, rpId: p.rpId, allowCredentials: p.allow.map((id) => ({ type: "public-key" as const, id })),
  });
  return { payload: p.payload, credential };
}
