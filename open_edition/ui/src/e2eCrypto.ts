// The primitives of the page's end-to-end encryption, over WebCrypto alone: X25519,
// HKDF-SHA256 and AES-256-GCM. The envelope a link sends in place of a text, the grants, wraps, chain and roster that
// keep the account's keys on the server, and the recovery code. Keys are CryptoKeys. Bytes that must stand for a key a
// moment (a PRF output, a new master key, the recovery key, a shared secret) are zeroed once imported, and a private
// key never becomes a JavaScript string: where a format wants one in base64url, it is written as bytes.
// Post-quantum keys add X-Wing, whose ML-KEM-768 half comes from ./mlkem768 (the one module that
// names a library) and whose X25519 half stays in WebCrypto: forms 2 and 3, and the last section of this file.
import * as Kem from "./mlkem768";
import { canonicalJson } from "./passkey";

/** Bytes over an ArrayBuffer of their own, which WebCrypto takes. */
export type Bytes = Uint8Array<ArrayBuffer>;

export const V = "proofpane-e2e/1";
export const PREFIX = "e2e1.";
export const HEADER_LEN = 61;                   // 0x01, key_id (16), epk (32), nonce (12)
export const TAG_LEN = 16;
export const OVERHEAD = HEADER_LEN + TAG_LEN;   // 77: an envelope's bytes beyond its plaintext's
const NONCE_LEN = 12;

/** A refusal of the encryption's own, in words the page can show. */
export class E2EError extends Error {}

export const DOES_NOT_OPEN = "It does not open with this key.";
export const NOT_ENVELOPE = "This is not an encrypted text in the form the ADE takes.";
export const OTHER_KEY = "It was sealed to another key.";
export const LOW_ORDER = "Its key is not one a key could be agreed with.";

const subtle = () => globalThis.crypto.subtle;
const encoder = new TextEncoder();
export const utf8 = (s: string): Bytes => encoder.encode(s) as Bytes;
const ascii = new TextDecoder("ascii");

export function random(n: number): Bytes {
  const b = new Uint8Array(n);
  crypto.getRandomValues(b);
  return b;
}

export function concat(...parts: Uint8Array[]): Bytes {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
}

/** Overwrite bytes that held a secret, once they are used. */
export function zero(...parts: (Uint8Array | ArrayBuffer | null | undefined)[]): void {
  for (const p of parts) {
    if (p instanceof Uint8Array) p.fill(0);
    else if (p) new Uint8Array(p).fill(0);
  }
}

export function same(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a[i] ^ b[i];
  return d === 0;
}

export const hex = (bytes: Uint8Array) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");

// ---- base64url without padding ------------------------------------------------------------------------------------

const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
const CODES = utf8(ALPHABET);
const VALUE = (() => {
  const t = new Int8Array(128).fill(-1);
  for (let i = 0; i < 64; i++) t[ALPHABET.charCodeAt(i)] = i;
  return t;
})();

/** base64url without padding, as ASCII bytes: how a secret is written where a format wants base64url. */
export function b64uBytes(bytes: Uint8Array): Bytes {
  const out = new Uint8Array(Math.ceil((bytes.length * 4) / 3));
  let o = 0;
  let i = 0;
  for (; i + 2 < bytes.length; i += 3) {
    const n = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
    out[o++] = CODES[(n >> 18) & 63]; out[o++] = CODES[(n >> 12) & 63]; out[o++] = CODES[(n >> 6) & 63]; out[o++] = CODES[n & 63];
  }
  if (bytes.length - i === 1) {
    const n = bytes[i] << 16;
    out[o++] = CODES[(n >> 18) & 63]; out[o++] = CODES[(n >> 12) & 63];
  } else if (bytes.length - i === 2) {
    const n = (bytes[i] << 16) | (bytes[i + 1] << 8);
    out[o++] = CODES[(n >> 18) & 63]; out[o++] = CODES[(n >> 12) & 63]; out[o++] = CODES[(n >> 6) & 63];
  }
  return out;
}

/** The bytes that base64url without padding, given as ASCII bytes, writes; null when it is not exactly that form: a
 *  character outside the alphabet, a length of 1 mod 4, or bits set past the last byte (so a text reads one way only,
 *  and encoding its bytes again gives it back exactly). */
export function fromB64uBytes(text: Uint8Array): Bytes | null {
  if (text.length % 4 === 1) return null;
  const out = new Uint8Array(Math.floor((text.length * 3) / 4));
  let acc = 0;
  let bits = 0;
  let o = 0;
  for (const c of text) {
    const v = c < 128 ? VALUE[c] : -1;
    if (v < 0) { zero(out); return null; }
    acc = (acc << 6) | v;
    bits += 6;
    if (bits >= 8) { bits -= 8; out[o++] = (acc >> bits) & 255; }
    acc &= (1 << bits) - 1;
  }
  if (acc !== 0) { zero(out); return null; }
  return out;
}

/** base64url without padding of bytes that are no secret (a public key, a ciphertext, a salt). */
export function b64u(bytes: Uint8Array): string {
  return ascii.decode(b64uBytes(bytes));
}

/** The bytes of base64url without padding, read strictly (fromB64uBytes); null for anything else. */
export function unb64u(text: unknown): Bytes | null {
  if (typeof text !== "string") return null;
  const raw = new Uint8Array(text.length);
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    if (c > 127) return null;
    raw[i] = c;
  }
  return fromB64uBytes(raw);
}

// ---- HKDF-SHA256 --------------------------------------------------------------------------------------------------

const EMPTY = new Uint8Array(0);

/** An HKDF key over `ikm` (never extractable: WebCrypto keeps no other kind). */
export function hkdfKey(ikm: Uint8Array): Promise<CryptoKey> {
  return subtle().importKey("raw", ikm as Bytes, "HKDF", false, ["deriveKey", "deriveBits"]);
}

const hkdf = (info: string, salt: Uint8Array = EMPTY): HkdfParams => ({ name: "HKDF", hash: "SHA-256", salt: salt as Bytes, info: utf8(info) });

/** HKDF-SHA256 with `info` (and an empty salt unless one is given), `length` bytes. */
export async function hkdfBytes(key: CryptoKey, info: string, length: number, salt?: Uint8Array): Promise<Bytes> {
  return new Uint8Array(await subtle().deriveBits(hkdf(info, salt), key, length * 8));
}

/** HKDF-SHA256 with `info`, as an AES-256-GCM key that is never extractable. */
export function hkdfAes(key: CryptoKey, info: string, usages: KeyUsage[], salt?: Uint8Array): Promise<CryptoKey> {
  return subtle().deriveKey(hkdf(info, salt), key, { name: "AES-GCM", length: 256 }, false, usages);
}

// ---- X25519 -------------------------------------------------------------------------------------------------------

const X = { name: "X25519" };
// PKCS#8 of an X25519 private key: this header, then its 32 bytes (RFC 8410)
const PKCS8_HEAD = Uint8Array.of(0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x6e, 0x04, 0x22, 0x04, 0x20);
const BASE_POINT = (() => { const b = new Uint8Array(32); b[0] = 9; return b; })();

/** Whether this browser does X25519 in WebCrypto, which opening anything takes. */
export async function x25519Here(): Promise<boolean> {
  try {
    await subtle().importKey("raw", BASE_POINT, X, true, []);
    return true;
  } catch {
    return false;
  }
}

export function x25519Public(raw: Uint8Array): Promise<CryptoKey> {
  if (raw.length !== 32) throw new E2EError("An X25519 public key is 32 bytes.");
  return subtle().importKey("raw", raw as Bytes, X, true, []);
}

/** A private key's public key: X25519 of it and the base point. */
export async function publicOf(priv: CryptoKey): Promise<Bytes> {
  return new Uint8Array(await subtle().deriveBits({ name: "X25519", public: await x25519Public(BASE_POINT) }, priv, 256));
}

