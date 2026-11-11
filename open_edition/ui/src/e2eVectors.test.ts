import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import * as N from "node:crypto";
import { describe, expect, it } from "vitest";
import * as C from "./e2eCrypto";
import * as K from "./e2eKeys";
import { eventSlot, historySlot, talkKeySlot, talkSlot } from "./e2e";
import { canonicalJson } from "./passkey";

// The page's own vectors (tests/vectors/e2e_v1_page.json), for the Python side to open: envelopes, grants, a chain
// link, a roster, a content key's wrap, a credential's own wrap and the recovery key, sealed here from fixed keys and
// nonces. This test makes them again and holds the committed file to them, so the file is what the page writes now.
// E2E_PRINT_VECTORS=1 prints them, for the file.
const FILE = join(__dirname, "../../tests/vectors/e2e_v1_page.json");
const ACCOUNT = "acct-e2e-page";
const LINK = "fedcba9876543210fedcba9876543210";

const fixed = (label: string, n = 32) => new Uint8Array(N.createHash("sha256").update(`proofpane-e2e/1 page vectors: ${label}`).digest()).slice(0, n);
const clientId = (n: number) => `${"0".repeat(28)}${(0xe000 + n).toString(16)}`;

async function envelopeOf(EK: Uint8Array, i: number, name: string, slot: string, plaintext: Record<string, unknown>) {
  const ephemeral = fixed(`ephemeral ${i}`);
  const nonce = fixed(`nonce ${i}`, 12);
  const key = await C.privateFromSeed(ephemeral);
  const text = canonicalJson(plaintext);
  return { name, slot, plaintext, plaintext_text: text, ephemeral_private: C.b64u(ephemeral), nonce: C.b64u(nonce),
    envelope: await C.sealEnvelope(EK, C.utf8(text), slot, { ephemeral: key, ephemeralPublic: await C.publicOf(key), nonce }) };
}

async function grantOf(label: string, to: string, recipientSeed: Uint8Array, mk: Uint8Array, mkId: string) {
  const recipient = await C.publicOf(await C.privateFromSeed(recipientSeed));
  const ephemeral = fixed(`${label} ephemeral`);
  const nonce = fixed(`${label} nonce`, 12);
  const key = await C.privateFromSeed(ephemeral);
  return { to, mk: C.b64u(mk), mk_id: mkId, private: C.b64u(recipientSeed), public: C.b64u(recipient), slot: C.AAD.grant(ACCOUNT, to, mkId),
    ephemeral_private: C.b64u(ephemeral), nonce: C.b64u(nonce),
    envelope: await C.sealGrant(recipient, mk, ACCOUNT, to, mkId, { ephemeral: key, ephemeralPublic: await C.publicOf(key), nonce }) };
}

const split = (stored: string) => { const b = C.unb64u(stored)!; return { nonce: C.b64u(b.subarray(0, 12)), wrapped: C.b64u(b.subarray(12)) }; };

