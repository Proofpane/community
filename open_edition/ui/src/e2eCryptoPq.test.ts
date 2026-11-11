import * as N from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import * as C from "./e2eCrypto";
import * as Kem from "./mlkem768";
import { talkKeySlot, talkSlot } from "./e2e";

// The page's post-quantum primitives: X-Wing against draft-11's vectors, the key
// held, form 2, a conversation's key and its form-3 messages, grants to post-quantum keys, the keys' wraps, the
// recovery key and its proof. What each derives is checked again here with Node's own crypto.
const DRAFT = join(__dirname, "../../tests/vectors/xwing_draft11.json");
const DRAFT_FILE_SHA256 = "70c0c7cb747e76ab0da47f0aaa852fd1ea7f6eb7b1437f123b67f662aad0e036";
const b = (h: string) => new Uint8Array(Buffer.from(h, "hex"));
const hex = (u: Uint8Array) => Buffer.from(u).toString("hex");
const hkdf = (ikm: Uint8Array, info: string, n = 32, salt: Uint8Array = new Uint8Array(0)) =>
  new Uint8Array(N.hkdfSync("sha256", ikm, salt, info, n));
const sha256 = (u: Uint8Array) => new Uint8Array(N.createHash("sha256").update(u).digest());
const PKCS8_HEAD = Buffer.from("302e020100300506032b656e04220420", "hex");
const x25519Public = (priv: Uint8Array) =>
  new Uint8Array((N.createPublicKey(N.createPrivateKey({ key: Buffer.concat([PKCS8_HEAD, priv]), format: "der", type: "pkcs8" }))
    .export({ format: "der", type: "spki" }) as Buffer).subarray(12));
const ACCOUNT = "acct-pq";
const MK_ID = "0123456789abcdef0123456789abcdef";

async function pair(fill?: number) {
  const seed = fill === undefined ? C.random(32) : new Uint8Array(32).fill(fill);
  const key = await C.hybridFromSeed(seed);
  return { seed, key, pub: C.hybridPublic(key) };
}

function frame(body: Uint8Array): string {
  return C.PREFIX + C.b64u(body);
}

async function refusal(p: Promise<unknown>): Promise<string> {
  try {
    await p;
  } catch (e) {
    return e instanceof C.E2EError ? e.message : `not an E2EError: ${e}`;
  }
  return "opened";
}

/** AES-256-GCM of `plain` under `key`, stored as nonce, ciphertext and tag, made with Node's crypto. */
function gcm(key: Uint8Array, plain: Uint8Array, aad: string): string {
  const nonce = C.random(12);
  const cipher = N.createCipheriv("aes-256-gcm", key, nonce);
  cipher.setAAD(C.utf8(aad));
  return C.b64u(C.concat(nonce, Buffer.concat([cipher.update(plain), cipher.final(), cipher.getAuthTag()])));
}

/** A wrap opened with Node's crypto: AES-256-GCM under `key`, nonce first, tag last. */
function openWrap(key: Uint8Array, wrapped: string, aad: string): string {
  const blob = C.unb64u(wrapped)!;
  const d = N.createDecipheriv("aes-256-gcm", key, blob.subarray(0, 12));
  d.setAAD(C.utf8(aad));
  d.setAuthTag(blob.subarray(blob.length - 16));
  return hex(Buffer.concat([d.update(blob.subarray(12, blob.length - 16)), d.final()]));
}

describe("X-Wing, against draft-11's vectors", () => {
  it("gives each vector's key, decapsulation and derandomized encapsulation", async () => {
    const raw = readFileSync(DRAFT);
    expect(N.createHash("sha256").update(raw).digest("hex")).toBe(DRAFT_FILE_SHA256);     // the file itself
    const draft = JSON.parse(raw.toString("utf8"));
    expect(draft.source_sha256).toBe("1353f61f0d706199d86b6802eab83cabbb5195b2d99fb35bca20f5e4f83db786");
    expect(draft.vectors).toHaveLength(3);
    for (const v of draft.vectors) {
      const key = await C.hybridFromSeed(b(v.sk));
      expect(hex(C.hybridPublic(key))).toBe(v.pk);
      expect(hex(await C.xwingDecapsulate(key, b(v.ct)))).toBe(v.ss);
      const { ct, ss } = await C.xwingEncapsulate(b(v.pk), b(v.eseed));
      expect([hex(ct), hex(ss)]).toEqual([v.ct, v.ss]);
    }
  });

  it("encapsulates at random, both halves fresh, and the key decapsulates it", async () => {
    const { key, pub } = await pair();
    const one = await C.xwingEncapsulate(pub);
    const two = await C.xwingEncapsulate(pub);
    expect(one.ct).toHaveLength(C.HYBRID_CT_LEN);
    expect(hex(one.ct.subarray(0, 1088))).not.toBe(hex(two.ct.subarray(0, 1088)));
    expect(hex(one.ct.subarray(1088))).not.toBe(hex(two.ct.subarray(1088)));
    expect(hex(await C.xwingDecapsulate(key, one.ct))).toBe(hex(one.ss));
  });
});

