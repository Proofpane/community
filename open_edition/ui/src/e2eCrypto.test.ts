import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import * as N from "node:crypto";
import { describe, expect, it } from "vitest";
import * as C from "./e2eCrypto";

// The link client's vectors (ade_link.py's tests make them): what the page must open, and refuse, as the Python side does.
// They are part of the repository: a missing file fails these checks, never passes them by.
const LINK_VECTORS = join(__dirname, "../../tests/vectors/e2e_v1.json");
const linkFile = existsSync(LINK_VECTORS) ? JSON.parse(readFileSync(LINK_VECTORS, "utf8")) : null;
function vectors() {
  if (!linkFile) throw new Error(`The link client's vectors are missing: ${LINK_VECTORS}`);
  return linkFile;
}

const raw = (text: string) => {
  const b = C.unb64u(text);
  if (!b) throw new Error(`not base64url: ${text}`);
  return b;
};
const text = (b: Uint8Array) => new TextDecoder().decode(b);

// ---- an implementation of its own, in Node's crypto, standing for the Python side's -------------------------------
const PKCS8_HEAD = Buffer.from("302e020100300506032b656e04220420", "hex");
const SPKI_HEAD = Buffer.from("302a300506032b656e032100", "hex");
const nodePriv = (seed: Uint8Array) => N.createPrivateKey({ key: Buffer.concat([PKCS8_HEAD, seed]), format: "der", type: "pkcs8" });
const nodePub = (pub: Uint8Array) => N.createPublicKey({ key: Buffer.concat([SPKI_HEAD, pub]), format: "der", type: "spki" });
const nodePubOf = (seed: Uint8Array) => (N.createPublicKey(nodePriv(seed)).export({ format: "der", type: "spki" }) as Buffer).subarray(12);

export function nodeOpen(seed: Uint8Array, recipient: Uint8Array, envelope: string, aad: string): Buffer {
  const body = Buffer.from(envelope.slice(5), "base64url");
  const header = body.subarray(0, 61);
  const epk = header.subarray(17, 49);
  const shared = N.diffieHellman({ privateKey: nodePriv(seed), publicKey: nodePub(epk) });
  const k = Buffer.from(N.hkdfSync("sha256", shared, Buffer.concat([epk, recipient]), "proofpane-e2e/1 content", 32));
  const d = N.createDecipheriv("aes-256-gcm", k, header.subarray(49, 61));
  d.setAAD(Buffer.concat([header, Buffer.from(aad, "utf8")]));
  d.setAuthTag(body.subarray(body.length - 16));
  return Buffer.concat([d.update(body.subarray(61, body.length - 16)), d.final()]);
}