/** An X25519 private key from its 32 bytes, through PKCS#8 (WebCrypto takes no raw private key); the copy made for it
 *  is zeroed, the caller's bytes are the caller's to zero. */
export async function privateFromSeed(seed: Uint8Array, extractable = false): Promise<CryptoKey> {
  if (seed.length !== 32) throw new E2EError("An X25519 private key is 32 bytes.");
  const der = concat(PKCS8_HEAD, seed);
  try {
    return await subtle().importKey("pkcs8", der, X, extractable, ["deriveBits"]);
  } finally {
    zero(der);
  }
}

/** An X25519 private key from its PKCS#8 bytes, never extractable. */
export function privateFromDer(der: Uint8Array): Promise<CryptoKey> {
  return subtle().importKey("pkcs8", der as Bytes, X, false, ["deriveBits"]);
}

/** A new X25519 key pair: the private key extractable only when it must be wrapped (and then only until it is). */
export async function newPair(extractable = true): Promise<{ priv: CryptoKey; pub: Bytes }> {
  const kp = (await subtle().generateKey(X, extractable, ["deriveBits"])) as CryptoKeyPair;
  return { priv: kp.privateKey, pub: new Uint8Array(await subtle().exportKey("raw", kp.publicKey)) };
}

/** X25519 of a private and a public key, as an HKDF key; an all-zero result (a public key of low order) is refused. */
async function agree(priv: CryptoKey, pub: Uint8Array): Promise<CryptoKey> {
  let bits: Bytes;
  try {
    bits = new Uint8Array(await subtle().deriveBits({ name: "X25519", public: await x25519Public(pub) }, priv, 256));
  } catch {
    throw new E2EError(LOW_ORDER);       // WebCrypto refuses an all-zero result itself
  }
  try {
    if (bits.every((b) => b === 0)) throw new E2EError(LOW_ORDER);
    return await hkdfKey(bits);
  } finally {
    zero(bits);
  }
}

/** The first 16 bytes of the sha256 of a public key: what names it in an envelope's header. */
export async function keyIdBytes(pub: Uint8Array): Promise<Bytes> {
  return new Uint8Array(await subtle().digest("SHA-256", pub as Bytes)).slice(0, 16);
}

/** A key's key_id: keyIdBytes as 32 lowercase hex. */
export async function keyIdOf(pub: Uint8Array): Promise<string> {
  return hex(await keyIdBytes(pub));
}

export async function sha256(bytes: Uint8Array): Promise<Bytes> {
  return new Uint8Array(await subtle().digest("SHA-256", bytes as Bytes));
}

// ---- AES-256-GCM, kept as nonce || ciphertext || tag ------------------------------------------------------------

export async function gcmSeal(key: CryptoKey, plaintext: Uint8Array, aad: Uint8Array, nonce: Uint8Array = random(NONCE_LEN)): Promise<Bytes> {
  const ct = await subtle().encrypt({ name: "AES-GCM", iv: nonce as Bytes, additionalData: aad as Bytes }, key, plaintext as Bytes);
  return concat(nonce, new Uint8Array(ct));
}

export async function gcmOpen(key: CryptoKey, blob: Uint8Array | null, aad: Uint8Array): Promise<Bytes> {
  if (!blob || blob.length < NONCE_LEN + TAG_LEN) throw new E2EError(DOES_NOT_OPEN);
  try {
    return new Uint8Array(await subtle().decrypt({ name: "AES-GCM", iv: blob.subarray(0, NONCE_LEN) as Bytes, additionalData: aad as Bytes },
      key, blob.subarray(NONCE_LEN) as Bytes));
  } catch {
    throw new E2EError(DOES_NOT_OPEN);
  }
}

/** An X25519 private key wrapped as its PKCS#8 bytes, inside WebCrypto: its bytes never reach the page. */
export async function gcmWrapPrivate(key: CryptoKey, priv: CryptoKey, aad: Uint8Array, nonce: Uint8Array = random(NONCE_LEN)): Promise<Bytes> {
  const ct = await subtle().wrapKey("pkcs8", priv, key, { name: "AES-GCM", iv: nonce as Bytes, additionalData: aad as Bytes });
  return concat(nonce, new Uint8Array(ct));
}

export async function gcmUnwrapPrivate(key: CryptoKey, blob: Uint8Array | null, aad: Uint8Array, extractable = false): Promise<CryptoKey> {
  if (!blob || blob.length < NONCE_LEN + TAG_LEN) throw new E2EError(DOES_NOT_OPEN);
  try {
    return await subtle().unwrapKey("pkcs8", blob.subarray(NONCE_LEN) as Bytes, key,
      { name: "AES-GCM", iv: blob.subarray(0, NONCE_LEN) as Bytes, additionalData: aad as Bytes }, X, extractable, ["deriveBits"]);
  } catch {
    throw new E2EError(DOES_NOT_OPEN);
  }
}

/** A private key that must be wrapped later, under a key not made yet (a passkey's PRF output, at its touch), held until
 *  then only wrapped inside WebCrypto under a fresh AES key that cannot be extracted: no extractable key is kept while
 *  the page waits. `release` gives it back, extractable, once, for that wrap. */
export async function holdPrivate(priv: CryptoKey): Promise<{ release(): Promise<CryptoKey> }> {
  const key = (await subtle().generateKey({ name: "AES-GCM", length: 256 }, false, ["wrapKey", "unwrapKey"])) as CryptoKey;
  const aad = utf8(`${V} held key`);
  let blob: Bytes | null = await gcmWrapPrivate(key, priv, aad);
  return {
    release: () => {
      const held = blob;
      blob = null;
      if (!held) return Promise.reject(new E2EError("That key was given back already."));
      return gcmUnwrapPrivate(key, held, aad, true);
    },
  };
}

// ---- the envelope -------------------------------------------------------------------------------------------------

const FORM = /^e2e1\.[A-Za-z0-9_-]+$/;

/** An envelope's bytes, when `text` has exactly the form of one the page reads (envelopeForm): the prefix, then
 *  base64url without padding that reads one way only (its length not 1 mod 4, and its bytes encoding back to it
 *  exactly), of form 1, 2 or 3, long enough for that form's header and tag. Null for anything else: a text that begins
 *  e2e1. and is not this is refused, never shown as text. */
export function envelopeBytes(text: unknown): Bytes | null {
  return envelopeForm(text)?.body ?? null;
}

/** The key_id an envelope names, as 32 hex (a content key's, or for form 3 its conversation key's), or null when it is
 *  not an envelope. */
export function envelopeKeyId(text: unknown): string | null {
  const body = envelopeBytes(text);
  return body ? hex(body.subarray(1, 17)) : null;
}

/** An envelope's fixed ephemeral key and nonce: only the test vectors give them. */
export interface Fixed { ephemeral: CryptoKey; ephemeralPublic: Uint8Array; nonce: Uint8Array }

function contentKey(shared: CryptoKey, epk: Uint8Array, recipient: Uint8Array, usages: KeyUsage[]): Promise<CryptoKey> {
  return hkdfAes(shared, `${V} content`, usages, concat(epk, recipient));
}

/** `plaintext` sealed to the X25519 public key `recipient`, bound to `aadText` (a text's slot, or a grant's AAD):
 *  "e2e1." and the base64url of header || ciphertext || tag. */
export async function sealEnvelope(recipient: Uint8Array, plaintext: Uint8Array, aadText: string, fixed?: Fixed): Promise<string> {
  const eph = fixed ? { priv: fixed.ephemeral, pub: fixed.ephemeralPublic } : await newPair(false);
  const nonce = fixed?.nonce ?? random(NONCE_LEN);
  const header = concat(Uint8Array.of(1), await keyIdBytes(recipient), eph.pub, nonce);
  const k = await contentKey(await agree(eph.priv, recipient), eph.pub, recipient, ["encrypt"]);
  const ct = await subtle().encrypt({ name: "AES-GCM", iv: nonce as Bytes, additionalData: concat(header, utf8(aadText)) }, k, plaintext as Bytes);
  return PREFIX + b64u(concat(header, new Uint8Array(ct)));
}