describe("a post-quantum key held", () => {
  it("is opaque: only hybridFromSeed makes one, and its public key comes out as a copy", async () => {
    const { key, pub } = await pair(3);
    expect(Object.keys(key)).toEqual(["hybrid"]);
    expect(Object.isFrozen(key)).toBe(true);
    const { ct, ss } = await C.xwingEncapsulate(pub);
    const copy = C.hybridPublic(key);
    copy.fill(0);                                                                // changing the copy changes nothing
    expect(hex(C.hybridPublic(key))).toBe(hex(pub));
    expect(hex(await C.xwingDecapsulate(key, ct))).toBe(hex(ss));
    const forged = Object.freeze({ hybrid: true as const });                     // the same shape, not made here
    expect(await refusal(C.xwingDecapsulate(forged, ct))).toBe(C.PUT_AWAY);
    expect(await refusal(C.openEnvelope2(forged, await C.sealEnvelope2(pub, Uint8Array.of(1), "s"), "s"))).toBe(C.PUT_AWAY);
    expect(() => C.hybridPublic(forged)).toThrow(C.PUT_AWAY);
  });

  it("is put away when forgotten: its ML-KEM half zeroed, and every later use refused as locked", async () => {
    const { key, pub } = await pair(4);
    const sealed = await C.sealEnvelope2(pub, Uint8Array.of(1, 2), "s");
    expect(Array.from(await C.openEnvelope2(key, sealed, "s"))).toEqual([1, 2]);
    C.forgetHybrid(key);
    expect(await refusal(C.openEnvelope2(key, sealed, "s"))).toBe(C.PUT_AWAY);
    expect(await refusal(C.xwingDecapsulate(key, new Uint8Array(1120)))).toBe(C.PUT_AWAY);
    expect(() => C.hybridPublic(key)).toThrow(C.PUT_AWAY);
    C.forgetHybrid(key);                                                         // twice is no fault
  });
});

describe("the key check", () => {
  it("takes a post-quantum key and refuses one of another length, a coefficient too large, or an X25519 half of low order", async () => {
    const { pub } = await pair(5);
    expect(await C.usableHybridKey(pub)).toBe(true);
    const bad = [pub.subarray(1), C.concat(pub, Uint8Array.of(0)), C.concat(pub.subarray(0, 1184), new Uint8Array(32)),
      C.concat(pub.subarray(0, 1184), Uint8Array.of(1), new Uint8Array(31))];
    const overflow = pub.slice();
    overflow[0] = 0x01;
    overflow[1] = (overflow[1] & 0xf0) | 0x0d;                                   // the first coefficient is 3,329
    bad.push(overflow);
    for (const key of bad) {
      expect(await C.usableHybridKey(key)).toBe(false);
      expect(await refusal(C.xwingEncapsulate(key))).toMatch(/post-quantum key|could be agreed/);
      expect(await refusal(C.sealEnvelope2(key, Uint8Array.of(1), "s"))).toMatch(/post-quantum key|could be agreed/);
    }
    expect(await C.usableHybridKey("text")).toBe(false);
    expect(await refusal(C.xwingEncapsulate(pub, new Uint8Array(63)))).toMatch(/eseed is 64 bytes/);
    expect(await refusal(C.hybridFromSeed(new Uint8Array(31)))).toMatch(/32-byte seed/);
  });
});