export async function pageVectors() {
  const contentSeed = fixed("content key");
  const ek = await C.privateFromSeed(contentSeed, true);
  const EK = await C.publicOf(ek);
  const keyId = await C.keyIdOf(EK);
  const pkcs8 = new Uint8Array(await crypto.subtle.exportKey("pkcs8", ek));
  const cases: [string, string, Record<string, unknown>][] = [
    ["event: a step to the owner", eventSlot(ACCOUNT, LINK, "step", null, null, clientId(1)), { said_at: null, session: null, text: "Sealed by the page." }],
    ["event: a question to the owner", eventSlot(ACCOUNT, LINK, "question", null, null, clientId(2)), { said_at: null, session: null, text: "Per user, or per org?" }],
    ["event: a reply to the command it took, with the command's seq", eventSlot(ACCOUNT, LINK, "done", null, 77, clientId(3)), { said_at: null, session: null, text: "done: 4 passed" }],
    ["event: a mirrored note of the session's words, naming its session", eventSlot(ACCOUNT, LINK, "note", "session", null, clientId(4)), { said_at: 1790000000456, session: "s-0042", text: "(2 of 2) Checked." }],
    ["event: text in other scripts, and what JSON escapes", eventSlot(ACCOUNT, LINK, "note", null, null, clientId(5)),
      { said_at: null, session: null, text: "été λ \u{1f600} \"q\" \\ tab\there\nnext\u0001\u007f end" }],
    ["history: a listed row", historySlot(ACCOUNT, LINK, "s-0042", false), { branch: "dev", project: "~/work/page", repositories: ["~/work/page"], title: "The Encryption pane" }],
    ["history: a withheld row, its project alone", historySlot(ACCOUNT, LINK, "s-0043", true), { project: "~/private" }],
    ["talk: the owner's message, second of two, one before them", talkSlot(ACCOUNT, LINK, "s-0042", 1, 2, 1, "owner", 1790000002, 1790000001500), { text: "and the roster?" }],
    ["talk: the session's message, with no times", talkSlot(ACCOUNT, LINK, "s-0042", 0, 2, 1, "session", null, null), { text: "It names each key." }],
  ];
  const envelopes = [];
  for (const [i, [name, slot, plaintext]] of cases.entries()) envelopes.push(await envelopeOf(EK, i, name, slot, plaintext));

  const mk = fixed("master key 1");
  const master = await C.masterOf(mk, ACCOUNT);
  const mk2 = fixed("master key 2");
  const next = await C.masterOf(mk2, ACCOUNT);
  const credentialSeed = fixed("credential key");
  const credentialId = "cred-page-0001";
  const code = fixed("recovery code", 20);
  const rkSeed = await C.hkdfBytes(await C.hkdfKey(code), C.INFO.recovery(ACCOUNT), 32);

  const chainStored = await C.sealChain(next, mk, master.id, ACCOUNT, fixed("chain nonce", 12));
  const credential = await C.publicOf(await C.privateFromSeed(credentialSeed));
  const roster: C.Roster = { credentials: [{ credential_id: credentialId, passkey_id: "passkey-0001", public_key: C.b64u(credential) }],
    recovery: C.b64u(await C.publicOf(await C.privateFromSeed(rkSeed))) };
  const rosterStored = await C.sealRoster(next, roster, ACCOUNT, fixed("roster nonce", 12));
  const wrapStored = await C.wrapContentKey(next, ek, keyId, ACCOUNT, fixed("content key wrap nonce", 12));
  const prf = fixed("credential prf output");
  const ckDer = C.concat(Uint8Array.of(0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x6e, 0x04, 0x22, 0x04, 0x20), credentialSeed);
  const credentialStored = await C.sealCredentialDer(await C.credentialKek(prf.slice(), ACCOUNT, credentialId), ckDer.slice(), master.id, ACCOUNT,
    credentialId, fixed("credential wrap nonce", 12));
  const anchorStored = await C.sealRecoveryAnchor(code, master.id, ACCOUNT, fixed("recovery anchor nonce", 12));
  const sub = async (m: C.Master, p: C.Purpose) => C.b64u(await C.hkdfBytes(m.key, C.INFO.subkey(p, ACCOUNT), 32));

  return {
    about: "End-to-end encryption: what the page (ui/src/e2eCrypto.ts, WebCrypto) seals from fixed keys and nonces, for the Python side to open, made by ui/src/e2eVectors.test.ts, which holds this file to what the page writes. Keys are base64url without padding; private keys raw (32 bytes) and, for the content key, as PKCS#8 DER too; plaintext_text is the plaintext's RFC 8785 bytes as text. Envelopes and grants are in the shape of e2e_v1.json. Every AES-256-GCM blob is stored as nonce (12 bytes), ciphertext and tag (nonce_and_wrapped), with its key a subkey of a master key, HKDF-SHA256(MK, salt empty, info \"proofpane-e2e/1 \" + purpose + \"|\" + account), given as subkey: the chain link wraps the previous master key's 32 bytes under the chain subkey of the next; the roster is its plaintext under the roster subkey; the content key's wrap is its PKCS#8 under the content-wrap subkey. The credential's own wrap is under HKDF-SHA256(the PRF output, salt empty, info credential_info), its plaintext {anchor, ck} with ck the base64url of the key's PKCS#8. The recovery key is HKDF-SHA256(the code's 20 bytes, salt empty, info \"proofpane-e2e/1 recovery|\" + account); its anchor is under HKDF-SHA256(the code, salt empty, info \"proofpane-e2e/1 recovery anchor|\" + account), its AAD that same text.",
    version: 1,
    account: ACCOUNT,
    link_id: LINK,
    content_key: { private: C.b64u(contentSeed), pkcs8: C.b64u(pkcs8), public: C.b64u(EK), key_id: keyId },
    envelopes,
    grant: await grantOf("grant", credentialId, credentialSeed, mk, master.id),
    recovery_grant: await grantOf("recovery grant", "recovery", rkSeed, mk, master.id),
    chain: { mk: C.b64u(mk2), mk_id: next.id, previous_mk: C.b64u(mk), previous: master.id, subkey: await sub(next, "chain"),
      aad: C.AAD.chain(ACCOUNT, next.id, master.id), ...split(chainStored), nonce_and_wrapped: chainStored },
    roster: { mk: C.b64u(mk2), mk_id: next.id, subkey: await sub(next, "roster"), aad: C.AAD.roster(ACCOUNT, next.id), plaintext: roster,
      plaintext_text: canonicalJson(roster), ...split(rosterStored), nonce_and_wrapped: rosterStored },
    content_key_wrap: { mk: C.b64u(mk2), mk_id: next.id, subkey: await sub(next, "content-wrap"), aad: C.AAD.contentKey(ACCOUNT, keyId, next.id),
      plaintext_pkcs8: C.b64u(pkcs8), ...split(wrapStored), nonce_and_wrapped: wrapStored },
    credential_wrap: { credential_id: credentialId, prf: C.b64u(prf), credential_info: C.INFO.credential(ACCOUNT, credentialId),
      aad: C.AAD.credential(ACCOUNT, credentialId), anchor: master.id, private: C.b64u(credentialSeed), pkcs8: C.b64u(ckDer),
      plaintext_text: `{"anchor":"${master.id}","ck":"${C.b64u(ckDer)}"}`, ...split(credentialStored), nonce_and_wrapped: credentialStored },
    recovery: { code: C.recoveryText(code), code_bytes: C.b64u(code), private: C.b64u(rkSeed),
      public: roster.recovery, anchor: { mk_id: master.id, aad: C.AAD.recoveryAnchor(ACCOUNT), plaintext_text: `{"anchor":"${master.id}"}`,
        ...split(anchorStored), nonce_and_wrapped: anchorStored } },
  };
}