describe("the link client's vectors", () => {
  it("are there to check against", () => {
    expect(existsSync(LINK_VECTORS), LINK_VECTORS).toBe(true);
  });

  it("open each envelope with its slot to the plaintext's RFC 8785 bytes, and the same keys seal the same envelope", async () => {
    const link = vectors();
    const ek = await C.privateFromSeed(raw(link.content_key.private));
    const EK = raw(link.content_key.public);
    expect(await C.keyIdOf(EK)).toBe(link.content_key.key_id);
    expect(C.b64u(await C.publicOf(ek))).toBe(link.content_key.public);
    expect(link.envelopes.length).toBeGreaterThan(5);
    for (const v of link.envelopes) {
      const plain = await C.openEnvelope(ek, EK, v.envelope, v.slot);
      expect(text(plain), v.name).toBe(v.plaintext_text);
      expect(C.plaintextObject(plain), v.name).toEqual(v.plaintext);
      const ephemeral = await C.privateFromSeed(raw(v.ephemeral_private));
      const again = await C.sealEnvelope(EK, C.utf8(v.plaintext_text), v.slot,
        { ephemeral, ephemeralPublic: await C.publicOf(ephemeral), nonce: raw(v.nonce) });
      expect(again, v.name).toBe(v.envelope);
    }
  });

  it("refuse what the Python side refuses: another slot or account, a changed byte, another version or key, a body in another form, a plaintext not RFC 8785, not UTF-8 or not an object", async () => {
    const link = vectors();
    const ek = await C.privateFromSeed(raw(link.content_key.private));
    const EK = raw(link.content_key.public);
    expect(link.refused.length).toBeGreaterThan(10);
    for (const r of link.refused) {
      const opened = await C.openEnvelope(ek, EK, r.envelope, r.slot).then(C.plaintextObject, () => null);
      expect(opened, r.why).toBeNull();
    }
  });

  it("open the grants to the master key, whose mk_id the page computes the same, and seal them the same", async () => {
    const link = vectors();
    for (const g of [link.grant, link.recovery_grant]) {
      const priv = await C.privateFromSeed(raw(g.private));
      const mk = await C.openEnvelope(priv, raw(g.public), g.envelope, g.slot);
      expect(C.b64u(mk)).toBe(g.mk);
      expect((await C.masterOf(mk, link.account)).id).toBe(g.mk_id);
      expect(C.AAD.grant(link.account, g.to, g.mk_id)).toBe(g.slot);
      const ephemeral = await C.privateFromSeed(raw(g.ephemeral_private));
      expect(await C.sealGrant(raw(g.public), mk, link.account, g.to, g.mk_id,
        { ephemeral, ephemeralPublic: await C.publicOf(ephemeral), nonce: raw(g.nonce) })).toBe(g.envelope);
    }
  });

  it("open the content key's wrap under the content-wrap subkey, which the page derives the same, and wrap it the same", async () => {
    const link = vectors();
    const w = link.content_key_wrap;
    const m = await C.masterOf(raw(w.mk), link.account);
    expect(m.id).toBe(w.mk_id);
    expect(C.b64u(await C.hkdfBytes(m.key, C.INFO.subkey("content-wrap", link.account), 32))).toBe(w.subkey);
    expect(C.AAD.contentKey(link.account, link.content_key.key_id, w.mk_id)).toBe(w.aad);
    const ek = await C.unwrapContentKey(m, w.nonce_and_wrapped, link.content_key.key_id, link.account, true);
    expect(C.b64u(await C.publicOf(ek))).toBe(link.content_key.public);
    expect(await C.wrapContentKey(m, ek, link.content_key.key_id, link.account, raw(w.nonce))).toBe(w.nonce_and_wrapped);
  });
});