describe("form 2", () => {
  it("is 0x02, the key_id, the ciphertext and the nonce, opens with the key, and is bound to its slot", async () => {
    const { key, pub } = await pair(8);
    const plain = C.utf8('{"text":"hello"}');
    const text = await C.sealEnvelope2(pub, plain, "one slot");
    const read = C.envelopeForm(text)!;
    expect(read.form).toBe(2);
    expect(read.body).toHaveLength(C.OVERHEAD2 + plain.length);
    expect(C.OVERHEAD2).toBe(1165);
    expect(hex(read.body.subarray(1, 17))).toBe(hex(sha256(pub).subarray(0, 16)));
    expect(new TextDecoder().decode(await C.openEnvelope2(key, text, "one slot"))).toBe('{"text":"hello"}');
    expect(await refusal(C.openEnvelope2(key, text, "another slot"))).toBe(C.DOES_NOT_OPEN);
    expect(await C.sealEnvelope2(pub, plain, "one slot")).not.toBe(text);
    expect(C.envelopeBytes(text)).toEqual(read.body);                            // every form the page reads
    expect(C.envelopeKeyId(text)).toBe(hex(read.body.subarray(1, 17)));
    const classical = await C.newPair(false);
    expect(await refusal(C.openEnvelope(classical.priv, classical.pub, text, "one slot"))).toBe(C.OTHER_FORM);
  });

  it("refuses a changed form byte, key_id, ciphertext, nonce or tag, and another key", async () => {
    const { key, pub } = await pair(9);
    const text = await C.sealEnvelope2(pub, Uint8Array.of(7, 7, 7), "s");
    const body = C.envelopeForm(text)!.body;
    const changed = (at: number, value?: number) => { const c = body.slice(); c[at] = value ?? c[at] ^ 1; return frame(c); };
    expect(await refusal(C.openEnvelope2(key, changed(0, 1), "s"))).toBe(C.OTHER_FORM);
    expect(await refusal(C.openEnvelope2(key, changed(0, 3), "s"))).toBe(C.OTHER_FORM);
    expect(await refusal(C.openEnvelope2(key, changed(0, 4), "s"))).toBe(C.NOT_ENVELOPE);
    expect(await refusal(C.openEnvelope2(key, changed(1), "s"))).toBe(C.OTHER_KEY);
    for (const at of [17, 17 + 1087, 1105, 1136, 1137, 1148, 1149, body.length - 1]) {
      expect([C.DOES_NOT_OPEN, C.LOW_ORDER], String(at)).toContain(await refusal(C.openEnvelope2(key, changed(at), "s")));
    }
    expect(await refusal(C.openEnvelope2((await pair(10)).key, text, "s"))).toBe(C.OTHER_KEY);
  });

  it("never opens by X25519 alone: an envelope whose key leaves out ML-KEM's secret is refused", async () => {
    const { key, pub } = await pair(11);
    // a form-2 frame sealed with ss from the X25519 half alone, as a fall back would derive it
    const eph = C.random(32);
    const ctX = x25519Public(eph);
    const ssX = new Uint8Array(N.diffieHellman({
      privateKey: N.createPrivateKey({ key: Buffer.concat([PKCS8_HEAD, eph]), format: "der", type: "pkcs8" }),
      publicKey: N.createPublicKey({ key: Buffer.concat([Buffer.from("302a300506032b656e032100", "hex"), pub.subarray(1184)]), format: "der", type: "spki" }),
    }));
    const { ct } = await C.xwingEncapsulate(pub);
    const header = C.concat(Uint8Array.of(2), sha256(pub).subarray(0, 16), ct.subarray(0, 1088), ctX, C.random(12));
    for (const ss of [ssX, sha256(C.concat(ssX, ctX, pub.subarray(1184)))]) {
      const k = hkdf(ss, "proofpane-e2e/2 content", 32, sha256(pub));
      const cipher = N.createCipheriv("aes-256-gcm", k, header.subarray(1137));
      cipher.setAAD(C.concat(header, C.utf8("s")));
      const sealed = Buffer.concat([cipher.update(Uint8Array.of(1, 2, 3)), cipher.final(), cipher.getAuthTag()]);
      expect(await refusal(C.openEnvelope2(key, frame(C.concat(header, sealed)), "s"))).toBe(C.DOES_NOT_OPEN);
    }
  });

  it("derives its key as Node's own crypto derives it", async () => {
    const { pub } = await pair(12);
    const eseed = C.random(64);
    const nonce = C.random(12);
    const text = await C.sealEnvelope2(pub, C.utf8("plain"), "the slot", { eseed, nonce });
    const body = C.envelopeForm(text)!.body;
    const ss = await C.xwingEncapsulate(pub, eseed);
    expect(hex(body.subarray(17, 1137))).toBe(hex(ss.ct));
    expect(hex(body.subarray(1137, 1149))).toBe(hex(nonce));
    const k = hkdf(ss.ss, "proofpane-e2e/2 content", 32, sha256(pub));
    const d = N.createDecipheriv("aes-256-gcm", k, nonce);
    d.setAAD(C.concat(body.subarray(0, 1149), C.utf8("the slot")));
    d.setAuthTag(body.subarray(body.length - 16));
    expect(Buffer.concat([d.update(body.subarray(1149, body.length - 16)), d.final()]).toString()).toBe("plain");
    expect(await refusal(C.sealEnvelope2(pub, C.utf8("x"), "s", { eseed, nonce: C.random(11) }))).toMatch(/nonce is 12 bytes/);
  });
});

