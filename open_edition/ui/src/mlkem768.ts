// ML-KEM-768 (FIPS 203), and the SHA3-256 and SHAKE256 the page's X-Wing needs, from the `mlkem` package at exactly
// 2.7.0. This is the only module that names the library, so changing or replacing it
// changes this file alone, and the vectors (draft-11's for X-Wing, Wycheproof's for ML-KEM-768) hold every change to
// the same bytes. The library zeroes its own working buffers after each call; what it returns reaches the caller with
// no other copy left (own), so the caller's zeroing of a secret reaches it.
import { createMlKem768, sha3_256, shake256, type MlKemInterface } from "mlkem";

/** Bytes over an ArrayBuffer of their own, which WebCrypto takes. */
export type Bytes = Uint8Array<ArrayBuffer>;

export const PUBLIC_LEN = 1184;         // an encapsulation key
export const CIPHERTEXT_LEN = 1088;
export const SECRET_LEN = 32;
export const SEED_LEN = 64;             // d || z, which KeyGen_internal takes
export const MESSAGE_LEN = 32;          // the randomness of one encapsulation
const Q = 3329;
const COEFFICIENT_BYTES = 1152;         // 768 coefficients of 12 bits; then rho, 32 bytes

let made: Promise<MlKemInterface> | null = null;
const kem = (): Promise<MlKemInterface> => (made ??= createMlKem768());

/** The library's result as Bytes, with no copy of a secret left behind: the array itself when it is the whole of an
 *  ArrayBuffer of its own (mlkem 2.7.0 returns fresh arrays, a `slice` or a `new Uint8Array`), so the caller's zeroing
 *  reaches the only copy; otherwise a copy, the original zeroed. */
function own(b: Uint8Array): Bytes {
  if (b.byteOffset === 0 && b.byteLength === b.buffer.byteLength && Object.prototype.toString.call(b.buffer) === "[object ArrayBuffer]") {
    return b as Bytes;
  }
  const copy = new Uint8Array(b);
  b.fill(0);
  return copy;
}

/** FIPS 203's check of an encapsulation key (section 7.2): 1,184 bytes whose 768 coefficients, twelve bits each in the
 *  first 1,152, are each below q = 3,329. Written here, not taken from the library, so a key is judged the same way
 *  whatever library encapsulates to it. */
export function validPublic(pk: Uint8Array): boolean {
  if (pk.length !== PUBLIC_LEN) return false;
  for (let i = 0; i < COEFFICIENT_BYTES; i += 3) {
    if ((pk[i] | ((pk[i + 1] & 0x0f) << 8)) >= Q || ((pk[i + 1] >> 4) | (pk[i + 2] << 4)) >= Q) return false;
  }
  return true;
}

/** A key pair from its 64-byte seed d || z (KeyGen_internal): [the encapsulation key, the decapsulation key]. */
export async function keysFromSeed(seed: Uint8Array): Promise<[Bytes, Bytes]> {
  if (seed.length !== SEED_LEN) throw new Error("An ML-KEM-768 seed is 64 bytes.");
  const [pk, dk] = (await kem()).deriveKeyPair(seed);
  return [own(pk), own(dk)];
}

/** Encapsulation to `pk`: [the ciphertext, the shared secret]. `message`, 32 bytes, makes it derandomized (a grant, or
 *  a vector); without it the library draws it from the browser's random source. Throws for a key that fails the
 *  check (validPublic) or is of another length. */
export async function encapsulate(pk: Uint8Array, message?: Uint8Array): Promise<[Bytes, Bytes]> {
  if (!validPublic(pk)) throw new Error("This is not an ML-KEM-768 encapsulation key.");
  if (message !== undefined && message.length !== MESSAGE_LEN) throw new Error("An encapsulation's message is 32 bytes.");
  const [ct, ss] = (await kem()).encap(pk, message);
  return [own(ct), own(ss)];
}

/** Decapsulation: the shared secret. A ciphertext not made for this key gives a pseudorandom secret instead of an error
 *  (implicit rejection), so the AES-GCM tag after it is what decides. Throws for inputs of the wrong lengths. */
export async function decapsulate(ct: Uint8Array, dk: Uint8Array): Promise<Bytes> {
  if (ct.length !== CIPHERTEXT_LEN) throw new Error("An ML-KEM-768 ciphertext is 1,088 bytes.");
  return own((await kem()).decap(ct, dk));
}

export function sha3(bytes: Uint8Array): Bytes {
  return own(sha3_256(bytes));
}

export function shake(bytes: Uint8Array, length: number): Bytes {
  return own(shake256(bytes, { dkLen: length }));
}