/** The plaintext of an envelope sealed to `recipient` (whose private key is `priv`) and bound to `aadText`; E2EError
 *  when it is not an envelope, names another key, or does not open (another slot, a changed byte). */
export async function openEnvelope(priv: CryptoKey, recipient: Uint8Array, text: unknown, aadText: string): Promise<Bytes> {
  const read = envelopeForm(text);
  if (!read) throw new E2EError(NOT_ENVELOPE);
  // a classical key opens form 1 alone: a hybrid record is never tried with X25519
  if (read.form !== 1) throw new E2EError(OTHER_FORM);
  const body = read.body;
  const header = body.subarray(0, HEADER_LEN);
  if (!same(header.subarray(1, 17), await keyIdBytes(recipient))) throw new E2EError(OTHER_KEY);
  const epk = header.subarray(17, 49);
  const k = await contentKey(await agree(priv, epk), epk, recipient, ["decrypt"]);
  try {
    return new Uint8Array(await subtle().decrypt({ name: "AES-GCM", iv: header.subarray(49, 61) as Bytes, additionalData: concat(header, utf8(aadText)) },
      k, body.subarray(HEADER_LEN) as Bytes));
  } catch {
    throw new E2EError(DOES_NOT_OPEN);
  }
}

const STRICT_UTF8 = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

/** A plaintext's JSON object: UTF-8 read strictly (a bad sequence fails, a byte order mark is kept and so fails the
 *  JSON), parsed, an object, and written in exactly its RFC 8785 form, as the link and the Python side write and read
 *  it (so a key given twice, which JSON.parse would read as its last, is refused); null for anything else. */
export function plaintextObject(bytes: Uint8Array): Record<string, unknown> | null {
  let text: string;
  let value: unknown;
  try {
    text = STRICT_UTF8.decode(bytes);
    value = JSON.parse(text);
    if (value === null || typeof value !== "object" || Array.isArray(value) || canonicalJson(value) !== text) return null;
  } catch {
    return null;
  }
  return value as Record<string, unknown>;
}

/** An object as the RFC 8785 bytes a plaintext is (passkey.canonicalJson). */
export const canonicalBytes = (value: unknown): Bytes => utf8(canonicalJson(value));

// ---- the account's keys -------------------------------------------------------------------------------------------

/** The texts each key and its AAD are bound to, with the account. */
export const INFO = {
  credential: (account: string, credentialId: string) => `${V} credential|${account}|${credentialId}`,
  recovery: (account: string) => `${V} recovery|${account}`,
  recoveryAnchor: (account: string) => `${V} recovery anchor|${account}`,
  mkId: (account: string) => `${V} mk_id|${account}`,
  subkey: (purpose: Purpose, account: string) => `${V} ${purpose}|${account}`,
  recoveryProof: `${V} recovery proof`,
};
export const AAD = {
  credential: (account: string, credentialId: string) => `${V} credential|${account}|${credentialId}`,
  grant: (account: string, to: string, mkId: string) => `${V}|${account}|grant|${to}|${mkId}`,
  chain: (account: string, newer: string, older: string) => `${V} chain|${account}|${newer}|${older}`,
  roster: (account: string, mkId: string) => `${V} roster|${account}|${mkId}`,
  contentKey: (account: string, keyId: string, mkId: string) => `${V} content key|${account}|${keyId}|${mkId}`,
  recoveryAnchor: (account: string) => `${V} recovery anchor|${account}`,
};

export type Purpose = "content-wrap" | "chain" | "roster";

/** A master key held: its HKDF key (never extractable) and its mk_id, which names it and commits to it. */
export interface Master { key: CryptoKey; id: string }

/** The master key over its 32 bytes, with its mk_id: HKDF(MK, "mk_id|" + account), 16 bytes, as 32 hex. */
export async function masterOf(bytes: Uint8Array, account: string): Promise<Master> {
  if (bytes.length !== 32) throw new E2EError("A master key is 32 bytes.");
  const key = await hkdfKey(bytes);
  return { key, id: hex(await hkdfBytes(key, INFO.mkId(account), 16)) };
}

/** One of the master key's subkeys: it is used only through these. */
export function subkey(m: Master, purpose: Purpose, account: string, usages: KeyUsage[]): Promise<CryptoKey> {
  return hkdfAes(m.key, INFO.subkey(purpose, account), usages);
}

// a credential's own wrap: {"anchor": mk_id, "ck": base64url of ck's PKCS#8}, in RFC 8785, written and read as bytes
const WRAP_HEAD = utf8('{"anchor":"');
const WRAP_MIDDLE = utf8('","ck":"');
const WRAP_TAIL = utf8('"}');
const MK_ID = /^[0-9a-f]{32}$/;

/** A credential wrap's plaintext, made as bytes so the key is never a string. */
export function credentialPlaintext(anchor: string, der: Uint8Array): Bytes {
  if (!MK_ID.test(anchor)) throw new E2EError("An anchor is an mk_id: 32 lowercase hex.");
  const key = b64uBytes(der);
  try {
    return concat(WRAP_HEAD, utf8(anchor), WRAP_MIDDLE, key, WRAP_TAIL);
  } finally {
    zero(key);
  }
}

/** The anchor and the PKCS#8 bytes of a credential wrap's plaintext, read as bytes in exactly the form it is written;
 *  null for anything else. */
export function readCredentialPlaintext(plain: Uint8Array): { anchor: string; der: Bytes } | null {
  const keyAt = WRAP_HEAD.length + 32 + WRAP_MIDDLE.length;
  const end = plain.length - WRAP_TAIL.length;
  if (end <= keyAt || !same(plain.subarray(0, WRAP_HEAD.length), WRAP_HEAD)
      || !same(plain.subarray(WRAP_HEAD.length + 32, keyAt), WRAP_MIDDLE) || !same(plain.subarray(end), WRAP_TAIL)) return null;
  const anchor = ascii.decode(plain.subarray(WRAP_HEAD.length, WRAP_HEAD.length + 32));
  if (!MK_ID.test(anchor)) return null;
  const der = fromB64uBytes(plain.subarray(keyAt, end));
  return der ? { anchor, der } : null;
}

/** The key that opens a credential's own wrap: HKDF of its PRF output. The PRF output is zeroed once imported. */
export async function credentialKek(prf: Uint8Array, account: string, credentialId: string): Promise<CryptoKey> {
  let ikm: CryptoKey;
  try {
    ikm = await hkdfKey(prf);
  } finally {
    zero(prf);
  }
  return hkdfAes(ikm, INFO.credential(account, credentialId), ["encrypt", "decrypt"]);
}

/** A credential's own wrap of its private key `ck` (which must be extractable for this alone) and its anchor. */
export async function sealCredential(kek: CryptoKey, ck: CryptoKey, anchor: string, account: string, credentialId: string,
                                     nonce?: Uint8Array): Promise<string> {
  const der = new Uint8Array(await subtle().exportKey("pkcs8", ck));
  return sealCredentialDer(kek, der, anchor, account, credentialId, nonce);
}

/** The same, from the key's PKCS#8 bytes, which are zeroed. */
export async function sealCredentialDer(kek: CryptoKey, der: Uint8Array, anchor: string, account: string, credentialId: string,
                                        nonce?: Uint8Array): Promise<string> {
  let plain: Bytes | null = null;
  try {
    plain = credentialPlaintext(anchor, der);
    return b64u(await gcmSeal(kek, plain, utf8(AAD.credential(account, credentialId)), nonce));
  } finally {
    zero(der, plain);
  }
}

/** A credential's wrap opened: its anchor and the PKCS#8 bytes of its key, which the caller zeroes. */
export async function openCredentialDer(kek: CryptoKey, wrapped: string, account: string, credentialId: string): Promise<{ anchor: string; der: Bytes }> {
  const plain = await gcmOpen(kek, unb64u(wrapped), utf8(AAD.credential(account, credentialId)));
  try {
    const read = readCredentialPlaintext(plain);
    if (!read) throw new E2EError("This key's own record is not in the form the page writes.");
    return read;
  } finally {
    zero(plain);
  }
}