describe("envelopeForm", () => {
  it("reads forms 1, 2 and 3 each with its overhead, and nothing else", () => {
    for (const [form, overhead] of [[1, 77], [2, 1165], [3, 45]] as const) {
      const least = new Uint8Array(overhead);
      least[0] = form;
      expect(C.envelopeForm(frame(least))?.form).toBe(form);
      expect(C.envelopeForm(frame(least.subarray(0, overhead - 1)))).toBeNull();
    }
    for (const other of [0, 4, 255]) {
      const body = new Uint8Array(1300);
      body[0] = other;
      expect(C.envelopeForm(frame(body))).toBeNull();
    }
    const one = new Uint8Array(100);
    one[0] = 1;
    for (const text of [frame(one) + "=", frame(one).slice(0, -1) + "!", "e2e1.", 7, null, "E2E1." + C.b64u(one)]) {
      expect(C.envelopeForm(text)).toBeNull();
    }
  });
});

describe("a conversation's key and its messages", () => {
  it("seals the key in form 2, exactly 1,197 bytes, bound to its list", async () => {
    const { key, pub } = await pair(13);
    const tk = C.random(32);
    const slot = talkKeySlot(ACCOUNT, "lnk", "s1", 2, 5);
    expect(slot).toBe("proofpane-e2e/1|acct-pq|lnk|talk-key|s1|2|5");
    const sealed = await C.sealTalkKey(pub, tk, slot);
    expect(C.envelopeForm(sealed)!.body).toHaveLength(C.TALK_KEY_SEALED_LEN);
    expect(C.TALK_KEY_SEALED_LEN).toBe(1197);
    const id = hex(hkdf(tk, "proofpane-e2e/3 talk key id", 16));
    expect((await C.talkKeyOf(tk)).id).toBe(id);
    expect((await C.openTalkKey(key, sealed, slot, id)).id).toBe(id);
    for (const other of [talkKeySlot(ACCOUNT, "lnk", "s1", 3, 5), talkKeySlot(ACCOUNT, "lnk", "s1", 2, 4), talkKeySlot(ACCOUNT, "lnk", "s2", 2, 5)]) {
      expect(await refusal(C.openTalkKey(key, sealed, other, id))).toBe(C.DOES_NOT_OPEN);
    }
    expect(await refusal(C.openTalkKey(key, sealed, slot, "0".repeat(32)))).toMatch(/not the one it names/);
    const longer = await C.sealEnvelope2(pub, C.random(33), slot);
    expect(await refusal(C.openTalkKey(key, longer, slot, id))).toMatch(/not in the form the page reads/);
    const classical = (await C.newPair()).pub;
    expect(await refusal(C.sealTalkKey(classical, tk, slot))).toMatch(/post-quantum key alone/);
    expect(await refusal(C.sealTalkKey(pub, tk.subarray(1), slot))).toMatch(/32 bytes/);
    for (const n of [31, 33]) expect(await refusal(C.talkKeyOf(C.random(n)))).toMatch(/32 bytes/);
  });

  it("seals each message in form 3 under a key of its own, bound to its place", async () => {
    const tkBytes = C.random(32);
    const tk = await C.talkKeyOf(tkBytes.slice());
    const first = talkSlot(ACCOUNT, "lnk", "s1", 0, 2, 5, "owner", 1790000000, 1790000000000);
    const second = talkSlot(ACCOUNT, "lnk", "s1", 1, 2, 5, "session", null, null);
    const plain = C.utf8('{"text":"how is the build?"}');
    const text = await C.sealTalkMessage(tk, plain, first);
    const read = C.envelopeForm(text)!;
    expect(read.form).toBe(3);
    expect(read.body).toHaveLength(C.OVERHEAD3 + plain.length);
    expect(C.OVERHEAD3).toBe(45);
    expect(hex(read.body.subarray(1, 17))).toBe(tk.id);
    expect(new TextDecoder().decode(await C.openTalkMessage(tk, text, first))).toBe('{"text":"how is the build?"}');
    expect(await refusal(C.openTalkMessage(tk, text, second))).toBe(C.DOES_NOT_OPEN);
    expect(await refusal(C.openTalkMessage(await C.talkKeyOf(C.random(32)), text, first))).toBe(C.OTHER_KEY);
    const { pub } = await pair(14);
    expect(await refusal(C.openTalkMessage(tk, await C.sealEnvelope2(pub, plain, first), first))).toBe(C.OTHER_FORM);
    // its key is HKDF(tk, "talk message|" + slot), and tk itself does not open it
    const nonce = read.body.subarray(17, 29);
    const open = (k: Uint8Array) => {
      const d = N.createDecipheriv("aes-256-gcm", k, nonce);
      d.setAAD(C.concat(read.body.subarray(0, 29), C.utf8(first)));
      d.setAuthTag(read.body.subarray(read.body.length - 16));
      return Buffer.concat([d.update(read.body.subarray(29, read.body.length - 16)), d.final()]).toString();
    };
    expect(open(hkdf(tkBytes, `proofpane-e2e/3 talk message|${first}`))).toBe('{"text":"how is the build?"}');
    expect(() => open(tkBytes)).toThrow();
    expect(await C.sealTalkMessage(tk, plain, first)).not.toBe(text);
    expect(await refusal(C.sealTalkMessage(tk, plain, first, C.random(13)))).toMatch(/nonce is 12 bytes/);
  });
});