describe("a grant", () => {
  const ACCT = "account-1";
  const MK_ID = "0123456789abcdef0123456789abcdef";

  it("is sealed with an ephemeral key and nonce derived from the master key, its slot and the recipient's key: the known answer an implementation of its own computes", async () => {
    // from HKDF-SHA256(mk, salt = recipient, info = "proofpane-e2e/1 grant ephemeral|" + slot), 44 bytes, then the
    // envelope's construction, computed with Python's cryptography alone
    const mk = Uint8Array.from({ length: 32 }, (_, i) => i);
    const recipient = C.unb64u("NYBy1jZYgNGu6jKa35EhODhR7SGijjt16WXQ0s0WYlQ")!;
    const envelope = "e2e1.AaAomBADLrQXJ07rxKOOaJl7GiOWY6hnHERGP4y8l5O0_0yRWHI0iyWu7NdvRaxzCqFaDnUPtN565jRbw0XRNIljZvnAW18RwlBLXXOIhi2b0XY8QxchzOPkaIfDKt82p0axduy9JRTmde2S8Q";
    expect(await C.sealGrant(recipient, mk, ACCT, "credAAAA", MK_ID)).toBe(envelope);
    const priv = await C.privateFromSeed(Uint8Array.from({ length: 32 }, (_, i) => 32 + i));
    expect(C.b64u(await C.openGrant(priv, recipient, envelope, ACCT, "credAAAA", MK_ID))).toBe(C.b64u(mk));
  });

  it("is the same each time, and another for another key, recipient, master key, mk_id or account; whoever holds the master key tells it from any other envelope", async () => {
    const mk = C.random(32);
    const pair = await C.newPair();
    const other = await C.newPair();
    const grant = await C.sealGrant(pair.pub, mk, ACCT, "credAAAA", MK_ID);
    expect(await C.sealGrant(pair.pub, mk, ACCT, "credAAAA", MK_ID)).toBe(grant);
    const part = (text: string, from: number, to: number) => C.b64u(C.envelopeBytes(text)!.subarray(from, to));
    const others: [string, string][] = [
      ["another key", await C.sealGrant(other.pub, mk, ACCT, "credAAAA", MK_ID)],
      ["another recipient", await C.sealGrant(pair.pub, mk, ACCT, "credBBBB", MK_ID)],
      ["another master key", await C.sealGrant(pair.pub, C.random(32), ACCT, "credAAAA", MK_ID)],
      ["another mk_id", await C.sealGrant(pair.pub, mk, ACCT, "credAAAA", "f".repeat(32))],
      ["another account", await C.sealGrant(pair.pub, mk, "account-2", "credAAAA", MK_ID)],
    ];
    for (const [what, text] of others) {
      expect(part(text, 17, 49), `${what}: its ephemeral key`).not.toBe(part(grant, 17, 49));
      expect(part(text, 49, 61), `${what}: its nonce`).not.toBe(part(grant, 49, 61));
    }
    expect(await C.isGrantOf(grant, pair.pub, mk, ACCT, "credAAAA", MK_ID)).toBe(true);
    // the same master key sealed to the same key with a random ephemeral key opens all the same, and is not the grant
    const random = await C.sealEnvelope(pair.pub, mk, C.AAD.grant(ACCT, "credAAAA", MK_ID));
    expect(C.b64u(await C.openGrant(pair.priv, pair.pub, random, ACCT, "credAAAA", MK_ID))).toBe(C.b64u(mk));
    expect(await C.isGrantOf(random, pair.pub, mk, ACCT, "credAAAA", MK_ID)).toBe(false);
    expect(await C.isGrantOf(grant, other.pub, mk, ACCT, "credAAAA", MK_ID)).toBe(false);
    expect(await C.isGrantOf(grant, pair.pub, C.random(32), ACCT, "credAAAA", MK_ID)).toBe(false);
    expect(await C.isGrantOf(grant, pair.pub, mk, ACCT, "credBBBB", MK_ID)).toBe(false);
    expect(await C.isGrantOf(grant, pair.pub, mk, ACCT, "credAAAA", "f".repeat(32))).toBe(false);
    expect(await C.isGrantOf(null, pair.pub, mk, ACCT, "credAAAA", MK_ID)).toBe(false);
  });
});