/** A credential's wrap opened: its private key (never extractable) and its anchor. */
export async function openCredential(kek: CryptoKey, wrapped: string, account: string, credentialId: string): Promise<{ ck: CryptoKey; anchor: string }> {
  const { anchor, der } = await openCredentialDer(kek, wrapped, account, credentialId);
  try {
    return { ck: await privateFromDer(der), anchor };
  } catch {
    throw new E2EError("This key's own record holds no X25519 key.");
  } finally {
    zero(der);
  }
}

/** A grant: the master key's 32 bytes sealed to a credential's key or the recovery key (`to`: the credential id, or
 *  "recovery"), with the envelope's construction and the grant's AAD. Its ephemeral key and nonce are not drawn at
 *  random but derived from the master key it seals, its AAD and the recipient's key (grantEphemeral), so whoever holds
 *  that master key seals the same grant again byte for byte (isGrantOf), and nobody else learns anything from it. Only
 *  the test vectors give `fixed`. A grant's form is its recipient's key's: form 1 to an X25519 key, form 2 to a
 *  post-quantum one (grantEseed), never chosen otherwise. */
export async function sealGrant(recipient: Uint8Array, mkBytes: Uint8Array, account: string, to: string, mkId: string,
                                fixed?: Fixed | Fixed2): Promise<string> {
  const aad = AAD.grant(account, to, mkId);
  if (recipient.length === HYBRID_KEY_LEN) {
    if (fixed && !("eseed" in fixed)) throw new E2EError("A post-quantum grant's fixed randomness is an eseed and a nonce.");
    const derived = (fixed as Fixed2 | undefined) ?? await grantEseed(recipient, mkBytes, aad);
    try {
      return await sealEnvelope2(recipient, mkBytes, aad, derived);
    } finally {
      if (!fixed) zero(derived.eseed, derived.nonce);
    }
  }
  if (fixed && "eseed" in fixed) throw new E2EError("A grant to an X25519 key takes an X25519 ephemeral key.");
  return sealEnvelope(recipient, mkBytes, aad, (fixed as Fixed | undefined) ?? await grantEphemeral(recipient, mkBytes, aad));
}

/** A grant's ephemeral key and nonce: HKDF of the master key's bytes, salted with the recipient's key, for the grant's
 *  AAD; 32 bytes of X25519 private key, then the 12 of the nonce. The master key is fresh for each master key made, so
 *  no two grants share them unless they are the same grant. */
async function grantEphemeral(recipient: Uint8Array, mkBytes: Uint8Array, aad: string): Promise<Fixed> {
  const seed = await hkdfBytes(await hkdfKey(mkBytes), `${V} grant ephemeral|${aad}`, 32 + NONCE_LEN, recipient);
  try {
    const ephemeral = await privateFromSeed(seed.subarray(0, 32));
    return { ephemeral, ephemeralPublic: await publicOf(ephemeral), nonce: seed.slice(32) };
  } finally {
    zero(seed);
  }
}

/** Whether `sealed` is exactly the grant of these master key bytes to `recipient` for `to`, as sealGrant seals it: the
 *  check of a grant to a key whose private half is not held, by one who holds the master key it seals. */
export async function isGrantOf(sealed: unknown, recipient: Uint8Array, mkBytes: Uint8Array, account: string, to: string, mkId: string): Promise<boolean> {
  return sealed === await sealGrant(recipient, mkBytes, account, to, mkId);
}

export function openGrant(priv: CryptoKey, recipient: Uint8Array, sealed: string, account: string, to: string, mkId: string): Promise<Bytes> {
  return openEnvelope(priv, recipient, sealed, AAD.grant(account, to, mkId));
}

/** A link of the chain: the older master key's bytes wrapped under the chain subkey of the newer. */
export async function sealChain(newer: Master, olderBytes: Uint8Array, olderId: string, account: string, nonce?: Uint8Array): Promise<string> {
  const k = await subkey(newer, "chain", account, ["encrypt"]);
  return b64u(await gcmSeal(k, olderBytes, utf8(AAD.chain(account, newer.id, olderId)), nonce));
}

/** The older master key's bytes from a link of the chain, which the caller zeroes. */
export async function openChain(newer: Master, wrapped: string, olderId: string, account: string): Promise<Bytes> {
  const k = await subkey(newer, "chain", account, ["decrypt"]);
  return gcmOpen(k, unb64u(wrapped), utf8(AAD.chain(account, newer.id, olderId)));
}

/** The roster: the keys a new master key may be sealed to. Public keys, in base64url. */
export interface Roster {
  credentials: { credential_id: string; passkey_id: string; public_key: string }[];
  recovery: string;
}

export async function sealRoster(m: Master, roster: Roster, account: string, nonce?: Uint8Array): Promise<string> {
  const k = await subkey(m, "roster", account, ["encrypt"]);
  const plain = canonicalBytes({
    credentials: roster.credentials.map((c) => ({ credential_id: c.credential_id, passkey_id: c.passkey_id, public_key: c.public_key })),
    recovery: roster.recovery,
  });
  return b64u(await gcmSeal(k, plain, utf8(AAD.roster(account, m.id)), nonce));
}

const isText = (v: unknown): v is string => typeof v === "string" && v.length > 0;

export async function openRoster(m: Master, sealed: string, account: string): Promise<Roster> {
  const k = await subkey(m, "roster", account, ["decrypt"]);
  const value = plaintextObject(await gcmOpen(k, unb64u(sealed), utf8(AAD.roster(account, m.id))));
  const list = value?.credentials;
  if (!value || !Array.isArray(list) || !isText(value.recovery)
      || !list.every((c) => c && typeof c === "object" && isText(c.credential_id) && isText(c.passkey_id) && isText(c.public_key))) {
    throw new E2EError("The roster is not in the form the page writes.");
  }
  return { credentials: list.map((c) => ({ credential_id: c.credential_id, passkey_id: c.passkey_id, public_key: c.public_key })), recovery: value.recovery };
}

/** A content key's private half wrapped under the content-wrap subkey; `ek` extractable for this alone. */
export async function wrapContentKey(m: Master, ek: CryptoKey, keyId: string, account: string, nonce?: Uint8Array): Promise<string> {
  const k = await subkey(m, "content-wrap", account, ["wrapKey"]);
  return b64u(await gcmWrapPrivate(k, ek, utf8(AAD.contentKey(account, keyId, m.id)), nonce));
}

/** A content key unwrapped: never extractable, but for the moment it is wrapped again under a new master key. */
export async function unwrapContentKey(m: Master, wrapped: string, keyId: string, account: string, extractable = false): Promise<CryptoKey> {
  const k = await subkey(m, "content-wrap", account, ["unwrapKey"]);
  return gcmUnwrapPrivate(k, unb64u(wrapped), utf8(AAD.contentKey(account, keyId, m.id)), extractable);
}

// ---- the recovery code --------------------------------------------------------------------------------------------

const BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
export const CODE_BYTES = 20;

/** The recovery code as it is shown once: its 20 bytes in base32 (RFC 4648, no padding), 32 characters in groups of
 *  four. */
export function recoveryText(bytes: Uint8Array): string {
  if (bytes.length !== CODE_BYTES) throw new E2EError("A recovery code is 20 bytes.");
  let out = "";
  let acc = 0;
  let bits = 0;
  for (const b of bytes) {
    acc = (acc << 8) | b;
    bits += 8;
    while (bits >= 5) { bits -= 5; out += BASE32[(acc >> bits) & 31]; }
    acc &= (1 << bits) - 1;
  }
  return out.match(/.{4}/g)!.join(" ");
}