// ---- an implementation of its own, in Node's crypto, standing for the Python side's -------------------------------
const PKCS8_HEAD = Buffer.from("302e020100300506032b656e04220420", "hex");
const SPKI_HEAD = Buffer.from("302a300506032b656e032100", "hex");
const nodePriv = (seed: Buffer) => N.createPrivateKey({ key: Buffer.concat([PKCS8_HEAD, seed]), format: "der", type: "pkcs8" });
const nodePub = (pub: Buffer) => N.createPublicKey({ key: Buffer.concat([SPKI_HEAD, pub]), format: "der", type: "spki" });
const b = (t: string) => Buffer.from(t, "base64url");
const hkdf = (ikm: Buffer, info: string, n = 32, salt = Buffer.alloc(0)) => Buffer.from(N.hkdfSync("sha256", ikm, salt, info, n));

function gcmOpen(key: Buffer, stored: string, aad: string): Buffer {
  const blob = b(stored);
  const d = N.createDecipheriv("aes-256-gcm", key, blob.subarray(0, 12));
  d.setAAD(Buffer.from(aad, "utf8"));
  d.setAuthTag(blob.subarray(blob.length - 16));
  return Buffer.concat([d.update(blob.subarray(12, blob.length - 16)), d.final()]);
}

function envelopeOpen(seed: Buffer, recipient: Buffer, envelope: string, aad: string): Buffer {
  const body = b(envelope.slice(5));
  const header = body.subarray(0, 61);
  const epk = header.subarray(17, 49);
  expect(header[0]).toBe(1);
  expect(header.subarray(1, 17).equals(N.createHash("sha256").update(recipient).digest().subarray(0, 16))).toBe(true);
  const k = hkdf(N.diffieHellman({ privateKey: nodePriv(seed), publicKey: nodePub(epk) }), "proofpane-e2e/1 content", 32, Buffer.concat([epk, recipient]));
  const d = N.createDecipheriv("aes-256-gcm", k, header.subarray(49, 61));
  d.setAAD(Buffer.concat([header, Buffer.from(aad, "utf8")]));
  d.setAuthTag(body.subarray(body.length - 16));
  return Buffer.concat([d.update(body.subarray(61, body.length - 16)), d.final()]);
}