describe("an envelope's form", () => {
  it("is taken only exactly: the prefix, base64url without padding that reads one way, long enough, version 1", async () => {
    const pair = await C.newPair();
    const good = await C.sealEnvelope(pair.pub, C.utf8('{"text":"hi"}'), "slot");
    expect(C.envelopeBytes(good)).not.toBeNull();
    const body = good.slice(5);
    expect(C.envelopeBytes("e2e2." + body)).toBeNull();                       // another prefix
    expect(C.envelopeBytes(" " + good)).toBeNull();
    expect(C.envelopeBytes(good + "=")).toBeNull();                           // padding
    // the other alphabet: a character of standard base64 in place of one of base64url, at a fixed place
    expect(C.envelopeBytes(`${good.slice(0, 20)}+${good.slice(21)}`)).toBeNull();
    expect(C.envelopeBytes(`${good.slice(0, 20)}/${good.slice(21)}`)).toBeNull();
    expect(C.envelopeBytes(good + "A")).toBeNull();                           // a length that does not hold
    expect(C.envelopeBytes(good + "\n")).toBeNull();
    expect(C.envelopeBytes(42)).toBeNull();
    // a last character whose spare bits are set (of a text 2 mod 4 long, it is one of A, Q, g, w): its bytes would
    // encode to another text
    const bytes = C.unb64u(body)!;
    const tail = C.b64u(bytes.subarray(0, bytes.length - (bytes.length % 3 || 3) + 1));
    expect(tail.length % 4).toBe(2);
    expect("AQgw").toContain(tail[tail.length - 1]);
    const spare = tail.slice(0, -1) + String.fromCharCode(tail.charCodeAt(tail.length - 1) + 1);
    expect(C.envelopeBytes("e2e1." + tail)).not.toBeNull();
    expect(C.envelopeBytes("e2e1." + spare)).toBeNull();
    // too short for a header and a tag, and another version
    expect(C.envelopeBytes("e2e1." + C.b64u(bytes.subarray(0, C.OVERHEAD - 1)))).toBeNull();
    expect(C.envelopeBytes("e2e1." + C.b64u(bytes.subarray(0, C.OVERHEAD)))).not.toBeNull();
    const v2 = bytes.slice();
    v2[0] = 2;
    expect(C.envelopeBytes("e2e1." + C.b64u(v2))).toBeNull();
    await expect(C.openEnvelope(pair.priv, pair.pub, "e2e1." + C.b64u(v2), "slot")).rejects.toThrow(C.NOT_ENVELOPE);
  });

  it("opens only with its slot and its key, and an all-zero ephemeral key is refused", async () => {
    const pair = await C.newPair();
    const other = await C.newPair();
    const env = await C.sealEnvelope(pair.pub, C.utf8('{"text":"hi"}'), "proofpane-e2e/1|a|l|event|step|||x");
    expect(text(await C.openEnvelope(pair.priv, pair.pub, env, "proofpane-e2e/1|a|l|event|step|||x"))).toBe('{"text":"hi"}');
    await expect(C.openEnvelope(pair.priv, pair.pub, env, "proofpane-e2e/1|a|l|event|step|||y")).rejects.toThrow(C.DOES_NOT_OPEN);
    await expect(C.openEnvelope(other.priv, other.pub, env, "proofpane-e2e/1|a|l|event|step|||x")).rejects.toThrow(C.OTHER_KEY);
    const b = C.envelopeBytes(env)!;
    b.fill(0, 17, 49);
    await expect(C.openEnvelope(pair.priv, pair.pub, "e2e1." + C.b64u(b), "proofpane-e2e/1|a|l|event|step|||x")).rejects.toThrow(C.LOW_ORDER);
  });

  it("its plaintext is read as strict UTF-8 holding a JSON object in RFC 8785 form: a byte order mark, a bad sequence, a list, another form is refused", () => {
    expect(C.plaintextObject(C.utf8('{"text":"café"}'))).toEqual({ text: "café" });
    expect(C.plaintextObject(C.utf8('{"said_at":1790000000123,"session":null,"text":"a\\u0001\\"b"}'))).toEqual({ said_at: 1790000000123, session: null, text: 'a\u0001"b' });
    expect(C.plaintextObject(Uint8Array.of(0xef, 0xbb, 0xbf, ...C.utf8('{"text":"x"}')))).toBeNull();
    expect(C.plaintextObject(Uint8Array.of(0x7b, 0x22, 0xc3, 0x28, 0x22, 0x3a, 0x31, 0x7d))).toBeNull();
    expect(C.plaintextObject(C.utf8('["text"]'))).toBeNull();
    expect(C.plaintextObject(C.utf8("null"))).toBeNull();
    expect(C.plaintextObject(C.utf8('{"text":'))).toBeNull();
    expect(C.plaintextObject(C.utf8('{"text": "x"}'))).toBeNull();                  // a space RFC 8785 does not write
    expect(C.plaintextObject(C.utf8('{"text":"x","said_at":1}'))).toBeNull();      // keys out of order
    expect(C.plaintextObject(C.utf8('{"text":"x","text":"y"}'))).toBeNull();       // a key twice
    expect(C.plaintextObject(C.utf8('{"text":"\\u00e9"}'))).toBeNull();            // escaped where it need not be
    expect(C.plaintextObject(C.utf8('{"n":1.5}'))).toBeNull();                     // no fractions in a plaintext
  });

  it("is what an implementation of its own opens: Node's crypto, as the Python side would", async () => {
    const seed = C.random(32);
    const priv = await C.privateFromSeed(seed);
    const pub = await C.publicOf(priv);
    expect(Buffer.from(nodePubOf(seed)).equals(Buffer.from(pub))).toBe(true);
    const env = await C.sealEnvelope(pub, C.utf8('{"text":"from the page"}'), "the slot");
    expect(nodeOpen(seed, pub, env, "the slot").toString("utf8")).toBe('{"text":"from the page"}');
  });
});