/** The 20 bytes of a recovery code as typed (spaces, dashes and case do not matter); null for anything else. */
export function recoveryBytes(text: string): Bytes | null {
  const clean = text.replace(/[\s-]+/g, "").toUpperCase();
  if (clean.length !== 32) return null;
  const out = new Uint8Array(CODE_BYTES);
  let acc = 0;
  let bits = 0;
  let o = 0;
  for (const ch of clean) {
    const v = BASE32.indexOf(ch);
    if (v < 0) { zero(out); return null; }
    acc = (acc << 5) | v;
    bits += 5;
    if (bits >= 8) { bits -= 8; out[o++] = (acc >> bits) & 255; }
    acc &= (1 << bits) - 1;
  }
  return out;
}

/** The recovery key pair from the code's 20 bytes: rk = HKDF(code, "recovery|" + account), an X25519 private key that
 *  is never extractable, and its public key RK. */
export async function recoveryKeys(code: Uint8Array, account: string): Promise<{ rk: CryptoKey; RK: Bytes }> {
  const seed = await hkdfBytes(await hkdfKey(code), INFO.recovery(account), 32);
  let rk: CryptoKey;
  try {
    rk = await privateFromSeed(seed);
  } finally {
    zero(seed);
  }
  return { rk, RK: await publicOf(rk) };
}

/** The key the recovery anchor is sealed under: HKDF(code, "recovery anchor|" + account), an AES-256-GCM key that cannot
 *  be extracted. A tab unlocked with the code keeps it, so it renews the code's anchor with nothing typed again. */
export async function recoveryAnchorKey(code: Uint8Array, account: string): Promise<CryptoKey> {
  return hkdfAes(await hkdfKey(code), INFO.recoveryAnchor(account), ["encrypt", "decrypt"]);
}

/** The recovery anchor: the mk_id of the master key when the code was made, or when its anchor was last renewed, sealed
 *  under the code's anchor key (recoveryAnchorKey). */
export async function sealRecoveryAnchorWith(key: CryptoKey, mkId: string, account: string, nonce?: Uint8Array): Promise<string> {
  return b64u(await gcmSeal(key, canonicalBytes({ anchor: mkId }), utf8(AAD.recoveryAnchor(account)), nonce));
}

export async function openRecoveryAnchorWith(key: CryptoKey, sealed: string, account: string): Promise<string> {
  const value = plaintextObject(await gcmOpen(key, unb64u(sealed), utf8(AAD.recoveryAnchor(account))));
  const anchor = value?.anchor;
  if (typeof anchor !== "string" || !MK_ID.test(anchor)) throw new E2EError("The recovery anchor is not in the form the page writes.");
  return anchor;
}

/** The same, from the code's 20 bytes. */
export async function sealRecoveryAnchor(code: Uint8Array, mkId: string, account: string, nonce?: Uint8Array): Promise<string> {
  return sealRecoveryAnchorWith(await recoveryAnchorKey(code, account), mkId, account, nonce);
}

export async function openRecoveryAnchor(code: Uint8Array, sealed: string, account: string): Promise<string> {
  return openRecoveryAnchorWith(await recoveryAnchorKey(code, account), sealed, account);
}

/** The answer to the server's challenge for the recovery code as a confirmation: HMAC-SHA256 under HKDF(X25519(rk, S),
 *  "recovery proof") over the nonce and the purpose, in base64url. */
export async function recoveryProof(rk: CryptoKey, serverKey: Uint8Array, nonce: Uint8Array, purpose: string): Promise<string> {
  const shared = await agree(rk, serverKey);
  const mac = await subtle().deriveKey(hkdf(INFO.recoveryProof), shared, { name: "HMAC", hash: "SHA-256", length: 256 }, false, ["sign"]);
  return b64u(new Uint8Array(await subtle().sign("HMAC", mac, concat(nonce, utf8(purpose)))));
}

// ---- post-quantum keys and envelopes ----------------------------------------
//
// A post-quantum key is X-Wing's (draft-connolly-cfrg-xwing-kem-11): a 32-byte seed, kept only inside wraps, whose
// public key is pk_M (ML-KEM-768, 1,184 bytes) || pk_X (X25519, 32). An envelope sealed to one is form 2; a
// conversation's messages are form 3 under a conversation key that travels in a form-2 envelope. The ML-KEM half is
// bytes the library takes (./mlkem768); the X25519 half stays in WebCrypto, never extractable. A hybrid record opens
// only by the hybrid path: nothing tries X25519 alone when its ML-KEM part fails.

export const V2 = "proofpane-e2e/2";
export const V3 = "proofpane-e2e/3";
export const SEED_LEN = 32;                                          // an X-Wing private key
export const HYBRID_KEY_LEN = Kem.PUBLIC_LEN + 32;                   // 1,216: pk_M, then pk_X
export const HYBRID_CT_LEN = Kem.CIPHERTEXT_LEN + 32;                // 1,120: ct_M, then ct_X
export const HEADER2_LEN = 1 + 16 + HYBRID_CT_LEN + NONCE_LEN;       // 1,149: 0x02, key_id, ct, nonce
export const OVERHEAD2 = HEADER2_LEN + TAG_LEN;                      // 1,165
export const HEADER3_LEN = 1 + 16 + NONCE_LEN;                       // 29: 0x03, key_id, nonce
export const OVERHEAD3 = HEADER3_LEN + TAG_LEN;                      // 45
export const TALK_KEY_LEN = 32;
export const TALK_KEY_SEALED_LEN = OVERHEAD2 + TALK_KEY_LEN;         // 1,197: a conversation key's envelope, exactly
export const WRAP_LEN = NONCE_LEN + SEED_LEN + TAG_LEN;              // 60: each key's wrap in a post-quantum account
const XWING_LABEL = Uint8Array.of(0x5c, 0x2e, 0x2f, 0x2f, 0x5e, 0x5c);   // X-Wing's label: \./ then /^\

export const BAD_HYBRID_KEY = "This is not a post-quantum key the page can seal to.";
export const OTHER_FORM = "It is sealed in a form its key does not take.";
export const OTHER_RECORD = "This key's record holds another key.";

export const INFO2 = {
  content: `${V2} content`,
  grantEphemeral: (aad: string) => `${V2} grant ephemeral|${aad}`,
  recovery: (account: string) => `${V2} recovery|${account}`,
  recoveryProof: `${V2} recovery proof`,
  talkKeyId: `${V3} talk key id`,
  talkMessage: (slot: string) => `${V3} talk message|${slot}`,
};
export const AAD2 = {
  contentKey: (account: string, keyId: string, mkId: string) => `${V2} content key|${account}|${keyId}|${mkId}`,
  contentKeyX25519: (account: string, keyId: string, mkId: string) => `${V2} content key x25519|${account}|${keyId}|${mkId}`,
  credential: (account: string, credentialId: string, anchor: string) => `${V2} credential|${account}|${credentialId}|${anchor}`,
};

const unhex = (text: string): Bytes => Uint8Array.from(text.match(/../g) ?? [], (h) => parseInt(h, 16));

/** X25519 of a private and a public key, as its 32 bytes, which the caller zeroes; an all-zero result (a public key of
 *  low order) is refused, as WebCrypto refuses it itself. */
async function agreeBits(priv: CryptoKey, pub: Uint8Array): Promise<Bytes> {
  let bits: Bytes;
  try {
    bits = new Uint8Array(await subtle().deriveBits({ name: "X25519", public: await x25519Public(pub) }, priv, 256));
  } catch {
    throw new E2EError(LOW_ORDER);
  }
  if (bits.every((b) => b === 0)) throw new E2EError(LOW_ORDER);
  return bits;
}

/** A post-quantum private key held, opaque outside this module. Its parts stay here: the X25519 half a CryptoKey that
 *  cannot be extracted, the ML-KEM-768 half the decapsulation key the library takes, and the public key, made from the
 *  seed by hybridFromSeed, the only maker. So no caller can hand it another pk_X, or open with its X25519 half alone.
 *  hybridPublic gives a copy of its public key; forgetHybrid zeroes its ML-KEM half and puts it away (at each lock),
 *  after which every use of it refuses. */