describe("the page's own vectors", () => {
  it("are what the page seals now, byte for byte", async () => {
    const made = await pageVectors();
    // printed in ASCII, every other character escaped, so nothing in it is lost on its way to the file
    if (process.env.E2E_PRINT_VECTORS) {
      console.log(JSON.stringify(made, null, 1).replace(/[^\x00-\x7e]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`));
    }
    expect(existsSync(FILE)).toBe(true);
    expect(JSON.parse(readFileSync(FILE, "utf8"))).toEqual(made);
  });

  it("open with an implementation of their own, as the Python side opens them", () => {
    expect(existsSync(FILE), FILE).toBe(true);                     // a file gone fails this, never passes it by
    const v = JSON.parse(readFileSync(FILE, "utf8"));
    const recipient = b(v.content_key.public);
    for (const e of v.envelopes) {
      expect(envelopeOpen(b(v.content_key.private), recipient, e.envelope, e.slot).toString("utf8"), e.name).toBe(e.plaintext_text);
    }
    for (const g of [v.grant, v.recovery_grant]) {
      expect(envelopeOpen(b(g.private), b(g.public), g.envelope, g.slot).equals(b(g.mk))).toBe(true);
      expect(hkdf(b(g.mk), `proofpane-e2e/1 mk_id|${v.account}`, 16).toString("hex")).toBe(g.mk_id);
    }
    expect(hkdf(b(v.chain.mk), `proofpane-e2e/1 chain|${v.account}`).toString("base64url")).toBe(v.chain.subkey);
    expect(gcmOpen(b(v.chain.subkey), v.chain.nonce_and_wrapped, v.chain.aad).equals(b(v.chain.previous_mk))).toBe(true);
    expect(gcmOpen(b(v.roster.subkey), v.roster.nonce_and_wrapped, v.roster.aad).toString("utf8")).toBe(v.roster.plaintext_text);
    expect(gcmOpen(b(v.content_key_wrap.subkey), v.content_key_wrap.nonce_and_wrapped, v.content_key_wrap.aad).equals(b(v.content_key.pkcs8))).toBe(true);
    const kek = hkdf(b(v.credential_wrap.prf), v.credential_wrap.credential_info);
    expect(gcmOpen(kek, v.credential_wrap.nonce_and_wrapped, v.credential_wrap.aad).toString("utf8")).toBe(v.credential_wrap.plaintext_text);
    const code = b(v.recovery.code_bytes);
    expect(hkdf(code, `proofpane-e2e/1 recovery|${v.account}`).toString("base64url")).toBe(v.recovery.private);
    expect(gcmOpen(hkdf(code, `proofpane-e2e/1 recovery anchor|${v.account}`), v.recovery.anchor.nonce_and_wrapped, v.recovery.anchor.aad).toString("utf8"))
      .toBe(v.recovery.anchor.plaintext_text);
    expect((N.createPublicKey(nodePriv(b(v.recovery.private))).export({ format: "der", type: "spki" }) as Buffer).subarray(12).toString("base64url")).toBe(v.recovery.public);
  });
});

// ---- post-quantum --------------------------------------------------------------------------------------------------
// The page's own post-quantum vectors (tests/vectors/e2e_v2_page.json), sealed with derandomized encapsulation and
// fixed nonces, for the Python side to open, and to seal again byte for byte with the server's own code
// (tests/test_ade_e2e_envelope_pq.py). This test makes them again and holds the committed file to them.
// E2E_WRITE_VECTORS=1 writes the file. The server's own vectors (e2e_v2.json, sealed by Python at random, which pyca
// cannot derandomize) are opened here.
const FILE2 = join(__dirname, "../../tests/vectors/e2e_v2_page.json");
const SERVER2 = join(__dirname, "../../tests/vectors/e2e_v2.json");

const fixed2 = (label: string, n = 32) => new Uint8Array(N.createHash("sha512").update(`proofpane-e2e/2 page vectors: ${label}`).digest()).slice(0, n);

async function envelope2Of(PK: Uint8Array, i: number, name: string, slot: string, plaintext: Record<string, unknown>) {
  const eseed = fixed2(`eseed ${i}`, 64);
  const nonce = fixed2(`nonce ${i}`, 12);
  const text = canonicalJson(plaintext);
  return { name, slot, plaintext, plaintext_text: text, eseed: C.b64u(eseed), nonce: C.b64u(nonce),
    envelope: await C.sealEnvelope2(PK, C.utf8(text), slot, { eseed, nonce }) };
}

async function grant2Of(to: string, recipientSeed: Uint8Array, mk: Uint8Array, mkId: string) {
  const recipient = C.hybridPublic(await C.hybridFromSeed(recipientSeed));
  return { to, mk: C.b64u(mk), mk_id: mkId, seed: C.b64u(recipientSeed), public: C.b64u(recipient), aad: C.AAD.grant(ACCOUNT, to, mkId),
    envelope: await C.sealGrant(recipient, mk, ACCOUNT, to, mkId) };
}

export async function pageVectorsV2() {
  const contentSeed = fixed2("content key");
  const PK = C.hybridPublic(await C.hybridFromSeed(contentSeed));
  const keyId = await C.keyIdOf(PK);
  const cases: [string, string, Record<string, unknown>][] = [
    ["event: a step to the owner", eventSlot(ACCOUNT, LINK, "step", null, null, clientId(1)), { said_at: null, session: null, text: "Sealed after the upgrade." }],
    ["event: a mirrored note, in other scripts and with what JSON escapes", eventSlot(ACCOUNT, LINK, "note", "session", null, clientId(2)),
      { said_at: 1790000000456, session: "s-0042", text: "été λ \u{1f600} \"q\" \\ tab\there\nnext\u0001\u007f end" }],
    ["event: a reply to the command it took", eventSlot(ACCOUNT, LINK, "done", null, 77, clientId(3)), { said_at: null, session: null, text: "done: 4 passed" }],
    ["history: a listed row", historySlot(ACCOUNT, LINK, "s-0042", false), { branch: "dev", project: "~/work/page", repositories: ["~/work/page"], title: "The Upgrade screen" }],
    ["history: a withheld row, its project alone", historySlot(ACCOUNT, LINK, "s-0043", true), { project: "~/private" }],
  ];
  const envelopes = [];
  for (const [i, [name, slot, plaintext]] of cases.entries()) envelopes.push(await envelope2Of(PK, i, name, slot, plaintext));

  const tkBytes = fixed2("conversation key");
  const tk = await C.talkKeyOf(tkBytes.slice());
  const keySlot = talkKeySlot(ACCOUNT, LINK, "s-0042", 2, 1);
  const keyEseed = fixed2("conversation key eseed", 64);
  const keyNonce = fixed2("conversation key nonce", 12);
  const talkCases: [string, Record<string, unknown>][] = [
    [talkSlot(ACCOUNT, LINK, "s-0042", 0, 2, 1, "owner", 1790000002, 1790000001500), { text: "and the roster?" }],
    [talkSlot(ACCOUNT, LINK, "s-0042", 1, 2, 1, "session", null, null), { text: "It names each key, 1,216 bytes each." }],
  ];
  const messages = [];
  for (const [i, [slot, plaintext]] of talkCases.entries()) {
    const nonce = fixed2(`message nonce ${i}`, 12);
    const text = canonicalJson(plaintext);
    messages.push({ slot, plaintext, plaintext_text: text, nonce: C.b64u(nonce), envelope: await C.sealTalkMessage(tk, C.utf8(text), slot, nonce) });
  }

  const mk = fixed2("master key 1");
  const master = await C.masterOf(mk, ACCOUNT);
  const credentialSeed = fixed2("credential key");
  const credentialId = "cred-page-0002";
  const code = fixed2("recovery code", 20);
  const rkSeed = await C.hkdfBytes(await C.hkdfKey(code), C.INFO2.recovery(ACCOUNT), 32);
  const rk = await C.hybridFromSeed(rkSeed);
  const subkey = C.b64u(await C.hkdfBytes(master.key, C.INFO.subkey("content-wrap", ACCOUNT), 32));
  const contentWrap = await C.wrapContentSeed(master, contentSeed, keyId, ACCOUNT, fixed2("content key wrap nonce", 12));
  const scalar = fixed2("retired classical content key");
  const classicalPublic = await C.publicOf(await C.privateFromSeed(scalar));
  const classicalKeyId = await C.keyIdOf(classicalPublic);
  const classicalWrap = await C.wrapClassicalScalar(master, scalar, classicalKeyId, ACCOUNT, fixed2("classical key wrap nonce", 12));
  const prf = fixed2("credential prf output");
  const credentialWrap = await C.sealCredentialSeed(await C.credentialKek(prf.slice(), ACCOUNT, credentialId), credentialSeed, master.id,
    ACCOUNT, credentialId, fixed2("credential wrap nonce", 12));
  const serverEseed = fixed2("recovery proof server eseed", 64);
  const { ct: serverCt } = await C.xwingEncapsulate(C.hybridPublic(rk), serverEseed);
  const proofNonce = fixed2("recovery proof nonce");

  return {
    about: "End-to-end encryption, post-quantum: what the page (ui/src/e2eCrypto.ts, X-Wing from mlkem 2.7.0 and WebCrypto) seals from fixed keys, eseeds and nonces, for the Python side to open and to seal again byte for byte, made by ui/src/e2eVectors.test.ts, which holds this file to what the page writes. Bytes are base64url without padding. A post-quantum private key is X-Wing's 32-byte seed (seed); public is its 1,216-byte public key. Envelopes are form 2 (0x02 || key_id || ct (1,120) || nonce || ciphertext || tag), each with the eseed of its derandomized encapsulation (the first 32 bytes ML-KEM's message, the last 32 the X25519 ephemeral key) and its nonce. The conversation's key is a form-2 envelope of its 32 bytes (tk) bound to key_slot, its key_id the first 16 bytes of HKDF-SHA256(tk, salt empty, info \"proofpane-e2e/3 talk key id\"); its messages are form 3 under HKDF-SHA256(tk, salt empty, info \"proofpane-e2e/3 talk message|\" + slot). Grants are form 2 with their randomness derived from the master key: HKDF-SHA256(MK, salt sha256(public), info \"proofpane-e2e/2 grant ephemeral|\" + aad), 76 bytes, eseed then nonce. The wraps are AES-256-GCM, stored as nonce, ciphertext and tag (60 bytes): a content key's seed and a retired classical key's 32-byte X25519 scalar under the content-wrap subkey (given), HKDF-SHA256(MK, salt empty, info \"proofpane-e2e/1 content-wrap|\" + account); a credential's seed under HKDF-SHA256(prf, salt empty, info credential_info), the anchor in its AAD. The recovery key's seed is HKDF-SHA256(the code's 20 bytes, salt empty, info \"proofpane-e2e/2 recovery|\" + account); its proof answers an encapsulation to it (server_ct, made here from server_eseed): HMAC-SHA256 under HKDF-SHA256(ss, salt empty, info \"proofpane-e2e/2 recovery proof\") over nonce || purpose.",
    version: 2,
    account: ACCOUNT,
    link_id: LINK,
    content_key: { seed: C.b64u(contentSeed), public: C.b64u(PK), key_id: keyId },
    envelopes,
    talk: { session: "s-0042", count: 2, earlier: 1, tk: C.b64u(tkBytes), key_id: tk.id, key_slot: keySlot, key_eseed: C.b64u(keyEseed),
      key_nonce: C.b64u(keyNonce), key_envelope: await C.sealTalkKey(PK, tkBytes, keySlot, { eseed: keyEseed, nonce: keyNonce }), messages },
    grant: await grant2Of(credentialId, credentialSeed, mk, master.id),
    recovery_grant: await grant2Of("recovery", rkSeed, mk, master.id),
    content_key_wrap: { mk: C.b64u(mk), mk_id: master.id, subkey, aad: C.AAD2.contentKey(ACCOUNT, keyId, master.id), key_id: keyId,
      seed: C.b64u(contentSeed), nonce_and_wrapped: contentWrap },
    classical_key_wrap: { mk: C.b64u(mk), mk_id: master.id, subkey, aad: C.AAD2.contentKeyX25519(ACCOUNT, classicalKeyId, master.id),
      scalar: C.b64u(scalar), public: C.b64u(classicalPublic), key_id: classicalKeyId, nonce_and_wrapped: classicalWrap },
    credential_wrap: { credential_id: credentialId, prf: C.b64u(prf), credential_info: C.INFO.credential(ACCOUNT, credentialId), anchor: master.id,
      aad: C.AAD2.credential(ACCOUNT, credentialId, master.id), seed: C.b64u(credentialSeed),
      public: C.b64u(C.hybridPublic(await C.hybridFromSeed(credentialSeed))), nonce_and_wrapped: credentialWrap },
    recovery: { code: C.recoveryText(code), code_bytes: C.b64u(code), seed: C.b64u(rkSeed), public: C.b64u(C.hybridPublic(rk)),
      proof: { server_eseed: C.b64u(serverEseed), server_ct: C.b64u(serverCt), nonce: C.b64u(proofNonce), purpose: "e2e-reseal",
        proof: await C.recoveryProofHybrid(rk, serverCt, proofNonce, "e2e-reseal") } },
  };
}

const asText = (made: unknown) =>
  JSON.stringify(made, null, 1).replace(/[^\x00-\x7e]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`) + "\n";

describe("the page's own post-quantum vectors", () => {
  it("are what the page seals now, byte for byte", async () => {
    const made = await pageVectorsV2();
    if (process.env.E2E_WRITE_VECTORS) writeFileSync(FILE2, asText(made));
    expect(existsSync(FILE2), FILE2).toBe(true);
    expect(JSON.parse(readFileSync(FILE2, "utf8"))).toEqual(made);
  });
});

describe("the server's own post-quantum vectors", () => {
  it("open here: each envelope, the conversation and the recovery proof", async () => {
    expect(existsSync(SERVER2), SERVER2).toBe(true);                // a file gone fails this, never passes it by
    const v = JSON.parse(readFileSync(SERVER2, "utf8"));
    const key = await C.hybridFromSeed(C.unb64u(v.content_key.seed)!);
    expect(C.b64u(C.hybridPublic(key))).toBe(v.content_key.public);
    expect(await C.keyIdOf(C.hybridPublic(key))).toBe(v.content_key.key_id);
    expect(v.envelopes.length).toBeGreaterThan(0);
    for (const e of v.envelopes) {
      expect(C.envelopeForm(e.envelope)?.form, e.name).toBe(2);
      expect(new TextDecoder().decode(await C.openEnvelope2(key, e.envelope, e.slot)), e.name).toBe(e.plaintext_text);
    }
    const tk = await C.openTalkKey(key, v.talk.key_envelope, v.talk.key_slot, v.talk.key_id);
    expect(v.talk.key_slot).toBe(talkKeySlot(v.account, v.link_id, v.talk.session, v.talk.count, v.talk.earlier));
    expect(v.talk.messages.length).toBe(v.talk.count);
    for (const m of v.talk.messages) {
      expect(new TextDecoder().decode(await C.openTalkMessage(tk, m.envelope, m.slot))).toBe(m.plaintext_text);
    }
    const rk = await C.recoveryHybrid(C.unb64u(v.recovery.code_bytes)!, v.account);
    expect(C.b64u(C.hybridPublic(rk))).toBe(v.recovery.public);
    const p = v.recovery.proof;
    expect(await C.recoveryProofHybrid(rk, C.unb64u(p.server_ct)!, C.unb64u(p.nonce)!, p.purpose)).toBe(p.proof);
  });
});

// The link's own post-quantum vectors (tests/vectors/e2e_v2_link.json, sealed at random by the link client's own
// functions as it posts to a post-quantum account; made by tests/test_ade_link_client_e2e_pq.py). The page opens each
// here as it opens what a link posts: at the slot it makes itself from where the text goes, with its own opener (a form-2
// envelope under a post-quantum content key; a conversation's messages through their key), which takes a plaintext only
// in its RFC 8785 form.
const LINK2 = join(__dirname, "../../tests/vectors/e2e_v2_link.json");

describe("the link's own post-quantum vectors", () => {
  it("open with the page's opener, each at the slot the page makes for where it goes, and at no other", async () => {
    expect(existsSync(LINK2), LINK2).toBe(true);                    // a file gone fails this, never passes it by
    const v = JSON.parse(readFileSync(LINK2, "utf8"));
    expect(v.version).toBe(2);
    const key = await C.hybridFromSeed(C.unb64u(v.content_key.seed)!);
    const EK = C.hybridPublic(key);
    expect(C.b64u(EK)).toBe(v.content_key.public);
    expect(await C.keyIdOf(EK)).toBe(v.content_key.key_id);
    // a tab's keys holding that content key alone
    const ring = { account: v.account, keys: new Map([[v.content_key.key_id, { ek: { pq: true as const, key }, EK }]]), unreached: [], unopened: [] } as unknown as K.Ring;
    const slots: string[] = [];
    for (const e of v.envelopes) {
      const w = e.where;
      const slot = w.path === "event" ? eventSlot(v.account, v.link_id, w.kind, w.mirror, w.ref, w.client_id)
        : historySlot(v.account, v.link_id, w.session, w.withheld);
      expect(slot, e.name).toBe(e.slot);
      expect(C.envelopeForm(e.envelope)?.form, e.name).toBe(2);
      expect(await K.openWith(ring, e.envelope, slot), e.name).toEqual({ ok: true, value: e.plaintext });
      expect(canonicalJson(e.plaintext), e.name).toBe(e.plaintext_text);
      slots.push(slot);
    }
    expect(v.envelopes.map((e: { where: { path: string } }) => e.where.path)).toEqual(["event", "event", "event", "history", "history"]);
    // bound to its slot: at the next one's, none opens
    for (const [i, e] of v.envelopes.entries()) {
      expect(await K.openWith(ring, e.envelope, slots[(i + 1) % slots.length]), e.name).toEqual({ ok: false, why: K.UNREADABLE });
    }
    const t = v.talk;
    const keySlot = talkKeySlot(v.account, v.link_id, t.session, t.count, t.earlier);
    expect(keySlot).toBe(t.key_slot);
    expect(t.messages.length).toBe(t.count);
    const talk = { key: t.key_envelope, key_id: t.key_id, keySlot };
    for (const [i, m] of t.messages.entries()) {
      const slot = talkSlot(v.account, v.link_id, t.session, i, t.count, t.earlier, m.who, m.at, m.ms);
      expect(slot).toBe(m.slot);
      expect(C.envelopeForm(m.envelope)?.form).toBe(3);
      expect(await K.openTalkText(ring, talk, m.envelope, slot)).toEqual({ ok: true, value: m.plaintext });
      expect(canonicalJson(m.plaintext)).toBe(m.plaintext_text);
      // the conversation's key bound to its own slot, and each message to its place
      expect(await K.openTalkText(ring, { ...talk, keySlot: talkKeySlot(v.account, v.link_id, t.session, t.count, t.earlier + 1) }, m.envelope, slot))
        .toEqual({ ok: false, why: K.UNREADABLE });
      expect(await K.openTalkText(ring, talk, m.envelope, talkSlot(v.account, v.link_id, t.session, 1 - i, t.count, t.earlier, m.who, m.at, m.ms)))
        .toEqual({ ok: false, why: K.UNREADABLE });
    }
  });
});