describe("base64url", () => {
  it("round-trips any bytes and reads only its own form", () => {
    for (let n = 0; n < 40; n++) {
      const b = C.random(n);
      expect(C.unb64u(C.b64u(b))).toEqual(b);
      expect(C.b64u(b)).toBe(Buffer.from(b).toString("base64url"));
      expect(new TextDecoder().decode(C.b64uBytes(b))).toBe(C.b64u(b));
    }
    expect(C.unb64u("AAA=")).toBeNull();
    expect(C.unb64u("A")).toBeNull();
    expect(C.unb64u("AB")).toBeNull();          // spare bits set: B is 000001
    expect(C.unb64u("AA")).toEqual(Uint8Array.of(0));
    expect(C.unb64u("a+b/")).toBeNull();
    expect(C.unb64u("éAAA")).toBeNull();
    expect(C.unb64u(null)).toBeNull();
  });
});

describe("the recovery code", () => {
  it("is 20 bytes shown as 32 base32 characters in groups of four, and read back whatever the spaces, dashes and case", () => {
    const code = C.random(C.CODE_BYTES);
    const shown = C.recoveryText(code);
    expect(shown).toMatch(/^([A-Z2-7]{4} ){7}[A-Z2-7]{4}$/);
    expect(C.recoveryBytes(shown)).toEqual(code);
    expect(C.recoveryBytes(shown.toLowerCase().replace(/ /g, "-"))).toEqual(code);
    expect(C.recoveryBytes(shown.replace(/ /g, ""))).toEqual(code);
    expect(C.recoveryBytes(shown.slice(0, -1))).toBeNull();
    expect(C.recoveryBytes(shown.slice(0, -1) + "1")).toBeNull();           // 1 is not in base32
    expect(C.recoveryText(Uint8Array.from({ length: 20 }, (_, i) => i))).toBe("AAAQ EAYE AUDA OCAJ BIFQ YDIO B4IB CEQT");
  });

  it("gives the recovery key pair and the anchor bound to the account", async () => {
    const code = C.random(C.CODE_BYTES);
    const a = await C.recoveryKeys(code, "acct-a");
    const b = await C.recoveryKeys(code, "acct-b");
    expect(a.rk.extractable).toBe(false);
    expect(C.b64u(a.RK)).not.toBe(C.b64u(b.RK));
    expect(C.same(await C.publicOf(a.rk), a.RK)).toBe(true);
    // rk is HKDF(code, "recovery|" + account), as an X25519 private key
    const seed = Buffer.from(N.hkdfSync("sha256", code, Buffer.alloc(0), "proofpane-e2e/1 recovery|acct-a", 32));
    expect(Buffer.from(nodePubOf(seed)).equals(Buffer.from(a.RK))).toBe(true);
    const anchor = await C.sealRecoveryAnchor(code, "ab".repeat(16), "acct-a");
    expect(await C.openRecoveryAnchor(code, anchor, "acct-a")).toBe("ab".repeat(16));
    await expect(C.openRecoveryAnchor(code, anchor, "acct-b")).rejects.toThrow(C.DOES_NOT_OPEN);
    await expect(C.openRecoveryAnchor(C.random(C.CODE_BYTES), anchor, "acct-a")).rejects.toThrow(C.DOES_NOT_OPEN);
  });

  it("proves itself to the server: the HMAC under HKDF(X25519(rk, S)) over the nonce and the purpose, which the server checks with S's private half and RK", async () => {
    const code = C.random(C.CODE_BYTES);
    const { rk, RK } = await C.recoveryKeys(code, "acct-a");
    const server = N.generateKeyPairSync("x25519");
    const S = (server.publicKey.export({ format: "der", type: "spki" }) as Buffer).subarray(12);
    const nonce = C.random(32);
    const proof = await C.recoveryProof(rk, S, nonce, "passkey");
    const shared = N.diffieHellman({ privateKey: server.privateKey, publicKey: nodePub(RK) });
    const key = Buffer.from(N.hkdfSync("sha256", shared, Buffer.alloc(0), "proofpane-e2e/1 recovery proof", 32));
    const want = N.createHmac("sha256", key).update(Buffer.concat([nonce, Buffer.from("passkey")])).digest("base64url");
    expect(proof).toBe(want);
    expect(await C.recoveryProof(rk, S, nonce, "revoke")).not.toBe(want);
  });
});