export type Hybrid = { readonly hybrid: true };

interface HybridParts { x: CryptoKey; dk: Bytes; pub: Bytes }
const HELD = new WeakMap<Hybrid, HybridParts>();
export const PUT_AWAY = "This key is no longer held here: unlock again.";

function held(key: Hybrid): HybridParts {
  const parts = HELD.get(key);
  if (!parts) throw new E2EError(PUT_AWAY);
  return parts;
}

/** A post-quantum key pair from its 32-byte seed, as X-Wing makes one: SHAKE256(seed, 96 bytes) gives ML-KEM-768's
 *  d || z (64), then the X25519 private key (32). What the seed expands to is zeroed, and the decapsulation key too if
 *  making the key fails; the seed is the caller's. */
export async function hybridFromSeed(seed: Uint8Array): Promise<Hybrid> {
  if (seed.length !== SEED_LEN) throw new E2EError("A post-quantum private key is a 32-byte seed.");
  const expanded = Kem.shake(seed, 96);
  let dk: Bytes | null = null;
  try {
    let pkM: Bytes;
    [pkM, dk] = await Kem.keysFromSeed(expanded.subarray(0, 64));
    const x = await privateFromSeed(expanded.subarray(64));
    const key: Hybrid = Object.freeze({ hybrid: true as const });
    HELD.set(key, { x, dk, pub: concat(pkM, await publicOf(x)) });
    dk = null;                                      // held now, and forgetHybrid zeroes it
    return key;
  } finally {
    zero(expanded, dk);
  }
}

/** A copy of a held key's public key (1,216 bytes). */
export function hybridPublic(key: Hybrid): Bytes {
  return held(key).pub.slice();
}

export function forgetHybrid(key: Hybrid): void {
  const parts = HELD.get(key);
  if (parts) zero(parts.dk);
  HELD.delete(key);
}

/** Whether `pub` is a post-quantum key the page can seal to: 1,216 bytes, its ML-KEM half passing FIPS 203's check
 *  (every coefficient below 3,329), its X25519 half a key a secret can be agreed with. */
export async function usableHybridKey(pub: unknown): Promise<boolean> {
  if (!(pub instanceof Uint8Array) || pub.length !== HYBRID_KEY_LEN || !Kem.validPublic(pub.subarray(0, Kem.PUBLIC_LEN))) return false;
  try {
    zero(await agreeBits((await newPair(false)).priv, pub.subarray(Kem.PUBLIC_LEN)));
    return true;
  } catch {
    return false;
  }
}

/** X-Wing's encapsulation to `pub`: ct = ct_M (1,088 bytes) || ct_X (32), ss = SHA3-256(ss_M || ss_X || ct_X || pk_X ||
 *  its label). `eseed`, 64 bytes, makes it derandomized (a grant, the vectors): its first 32 bytes are ML-KEM's message,
 *  its last 32 the X25519 ephemeral key, as the draft lays them out. The caller zeroes ss. */
export async function xwingEncapsulate(pub: Uint8Array, eseed?: Uint8Array): Promise<{ ct: Bytes; ss: Bytes }> {
  if (pub.length !== HYBRID_KEY_LEN || !Kem.validPublic(pub.subarray(0, Kem.PUBLIC_LEN))) throw new E2EError(BAD_HYBRID_KEY);
  if (eseed !== undefined && eseed.length !== 64) throw new E2EError("An encapsulation's eseed is 64 bytes.");
  const pkX = pub.subarray(Kem.PUBLIC_LEN);
  let eph: CryptoKey;
  let ctX: Bytes;
  if (eseed) {
    eph = await privateFromSeed(eseed.subarray(32));
    ctX = await publicOf(eph);
  } else {
    ({ priv: eph, pub: ctX } = await newPair(false));
  }
  const ssX = await agreeBits(eph, pkX);
  let ctM: Bytes;
  let ssM: Bytes;
  try {
    [ctM, ssM] = await Kem.encapsulate(pub.subarray(0, Kem.PUBLIC_LEN), eseed?.subarray(0, 32));
  } catch {
    zero(ssX);
    throw new E2EError(BAD_HYBRID_KEY);
  }
  const input = concat(ssM, ssX, ctX, pkX, XWING_LABEL);
  try {
    return { ct: concat(ctM, ctX), ss: Kem.sha3(input) };
  } finally {
    zero(ssM, ssX, input);
  }
}

/** X-Wing's decapsulation: the shared secret, with pk_X the key's own (made from its seed). A ciphertext not made for
 *  this key gives another secret, never an error: the AES-GCM tag after it decides. The caller zeroes it. */
export async function xwingDecapsulate(key: Hybrid, ct: Uint8Array): Promise<Bytes> {
  const { x, dk, pub } = held(key);
  if (ct.length !== HYBRID_CT_LEN) throw new E2EError(DOES_NOT_OPEN);
  const ctX = ct.subarray(Kem.CIPHERTEXT_LEN);
  const ssX = await agreeBits(x, ctX);
  let ssM: Bytes;
  try {
    ssM = await Kem.decapsulate(ct.subarray(0, Kem.CIPHERTEXT_LEN), dk);
  } catch {
    zero(ssX);
    throw new E2EError(DOES_NOT_OPEN);
  }
  const input = concat(ssM, ssX, ctX, pub.subarray(Kem.PUBLIC_LEN), XWING_LABEL);
  try {
    return Kem.sha3(input);
  } finally {
    zero(ssM, ssX, input);
  }
}

export type Form = 1 | 2 | 3;
const FORM_OVERHEAD: Record<Form, number> = { 1: OVERHEAD, 2: OVERHEAD2, 3: OVERHEAD3 };

/** An envelope's form and bytes, for each form the page reads (1, 2 and 3): the prefix, then base64url without padding
 *  that reads one way only, long enough for its form's header and tag. Null for anything else, which is refused, never
 *  shown as text. */
export function envelopeForm(text: unknown): { form: Form; body: Bytes } | null {
  if (typeof text !== "string" || !FORM.test(text)) return null;
  const body = unb64u(text.slice(PREFIX.length));
  if (!body) return null;
  const form = body[0];
  if (form !== 1 && form !== 2 && form !== 3) return null;
  return body.length < FORM_OVERHEAD[form] ? null : { form, body };
}

/** A form-2 envelope's fixed randomness: its eseed (64 bytes) and nonce. Only grants (derived from the master key) and
 *  the test vectors give it. */
export interface Fixed2 { eseed: Uint8Array; nonce: Uint8Array }

/** A form-2 envelope's AES key: HKDF-SHA256(ss, salt = sha256(PK), info "proofpane-e2e/2 content"); ss is zeroed. */
async function contentKey2(ss: Bytes, recipient: Uint8Array, usages: KeyUsage[]): Promise<CryptoKey> {
  let ikm: CryptoKey;
  try {
    ikm = await hkdfKey(ss);
  } finally {
    zero(ss);
  }
  return hkdfAes(ikm, INFO2.content, usages, await sha256(recipient));
}

/** `plaintext` sealed to the post-quantum key `recipient` (form 2), bound to `aadText`: "e2e1." and the base64url of
 *  0x02 || key_id || ct || nonce || ciphertext || tag. */
export async function sealEnvelope2(recipient: Uint8Array, plaintext: Uint8Array, aadText: string, fixed?: Fixed2): Promise<string> {
  const nonce = fixed?.nonce ?? random(NONCE_LEN);
  if (nonce.length !== NONCE_LEN) throw new E2EError("An envelope's nonce is 12 bytes.");
  const { ct, ss } = await xwingEncapsulate(recipient, fixed?.eseed);
  const header = concat(Uint8Array.of(2), await keyIdBytes(recipient), ct, nonce);
  const k = await contentKey2(ss, recipient, ["encrypt"]);
  const sealed = await subtle().encrypt({ name: "AES-GCM", iv: nonce as Bytes, additionalData: concat(header, utf8(aadText)) }, k, plaintext as Bytes);
  return PREFIX + b64u(concat(header, new Uint8Array(sealed)));
}