describe("grants to a post-quantum key", () => {
  it("are form 2, made again byte for byte by whoever holds the master key, and open to it", async () => {
    const { key, pub } = await pair(15);
    const mk = C.random(32);
    const grant = await C.sealGrant(pub, mk, ACCOUNT, "cred-1", MK_ID);
    expect(C.envelopeForm(grant)!.form).toBe(2);
    expect(await C.sealGrant(pub, mk, ACCOUNT, "cred-1", MK_ID)).toBe(grant);
    expect(await C.isGrantOf(grant, pub, mk, ACCOUNT, "cred-1", MK_ID)).toBe(true);
    for (const [m, to, id] of [[C.random(32), "cred-1", MK_ID], [mk, "cred-2", MK_ID], [mk, "cred-1", "f".repeat(32)]] as const) {
      expect(await C.isGrantOf(grant, pub, m, ACCOUNT, to, id)).toBe(false);
    }
    expect(hex(await C.openGrantHybrid(key, grant, ACCOUNT, "cred-1", MK_ID))).toBe(hex(mk));
    expect(await refusal(C.openGrantHybrid(key, grant, ACCOUNT, "cred-2", MK_ID))).toBe(C.DOES_NOT_OPEN);
    const long = await C.sealEnvelope2(pub, C.random(33), C.AAD.grant(ACCOUNT, "cred-1", MK_ID));
    expect(await refusal(C.openGrantHybrid(key, long, ACCOUNT, "cred-1", MK_ID))).toMatch(/32 bytes/);
  });

  it("derive their eseed and nonce from the master key, salted with the key's sha256, over the grant's AAD text", async () => {
    const { pub } = await pair(16);
    const mk = C.random(32);
    const aad = C.AAD.grant(ACCOUNT, "recovery", MK_ID);
    const body = C.envelopeForm(await C.sealGrant(pub, mk, ACCOUNT, "recovery", MK_ID))!.body;
    const derived = hkdf(mk, `proofpane-e2e/2 grant ephemeral|${aad}`, 76, sha256(pub));
    expect(hex(body.subarray(1105, 1137))).toBe(hex(x25519Public(derived.subarray(32, 64))));      // ct_X
    expect(hex(body.subarray(1137, 1149))).toBe(hex(derived.subarray(64)));                        // the nonce
    const [ctM] = await Kem.encapsulate(pub.subarray(0, 1184), derived.subarray(0, 32));
    expect(hex(body.subarray(17, 1105))).toBe(hex(ctM));                                           // ct_M
  });

  it("take only the randomness of their recipient's form", async () => {
    const { pub } = await pair(17);
    const classical = await C.newPair(false);
    const mk = C.random(32);
    const xFixed = { ephemeral: classical.priv, ephemeralPublic: classical.pub, nonce: C.random(12) };
    expect(await refusal(C.sealGrant(pub, mk, ACCOUNT, "c", MK_ID, xFixed))).toMatch(/eseed and a nonce/);
    expect(await refusal(C.sealGrant(classical.pub, mk, ACCOUNT, "c", MK_ID, { eseed: C.random(64), nonce: C.random(12) })))
      .toMatch(/X25519 ephemeral key/);
    expect(C.envelopeForm(await C.sealGrant(classical.pub, mk, ACCOUNT, "c", MK_ID))!.form).toBe(1);
  });
});

