import { beforeEach, describe, expect, it, vi } from "vitest";

const calls: [string, unknown][] = [];
vi.mock("./api", () => ({
  api: vi.fn(async (path: string, opts?: { body?: unknown }) => {
    calls.push([path, opts?.body]);
    if (path === "/api/check/signature") return { ok: true, key: "sha256:k" };
    if (path === "/api/check/manifest") return { ok: true, errors: [] };
    if (path === "/api/check/chain") return { ok: true, lines: 3, head: "sha256:h", errors: [] };
    if (path.startsWith("/api/ade/workspace/finishes/")) return path.endsWith("/none") ? { manifest: null, signature: null } : { manifest, signature };
    throw new Error("unexpected " + path);
  }),
}));

const { checkFiles, checkFinish, isManifest, isSignature } = await import("./records");
const manifest = { obligations: [], decision: { verify: "success" } };
const signature = { alg: "a", purpose: "p", key: "k", sig: "s" };
const j = (v: unknown) => JSON.stringify(v);

beforeEach(() => { calls.length = 0; });

describe("what a dropped file is, and the checks it gets", () => {
  it("a Finish record: its signature and its manifest", async () => {
    const r = await checkFiles([j({ id: "f", manifest, signature })]);
    expect(r.what).toBe("record");
    expect(calls).toEqual([["/api/check/signature", { value: manifest, signature }], ["/api/check/manifest", { text: j(manifest) }]]);
  });
  it("a manifest and its signature, in either order", async () => {
    for (const files of [[j(manifest), j(signature)], [j(signature), j(manifest)]]) {
      calls.length = 0;
      expect((await checkFiles(files)).what).toBe("pair");
      expect(calls[0]).toEqual(["/api/check/signature", { value: manifest, signature }]);
    }
  });
  it("a manifest alone, an audit chain, and what is refused", async () => {
    expect((await checkFiles([j(manifest)])).what).toBe("manifest");
    expect(calls).toEqual([["/api/check/manifest", { text: j(manifest) }]]);
    calls.length = 0;
    expect((await checkFiles(['{"seq": 1}\n{"seq": 2}\n'])).what).toBe("chain");
    expect(calls).toEqual([["/api/check/chain", { text: '{"seq": 1}\n{"seq": 2}\n' }]]);
    await expect(checkFiles([j({ manifest, signature: null })])).rejects.toThrow(/no signature/);
    await expect(checkFiles([j(manifest), j(manifest)])).rejects.toThrow(/manifest and its signature/);
    await expect(checkFiles(["a", "b", "c"])).rejects.toThrow(/one file/);
    await expect(checkFiles([])).rejects.toThrow(/one file/);
  });
  it("a finish kept by the workspace, by its id", async () => {
    expect((await checkFinish("abc")).what).toBe("record");
    await expect(checkFinish("none")).rejects.toThrow(/nothing signed/);
  });
  it("tells a signature and a manifest by their shape", () => {
    expect(isSignature(signature)).toBe(true);
    expect(isSignature({ ...signature, extra: "x" })).toBe(false);
    expect(isSignature({ ...signature, sig: 5 })).toBe(false);
    expect(isManifest(manifest)).toBe(true);
    expect(isManifest({ obligations: [] })).toBe(false);
    expect(isManifest(null)).toBe(false);
  });
});