/** The plaintext of a form-2 envelope sealed to `key` and bound to `aadText`; E2EError when it is not an envelope, is of
 *  another form, names another key, or does not open. */
export async function openEnvelope2(key: Hybrid, text: unknown, aadText: string): Promise<Bytes> {
  const { pub } = held(key);
  const read = envelopeForm(text);
  if (!read) throw new E2EError(NOT_ENVELOPE);
  if (read.form !== 2) throw new E2EError(OTHER_FORM);
  const header = read.body.subarray(0, HEADER2_LEN);
  if (!same(header.subarray(1, 17), await keyIdBytes(pub))) throw new E2EError(OTHER_KEY);
  const k = await contentKey2(await xwingDecapsulate(key, header.subarray(17, 17 + HYBRID_CT_LEN)), pub, ["decrypt"]);
  try {
    return new Uint8Array(await subtle().decrypt({ name: "AES-GCM", iv: header.subarray(HEADER2_LEN - NONCE_LEN) as Bytes,
      additionalData: concat(header, utf8(aadText)) }, k, read.body.subarray(HEADER2_LEN) as Bytes));
  } catch {
    throw new E2EError(DOES_NOT_OPEN);
  }
}

/** A post-quantum grant's randomness: 76 bytes of HKDF-SHA256(MK, salt = sha256(PK), info
 *  "proofpane-e2e/2 grant ephemeral|" + the grant's AAD text); the first 64 its eseed, the last 12 its nonce. One for each
 *  master key, recipient and AAD, so no two different grants share one. */
async function grantEseed(recipient: Uint8Array, mkBytes: Uint8Array, aad: string): Promise<Fixed2> {
  const out = await hkdfBytes(await hkdfKey(mkBytes), INFO2.grantEphemeral(aad), 76, await sha256(recipient));
  try {
    return { eseed: out.slice(0, 64), nonce: out.slice(64) };
  } finally {
    zero(out);
  }
}

/** The master key's 32 bytes from a post-quantum grant (form 2) to `key`, which the caller zeroes. */
export async function openGrantHybrid(key: Hybrid, sealed: string, account: string, to: string, mkId: string): Promise<Bytes> {
  const mk = await openEnvelope2(key, sealed, AAD.grant(account, to, mkId));
  if (mk.length !== 32) {
    zero(mk);
    throw new E2EError("A grant holds a master key's 32 bytes.");
  }
  return mk;
}

/** A conversation's key held: an HKDF key over its 32 bytes (never extractable) and its key_id, the first 16 bytes of
 *  HKDF-SHA256(tk, info "proofpane-e2e/3 talk key id") as 32 hex, which names it and commits to it. */
export interface TalkKey { key: CryptoKey; id: string }

/** A conversation's key from its 32 bytes, which the caller zeroes. */
export async function talkKeyOf(bytes: Uint8Array): Promise<TalkKey> {
  if (bytes.length !== TALK_KEY_LEN) throw new E2EError("A conversation's key is 32 bytes.");
  const key = await hkdfKey(bytes);
  return { key, id: hex(await hkdfBytes(key, INFO2.talkKeyId, 16)) };
}

/** A conversation's key sealed to the post-quantum content key `recipient`: a form-2 envelope of its 32 bytes, exactly
 *  1,197 bytes, bound to its talk-key slot (e2e.ts, talkKeySlot). */
export async function sealTalkKey(recipient: Uint8Array, tkBytes: Uint8Array, keySlot: string, fixed?: Fixed2): Promise<string> {
  if (recipient.length !== HYBRID_KEY_LEN) throw new E2EError("A conversation's key is sealed to a post-quantum key alone.");
  if (tkBytes.length !== TALK_KEY_LEN) throw new E2EError("A conversation's key is 32 bytes.");
  return sealEnvelope2(recipient, tkBytes, keySlot, fixed);
}

/** A conversation's key from its envelope: form 2, exactly 1,197 bytes, holding 32 bytes whose key_id is the one the
 *  conversation names beside it. */
export async function openTalkKey(key: Hybrid, sealed: unknown, keySlot: string, keyId: unknown): Promise<TalkKey> {
  const read = envelopeForm(sealed);
  if (!read || read.form !== 2 || read.body.length !== TALK_KEY_SEALED_LEN) throw new E2EError("A conversation's key is not in the form the page reads.");
  const bytes = await openEnvelope2(key, sealed, keySlot);
  try {
    const tk = await talkKeyOf(bytes);
    if (tk.id !== keyId) throw new E2EError("The conversation's key is not the one it names.");
    return tk;
  } finally {
    zero(bytes);
  }
}

/** A message of a conversation (form 3) under its key: AES-256-GCM under HKDF-SHA256(tk, info "proofpane-e2e/3 talk
 *  message|" + its slot), a key of its own for each message, bound to its slot through the AAD as well. */
export async function sealTalkMessage(tk: TalkKey, plaintext: Uint8Array, slot: string, nonce: Uint8Array = random(NONCE_LEN)): Promise<string> {
  if (nonce.length !== NONCE_LEN) throw new E2EError("An envelope's nonce is 12 bytes.");
  const header = concat(Uint8Array.of(3), unhex(tk.id), nonce);
  const k = await hkdfAes(tk.key, INFO2.talkMessage(slot), ["encrypt"]);
  const sealed = await subtle().encrypt({ name: "AES-GCM", iv: nonce as Bytes, additionalData: concat(header, utf8(slot)) }, k, plaintext as Bytes);
  return PREFIX + b64u(concat(header, new Uint8Array(sealed)));
}

/** The plaintext of a conversation's message (form 3) under `tk`, bound to `slot`. */
export async function openTalkMessage(tk: TalkKey, text: unknown, slot: string): Promise<Bytes> {
  const read = envelopeForm(text);
  if (!read) throw new E2EError(NOT_ENVELOPE);
  if (read.form !== 3) throw new E2EError(OTHER_FORM);
  const header = read.body.subarray(0, HEADER3_LEN);
  if (hex(header.subarray(1, 17)) !== tk.id) throw new E2EError(OTHER_KEY);
  const k = await hkdfAes(tk.key, INFO2.talkMessage(slot), ["decrypt"]);
  try {
    return new Uint8Array(await subtle().decrypt({ name: "AES-GCM", iv: header.subarray(17) as Bytes, additionalData: concat(header, utf8(slot)) },
      k, read.body.subarray(HEADER3_LEN) as Bytes));
  } catch {
    throw new E2EError(DOES_NOT_OPEN);
  }
}

/** A post-quantum content key's seed wrapped under the content-wrap subkey: 60 bytes, its AAD naming the account, the
 *  key and the master key. */
export async function wrapContentSeed(m: Master, seed: Uint8Array, keyId: string, account: string, nonce?: Uint8Array): Promise<string> {
  if (seed.length !== SEED_LEN) throw new E2EError("A post-quantum private key is a 32-byte seed.");
  const k = await subkey(m, "content-wrap", account, ["encrypt"]);
  return b64u(await gcmSeal(k, seed, utf8(AAD2.contentKey(account, keyId, m.id)), nonce));
}

/** A post-quantum content key's seed from its wrap (exactly 60 bytes), which the caller zeroes. */
export async function openContentSeed(m: Master, wrapped: string, keyId: string, account: string): Promise<Bytes> {
  const blob = unb64u(wrapped);
  if (!blob || blob.length !== WRAP_LEN) throw new E2EError(DOES_NOT_OPEN);
  const k = await subkey(m, "content-wrap", account, ["decrypt"]);
  return gcmOpen(k, blob, utf8(AAD2.contentKey(account, keyId, m.id)));
}

