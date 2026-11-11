// Checking what this deployment signed: a session's Finish record (its manifest and the signature over it), a
// manifest with its detached signature, a manifest alone, or an audit chain. The checks are the server's own
// (tools.py: the same checker as the offline verifier); this only works out which a file is.
import { api } from "./api";

export interface SignatureCheck { ok: boolean; key: string }
export interface ManifestCheck { ok: boolean; digest?: string; decision?: { verify?: string; claims?: string; rule_id?: string | null; because?: string[] }; errors: string[] }
export interface ChainCheck { ok: boolean; lines: number; head: string; errors: string[] }
export type Checked =
  | { what: "record" | "pair"; signature: SignatureCheck; manifest: ManifestCheck }
  | { what: "manifest"; manifest: ManifestCheck }
  | { what: "chain"; chain: ChainCheck };

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => !!v && typeof v === "object" && !Array.isArray(v);
export const isSignature = (v: unknown): boolean =>
  isObj(v) && Object.keys(v).length === 4 && ["alg", "purpose", "key", "sig"].every((k) => typeof v[k] === "string");
export const isManifest = (v: unknown): boolean => isObj(v) && Array.isArray(v.obligations) && isObj(v.decision);

function parse(text: string): unknown {
  try { return JSON.parse(text); } catch { return undefined; }
}

async function both(manifest: unknown, signature: unknown) {
  const [sig, man] = await Promise.all([
    api<SignatureCheck>("/api/check/signature", { method: "POST", body: { value: manifest, signature } }),
    api<ManifestCheck>("/api/check/manifest", { method: "POST", body: { text: JSON.stringify(manifest) } }),
  ]);
  return { signature: sig, manifest: man };
}

/** What one file, or a manifest and its signature as two, is; and what this deployment says of it. */
export async function checkFiles(texts: string[]): Promise<Checked> {
  if (!texts.length || texts.length > 2) throw new Error("Choose one file, or a manifest and its signature.");
  const values = texts.map(parse);
  if (texts.length === 2) {
    const s = values.findIndex(isSignature);
    const m = values.findIndex(isManifest);
    if (s < 0 || m < 0 || s === m) throw new Error("Two files are a manifest and its signature (manifest.sig.json).");
    return { what: "pair", ...(await both(values[m], values[s])) };
  }
  const [v] = values;
  if (isObj(v) && isManifest(v.manifest) && isSignature(v.signature)) return { what: "record", ...(await both(v.manifest, v.signature)) };
  if (isObj(v) && "manifest" in v && v.signature == null) throw new Error("This record has no signature: its change was not verified, so there is nothing signed to check.");
  if (isManifest(v)) {
    return { what: "manifest", manifest: await api<ManifestCheck>("/api/check/manifest", { method: "POST", body: { text: texts[0] } }) };
  }
  return { what: "chain", chain: await api<ChainCheck>("/api/check/chain", { method: "POST", body: { text: texts[0] } }) };
}

/** A Finish record as the workspace keeps it, checked: its signature by this deployment, and its manifest. */
export async function checkFinish(id: string): Promise<Checked> {
  const r = await api<Obj>(`/api/ade/workspace/finishes/${id}`);
  if (!isManifest(r.manifest) || !isSignature(r.signature)) throw new Error("This finish has nothing signed to check.");
  return { what: "record", ...(await both(r.manifest, r.signature)) };
}