describe("the keys' wraps", () => {
  it("wraps a content key's seed in 60 bytes, bound to the account, the key and the master key", async () => {
    const mk = C.random(32);
    const m = await C.masterOf(mk.slice(), ACCOUNT);
    const { seed, pub } = await pair(18);
    const keyId = await C.keyIdOf(pub);
    const wrapped = await C.wrapContentSeed(m, seed, keyId, ACCOUNT);
    expect(C.unb64u(wrapped)).toHaveLength(C.WRAP_LEN);
    expect(C.WRAP_LEN).toBe(60);
    expect(hex(C.hybridPublic(await C.unwrapContentSeed(m, wrapped, keyId, ACCOUNT)))).toBe(hex(pub));
    const subkey = hkdf(mk, `proofpane-e2e/1 content-wrap|${ACCOUNT}`);       // the classical subkey, unchanged
    expect(openWrap(subkey, wrapped, `proofpane-e2e/2 content key|${ACCOUNT}|${keyId}|${m.id}`)).toBe(hex(seed));
    expect(await refusal(C.unwrapContentSeed(m, wrapped, "0".repeat(32), ACCOUNT))).toBe(C.DOES_NOT_OPEN);
    expect(await refusal(C.unwrapContentSeed(await C.masterOf(C.random(32), ACCOUNT), wrapped, keyId, ACCOUNT))).toBe(C.DOES_NOT_OPEN);
    expect(await refusal(C.unwrapContentSeed(m, wrapped, keyId, "another"))).toBe(C.DOES_NOT_OPEN);
    const other = await C.wrapContentSeed(m, C.random(32), keyId, ACCOUNT);   // another seed under this key's name
    expect(await refusal(C.unwrapContentSeed(m, other, keyId, ACCOUNT))).toBe(C.OTHER_RECORD);
    const pkcs8Sized = C.b64u(C.random(76));                                     // a classical wrap: refused by its length
    expect(await refusal(C.unwrapContentSeed(m, pkcs8Sized, keyId, ACCOUNT))).toBe(C.DOES_NOT_OPEN);
    expect(await refusal(C.wrapContentSeed(m, C.random(31), keyId, ACCOUNT))).toMatch(/32-byte seed/);
    // a wrap that opens, under the right key and AAD, to 48 bytes: refused for its length before it is opened
    const long = gcm(subkey, C.random(48), `proofpane-e2e/2 content key|${ACCOUNT}|${keyId}|${m.id}`);
    expect(await refusal(C.openContentSeed(m, long, keyId, ACCOUNT))).toBe(C.DOES_NOT_OPEN);
  });

  it("wraps a retired classical content key as its scalar, under an AAD of its own", async () => {
    const mk = C.random(32);
    const m = await C.masterOf(mk.slice(), ACCOUNT);
    const scalar = C.random(32);
    const ek = await C.privateFromSeed(scalar, true);
    const keyId = await C.keyIdOf(await C.publicOf(ek));
    expect(hex(await C.scalarOf(ek, keyId))).toBe(hex(scalar));
    const wrapped = await C.wrapClassicalScalar(m, scalar, keyId, ACCOUNT);
    expect(C.unb64u(wrapped)).toHaveLength(60);
    expect(hex(await C.openClassicalScalar(m, wrapped, keyId, ACCOUNT))).toBe(hex(scalar));
    const back = await C.unwrapClassicalScalar(m, wrapped, keyId, ACCOUNT);
    expect(back.extractable).toBe(false);
    expect(hex(await C.publicOf(back))).toBe(hex(await C.publicOf(ek)));
    // one kind's wrap does not open as the other's
    expect(await refusal(C.unwrapContentSeed(m, wrapped, keyId, ACCOUNT))).toBe(C.DOES_NOT_OPEN);
    const seedWrap = await C.wrapContentSeed(m, scalar, keyId, ACCOUNT);
    expect(await refusal(C.unwrapClassicalScalar(m, seedWrap, keyId, ACCOUNT))).toBe(C.DOES_NOT_OPEN);
    const other = await C.wrapClassicalScalar(m, C.random(32), keyId, ACCOUNT);
    expect(await refusal(C.openClassicalScalar(m, other, keyId, ACCOUNT))).toBe(C.OTHER_RECORD);
    expect(await refusal(C.unwrapClassicalScalar(m, other, keyId, ACCOUNT))).toBe(C.OTHER_RECORD);
    // under the content-wrap subkey, with the AAD below, checked with Node's crypto
    const subkey = hkdf(mk, `proofpane-e2e/1 content-wrap|${ACCOUNT}`);
    const aad = `proofpane-e2e/2 content key x25519|${ACCOUNT}|${keyId}|${m.id}`;
    expect(openWrap(subkey, wrapped, aad)).toBe(hex(scalar));
    // a wrap that opens to 48 bytes, and a scalar of another length to wrap: refused for their lengths
    expect(await refusal(C.openClassicalScalar(m, gcm(subkey, C.random(48), aad), keyId, ACCOUNT))).toBe(C.DOES_NOT_OPEN);
    for (const n of [31, 33]) expect(await refusal(C.wrapClassicalScalar(m, C.random(n), keyId, ACCOUNT))).toMatch(/32 bytes/);
  });

  it("reads a classical key's scalar from its PKCS#8 in either version, checked by its key_id", async () => {
    const scalar = C.random(32);
    const pub = x25519Public(scalar);
    const keyId = await C.keyIdOf(pub);
    const v0 = C.concat(PKCS8_HEAD, scalar);                                     // RFC 8410's, as Chrome and Node export
    expect(hex(await C.scalarFromPkcs8(v0, keyId))).toBe(hex(scalar));
    // RFC 5958's version 1, the public key after the private one, as another browser may export it
    const v1 = C.concat(Uint8Array.of(0x30, 0x51, 0x02, 0x01, 0x01), PKCS8_HEAD.subarray(5), scalar, Uint8Array.of(0x81, 0x21, 0x00), pub);
    expect(v1).toHaveLength(0x53);
    expect(hex(await C.scalarFromPkcs8(v1, keyId))).toBe(hex(scalar));
    expect(await refusal(C.scalarFromPkcs8(v0, "0".repeat(32)))).toBe(C.OTHER_RECORD);
    const ed25519 = new Uint8Array(N.generateKeyPairSync("ed25519").privateKey.export({ format: "der", type: "pkcs8" }) as Buffer);
    expect(ed25519).toHaveLength(48);
    expect(await refusal(C.scalarFromPkcs8(ed25519, keyId))).toMatch(/not an X25519 private key/);
    const wrongLength = v0.slice();
    wrongLength[1] = 0x2f;
    const version2 = v0.slice();
    version2[4] = 2;
    const innerTag = v0.slice();
    innerTag[13] = 0x21;                                                         // the outer OCTET STRING's length
    const short = C.concat(Uint8Array.of(0x30, 0x12), PKCS8_HEAD.subarray(2), scalar.subarray(0, 4));   // consistent, too short
    for (const der of [wrongLength, version2, innerTag, short, v0.subarray(0, 47)]) {
      expect(await refusal(C.scalarFromPkcs8(der, keyId))).toMatch(/not an X25519 private key/);
    }
    const ed = (await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign"])) as CryptoKeyPair;
    expect(await refusal(C.scalarOf(ed.privateKey, keyId))).toMatch(/not an X25519 private key/);
  });

  it("wraps a credential's seed in 60 bytes under its kek, the anchor in the AAD, and unwraps it against the roster", async () => {
    const prf = C.random(32);
    const kek = await C.credentialKek(prf.slice(), ACCOUNT, "cred-1");
    const { seed, pub } = await pair(19);
    const wrapped = await C.sealCredentialSeed(kek, seed, MK_ID, ACCOUNT, "cred-1");
    expect(C.unb64u(wrapped)).toHaveLength(60);
    expect(hex(await C.openCredentialSeed(kek, wrapped, MK_ID, ACCOUNT, "cred-1"))).toBe(hex(seed));
    // kek as the classical keys derive it; the AAD below, with the anchor
    const aad = `proofpane-e2e/2 credential|${ACCOUNT}|cred-1|${MK_ID}`;
    expect(openWrap(hkdf(prf, `proofpane-e2e/1 credential|${ACCOUNT}|cred-1`), wrapped, aad)).toBe(hex(seed));
    expect(await refusal(C.openCredentialSeed(kek, wrapped, "f".repeat(32), ACCOUNT, "cred-1"))).toBe(C.DOES_NOT_OPEN);   // the anchor changed
    expect(await refusal(C.openCredentialSeed(kek, wrapped, MK_ID, ACCOUNT, "cred-2"))).toBe(C.DOES_NOT_OPEN);
    expect(await refusal(C.openCredentialSeed(kek, wrapped, MK_ID, "another", "cred-1"))).toBe(C.DOES_NOT_OPEN);
    expect(await refusal(C.openCredentialSeed(kek, wrapped, "not hex", ACCOUNT, "cred-1"))).toMatch(/anchor is an mk_id/);
    expect(await refusal(C.sealCredentialSeed(kek, seed, "NOT", ACCOUNT, "cred-1"))).toMatch(/anchor is an mk_id/);
    expect(await refusal(C.openCredentialSeed(kek, C.b64u(C.random(145)), MK_ID, ACCOUNT, "cred-1"))).toBe(C.DOES_NOT_OPEN);
    const long = gcm(hkdf(prf, `proofpane-e2e/1 credential|${ACCOUNT}|cred-1`), C.random(48), aad);
    expect(await refusal(C.openCredentialSeed(kek, long, MK_ID, ACCOUNT, "cred-1"))).toBe(C.DOES_NOT_OPEN);
    for (const n of [31, 33]) expect(await refusal(C.sealCredentialSeed(kek, C.random(n), MK_ID, ACCOUNT, "cred-1"))).toMatch(/32-byte seed/);
    // unlock's check: the seed's public key, both halves, is the roster's
    const key = await C.unwrapCredentialSeed(kek, wrapped, MK_ID, ACCOUNT, "cred-1", pub);
    expect(hex(C.hybridPublic(key))).toBe(hex(pub));
    const otherHalf = C.concat(pub.subarray(0, 1184), (await pair(20)).pub.subarray(1184));
    const otherMlkem = C.concat((await pair(21)).pub.subarray(0, 1184), pub.subarray(1184));
    for (const roster of [otherHalf, otherMlkem, pub.subarray(0, 1215), (await pair(22)).pub]) {
      expect(await refusal(C.unwrapCredentialSeed(kek, wrapped, MK_ID, ACCOUNT, "cred-1", roster))).toBe(C.OTHER_RECORD);
    }
  });
});

describe("the recovery key and its proof", () => {
  it("derives the recovery key's seed from the code with its own info", async () => {
    const code = C.random(20);
    const rk = await C.recoveryHybrid(code, ACCOUNT);
    const seed = hkdf(code, `proofpane-e2e/2 recovery|${ACCOUNT}`);
    expect(hex(C.hybridPublic(rk))).toBe(hex(C.hybridPublic(await C.hybridFromSeed(seed))));
    const old = hkdf(code, `proofpane-e2e/1 recovery|${ACCOUNT}`);
    expect(hex(C.hybridPublic(rk))).not.toBe(hex(C.hybridPublic(await C.hybridFromSeed(old))));
  });

  it("answers the server's encapsulation with the recovery proof's HMAC", async () => {
    const rk = await C.recoveryHybrid(C.random(20), ACCOUNT);
    const { ct, ss } = await C.xwingEncapsulate(C.hybridPublic(rk));             // the server's part
    const nonce = C.random(32);
    const proof = await C.recoveryProofHybrid(rk, ct, nonce, "e2e-reseal");
    const mac = N.createHmac("sha256", hkdf(ss, "proofpane-e2e/2 recovery proof")).update(C.concat(nonce, C.utf8("e2e-reseal"))).digest();
    expect(proof).toBe(C.b64u(new Uint8Array(mac)));
    expect(await C.recoveryProofHybrid(rk, ct, nonce, "e2e-cancel")).not.toBe(proof);
    const other = await C.recoveryHybrid(C.random(20), ACCOUNT);
    expect(await C.recoveryProofHybrid(other, ct, nonce, "e2e-reseal")).not.toBe(proof);
  });
});