describe("the master key and the credential's own wrap", () => {
  it("names the master key by an mk_id bound to the account, and keeps its subkeys apart", async () => {
    const mk = C.random(32);
    const a = await C.masterOf(mk, "acct-a");
    expect(a.id).toMatch(/^[0-9a-f]{32}$/);
    expect(a.id).toBe(Buffer.from(N.hkdfSync("sha256", mk, Buffer.alloc(0), "proofpane-e2e/1 mk_id|acct-a", 16)).toString("hex"));
    expect((await C.masterOf(mk, "acct-b")).id).not.toBe(a.id);
    expect(a.key.extractable).toBe(false);
    const sub = (p: C.Purpose) => C.hkdfBytes(a.key, C.INFO.subkey(p, "acct-a"), 32).then(C.b64u);
    const all = await Promise.all((["content-wrap", "chain", "roster"] as C.Purpose[]).map(sub));
    expect(new Set(all).size).toBe(3);
    expect(all[1]).toBe(Buffer.from(N.hkdfSync("sha256", mk, Buffer.alloc(0), "proofpane-e2e/1 chain|acct-a", 32)).toString("base64url"));
    await expect(C.masterOf(C.random(31), "acct-a")).rejects.toThrow(C.E2EError);
  });

  it("writes the credential's wrap as {anchor, ck} in RFC 8785, made and read as bytes, and refuses another form", async () => {
    const der = Uint8Array.of(...PKCS8_HEAD, ...C.random(32));
    const anchor = "0f".repeat(16);
    const plain = C.credentialPlaintext(anchor, der);
    expect(text(plain)).toBe(`{"anchor":"${anchor}","ck":"${Buffer.from(der).toString("base64url")}"}`);
    expect(C.readCredentialPlaintext(plain)).toEqual({ anchor, der });
    for (const broken of [`{"anchor":"${anchor}","ck":"A"}`, `{"ck":"x","anchor":"${anchor}"}`, `{"anchor":"${anchor.toUpperCase()}","ck":"AAAA"}`,
      `{"anchor":"${anchor}","ck":"AAAA"} `, `{"anchor":"${anchor}","ck":"AA=A"}`]) {
      expect(C.readCredentialPlaintext(C.utf8(broken)), broken).toBeNull();
    }
    expect(() => C.credentialPlaintext("not an mk_id", der)).toThrow(C.E2EError);
  });

  it("wraps the credential's key under its PRF output, bound to the account and the credential, and zeroes the PRF output", async () => {
    const pair = await C.newPair(true);
    const prf = C.random(32);
    const copy = prf.slice();
    const kek = await C.credentialKek(prf, "acct-a", "cred-1");
    expect(prf.every((b) => b === 0)).toBe(true);
    const wrapped = await C.sealCredential(kek, pair.priv, "ab".repeat(16), "acct-a", "cred-1");
    const opened = await C.openCredential(await C.credentialKek(copy.slice(), "acct-a", "cred-1"), wrapped, "acct-a", "cred-1");
    expect(opened.anchor).toBe("ab".repeat(16));
    expect(opened.ck.extractable).toBe(false);
    expect(C.same(await C.publicOf(opened.ck), pair.pub)).toBe(true);
    await expect(C.openCredential(await C.credentialKek(copy.slice(), "acct-a", "cred-2"), wrapped, "acct-a", "cred-2")).rejects.toThrow();
    await expect(C.openCredential(await C.credentialKek(copy.slice(), "acct-b", "cred-1"), wrapped, "acct-b", "cred-1")).rejects.toThrow();
    // the wrap under its kek is AES-256-GCM with the credential's AAD: an implementation of its own opens it
    const k = Buffer.from(N.hkdfSync("sha256", copy, Buffer.alloc(0), "proofpane-e2e/1 credential|acct-a|cred-1", 32));
    const blob = Buffer.from(wrapped, "base64url");
    const d = N.createDecipheriv("aes-256-gcm", k, blob.subarray(0, 12));
    d.setAAD(Buffer.from("proofpane-e2e/1 credential|acct-a|cred-1"));
    d.setAuthTag(blob.subarray(blob.length - 16));
    expect(Buffer.concat([d.update(blob.subarray(12, blob.length - 16)), d.final()]).toString()).toMatch(/^\{"anchor":"(ab){16}","ck":"MC4CAQAwBQYDK2VuBCIEI/);
  });
});