/** A post-quantum content key unwrapped: its seed expanded, checked to be the key its key_id names, then zeroed. */
export async function unwrapContentSeed(m: Master, wrapped: string, keyId: string, account: string): Promise<Hybrid> {
  const seed = await openContentSeed(m, wrapped, keyId, account);
  try {
    const key = await hybridFromSeed(seed);
    if (await keyIdOf(held(key).pub) !== keyId) {
      forgetHybrid(key);
      throw new E2EError(OTHER_RECORD);
    }
    return key;
  } finally {
    zero(seed);
  }
}

const X25519_ALGORITHM = Uint8Array.of(0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x6e);   // AlgorithmIdentifier: id-X25519

/** An X25519 private key's 32-byte scalar from its PKCS#8 (RFC 5958, RFC 8410), which the caller zeroes: version 0 or
 *  1, id-X25519, the scalar in an OCTET STRING inside the OCTET STRING, and whatever optional fields a browser adds
 *  after it (attributes, the public key), which are not read. A browser's own export is not trusted to have one
 *  layout; the scalar is trusted only once its public key has the key_id the key is known by. How the upgrade wraps a
 *  retired classical content key again. */
export async function scalarFromPkcs8(der: Uint8Array, keyId: string): Promise<Bytes> {
  const shaped = der.length >= 48 && der.length < 130 && der[0] === 0x30 && der[1] === der.length - 2
    && der[2] === 0x02 && der[3] === 0x01 && (der[4] === 0 || der[4] === 1) && same(der.subarray(5, 12), X25519_ALGORITHM)
    && der[12] === 0x04 && der[13] === 0x22 && der[14] === 0x04 && der[15] === 0x20;
  if (!shaped) throw new E2EError("This is not an X25519 private key.");
  const scalar = der.slice(16, 48);
  try {
    if (await keyIdOf(await publicOf(await privateFromSeed(scalar))) !== keyId) throw new E2EError(OTHER_RECORD);
    return scalar;
  } catch (e) {
    zero(scalar);
    throw e;
  }
}

/** The scalar of a classical content key held in WebCrypto (extractable for this alone), read as scalarFromPkcs8
 *  reads it and checked against its key_id; the caller zeroes it. */
export async function scalarOf(priv: CryptoKey, keyId: string): Promise<Bytes> {
  const der = new Uint8Array(await subtle().exportKey("pkcs8", priv));
  try {
    return await scalarFromPkcs8(der, keyId);
  } finally {
    zero(der);
  }
}

/** A retired classical content key's 32-byte X25519 scalar wrapped again under the content-wrap subkey, at the upgrade:
 *  60 bytes, an AAD of its own, kept only to open what was sealed to it. */
export async function wrapClassicalScalar(m: Master, scalar: Uint8Array, keyId: string, account: string, nonce?: Uint8Array): Promise<string> {
  if (scalar.length !== 32) throw new E2EError("An X25519 private key is 32 bytes.");
  const k = await subkey(m, "content-wrap", account, ["encrypt"]);
  return b64u(await gcmSeal(k, scalar, utf8(AAD2.contentKeyX25519(account, keyId, m.id)), nonce));
}

/** A retired classical content key's scalar from its wrap (exactly 60 bytes), checked to be the key its key_id names,
 *  which the caller zeroes: what a later change wraps again under its new master key. */
export async function openClassicalScalar(m: Master, wrapped: string, keyId: string, account: string): Promise<Bytes> {
  const blob = unb64u(wrapped);
  if (!blob || blob.length !== WRAP_LEN) throw new E2EError(DOES_NOT_OPEN);
  const k = await subkey(m, "content-wrap", account, ["decrypt"]);
  const scalar = await gcmOpen(k, blob, utf8(AAD2.contentKeyX25519(account, keyId, m.id)));
  try {
    if (await keyIdOf(await publicOf(await privateFromSeed(scalar))) !== keyId) throw new E2EError(OTHER_RECORD);
    return scalar;
  } catch (e) {
    zero(scalar);
    throw e;
  }
}

/** A retired classical content key from its wrap, as openClassicalScalar opens it: never extractable; its scalar is
 *  zeroed. */
export async function unwrapClassicalScalar(m: Master, wrapped: string, keyId: string, account: string): Promise<CryptoKey> {
  const scalar = await openClassicalScalar(m, wrapped, keyId, account);
  try {
    return await privateFromSeed(scalar);
  } finally {
    zero(scalar);
  }
}

/** A credential's own wrap of its post-quantum key's seed, under its kek (credentialKek, as today): 60 bytes, its AAD
 *  naming the account, the credential and its anchor, which is stored beside the wrap, so a changed anchor fails the tag
 *  and the wrap stays a raw seed. */
export async function sealCredentialSeed(kek: CryptoKey, seed: Uint8Array, anchor: string, account: string, credentialId: string,
                                         nonce?: Uint8Array): Promise<string> {
  if (!MK_ID.test(anchor)) throw new E2EError("An anchor is an mk_id: 32 lowercase hex.");
  if (seed.length !== SEED_LEN) throw new E2EError("A post-quantum private key is a 32-byte seed.");
  return b64u(await gcmSeal(kek, seed, utf8(AAD2.credential(account, credentialId, anchor)), nonce));
}

/** A credential's seed from its wrap (exactly 60 bytes) and the anchor stored beside it, which the caller zeroes. */
export async function openCredentialSeed(kek: CryptoKey, wrapped: string, anchor: string, account: string, credentialId: string): Promise<Bytes> {
  if (!MK_ID.test(anchor)) throw new E2EError("An anchor is an mk_id: 32 lowercase hex.");
  const blob = unb64u(wrapped);
  if (!blob || blob.length !== WRAP_LEN) throw new E2EError(DOES_NOT_OPEN);
  return gcmOpen(kek, blob, utf8(AAD2.credential(account, credentialId, anchor)));
}

/** A credential's post-quantum key from its wrap, as unlock takes it: its seed expanded, and the public
 *  key it makes, both halves, compared byte for byte with the one the roster names; the seed zeroed. */
export async function unwrapCredentialSeed(kek: CryptoKey, wrapped: string, anchor: string, account: string, credentialId: string,
                                           rosterPublic: Uint8Array): Promise<Hybrid> {
  const seed = await openCredentialSeed(kek, wrapped, anchor, account, credentialId);
  try {
    const key = await hybridFromSeed(seed);
    if (!same(held(key).pub, rosterPublic)) {
      forgetHybrid(key);
      throw new E2EError(OTHER_RECORD);
    }
    return key;
  } finally {
    zero(seed);
  }
}

/** The post-quantum recovery key from the code's 20 bytes: its seed is HKDF-SHA256(code, info "proofpane-e2e/2
 *  recovery|" + account), 32 bytes, expanded as any X-Wing key and then zeroed. */
export async function recoveryHybrid(code: Uint8Array, account: string): Promise<Hybrid> {
  const seed = await hkdfBytes(await hkdfKey(code), INFO2.recovery(account), SEED_LEN);
  try {
    return await hybridFromSeed(seed);
  } finally {
    zero(seed);
  }
}

/** The answer to the server's challenge for the recovery code when the recovery key is post-quantum: the
 *  server encapsulated to RK (server_ct); HMAC-SHA256 under HKDF-SHA256(ss, info "proofpane-e2e/2 recovery proof") over
 *  the nonce and the purpose, in base64url. */
export async function recoveryProofHybrid(rk: Hybrid, serverCt: Uint8Array, nonce: Uint8Array, purpose: string): Promise<string> {
  const ss = await xwingDecapsulate(rk, serverCt);
  let ikm: CryptoKey;
  try {
    ikm = await hkdfKey(ss);
  } finally {
    zero(ss);
  }
  const mac = await subtle().deriveKey(hkdf(INFO2.recoveryProof), ikm, { name: "HMAC", hash: "SHA-256", length: 256 }, false, ["sign"]);
  return b64u(new Uint8Array(await subtle().sign("HMAC", mac, concat(nonce, utf8(purpose)))));
}
