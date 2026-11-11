// @vitest-environment node
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { build } from "vite";
import type { RolldownOutput } from "rolldown";
import { allowed, FILE, notice, OWN, packageDir, packagesOf, problems, SUPPLIED, supplyFor, thirdPartyLicenses, wrap } from "../licenses";

// The page's third-party licenses (open_edition/NOTICE): each build writes THIRD_PARTY_LICENSES.txt beside the page,
// with every npm package its chunks hold, and Proofpane Community's own LICENSE and NOTICE; no package the page bundles
// may carry a license outside ALLOWED or ship no license file of its own (but one SUPPLIED for it).
const UI = join(__dirname, "..");
const KEPT = readFileSync(join(UI, "third-party", "react-remove-scroll-bar", "LICENSE"), "utf8");
const scratch = mkdtempSync(join(tmpdir(), "licenses-"));
afterAll(() => rmSync(scratch, { recursive: true, force: true }));

function pkg(dir: string, meta: Record<string, unknown>, files: Record<string, string> = {}) {
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "package.json"), JSON.stringify(meta));
  for (const [name, text] of Object.entries(files)) writeFileSync(join(dir, name), text);
}

describe("which package a module lies in", () => {
  it("is the name after the last node_modules, with its scope", () => {
    expect(packageDir("/r/node_modules/a/dist/x.js")).toBe("/r/node_modules/a");
    expect(packageDir("/r/node_modules/@s/b/esm/y.js?commonjs-proxy")).toBe("/r/node_modules/@s/b");
    expect(packageDir("/r/node_modules/a/node_modules/c/i.js")).toBe("/r/node_modules/a/node_modules/c");
    expect(packageDir("C:\\r\\node_modules\\a\\i.js")).toBe("C:/r/node_modules/a");
    expect(packageDir("/r/node_modules/a/x.js?from=/r/node_modules/zz/y.js")).toBe("/r/node_modules/a");   // the query left out
    expect(packageDir("\0/r/node_modules/z/x.js?commonjs-proxy")).toBe("/r/node_modules/z");               // a virtual module of z's
    expect(packageDir("\0virtual:thing")).toBeNull();
    expect(packageDir("/r/src/App.tsx")).toBeNull();
    expect(packageDir("/r/node_modules/@s")).toBeNull();
    expect(packageDir("/r/node_modules/")).toBeNull();
  });
});

describe("the packages and their licenses", () => {
  const root = join(scratch, "node_modules");
  pkg(join(root, "a"), { name: "a", version: "1.0.0", license: "MIT" }, { LICENSE: "MIT License\nCopyright (c) A\n", "license.md": "x" });
  pkg(join(root, "@s", "b"), { name: "@s/b", version: "2.0.0", license: { type: "ISC" } }, { "LICENCE.txt": "ISC License, B" });
  pkg(join(root, "a", "node_modules", "c"), { name: "c", version: "3.0.0", licenses: [{ type: "MIT" }, { type: "Apache-2.0" }] },
    { COPYING: "C's terms", "LICENSE-MIT": "C's MIT", NOTICE: "C's notice", "LICENSE_APACHE.txt": "C's Apache", "README.md": "not a license" });
  pkg(join(root, "d"), { name: "d", version: "4.0.0", license: "GPL-3.0-only" }, { LICENSE: "GPL" });
  pkg(join(root, "e"), { name: "e", version: "5.0.0", license: "MIT" });
  pkg(join(root, "react-remove-scroll-bar"), { name: "react-remove-scroll-bar", version: "2.3.8", license: "MIT" });
  pkg(join(root, "f"), { name: "f", version: "6.0.0" }, { LICENSE: "?" });
  pkg(join(root, "g"), { name: "g", version: "1.0.0", license: "MIT" }, { LICENSE: "g one" });
  pkg(join(root, "@s", "b", "node_modules", "g"), { name: "g", version: "2.0.0", license: "MIT" }, { LICENSE: "g two" });
  mkdirSync(join(root, "a", "LICENSE.d"));                                     // a directory is no license file
  const ids = ["a/dist/x.js", "a/dist/y.js?inline", "@s/b/i.js", "a/node_modules/c/i.js", "d/i.js", "e/i.js", "g/i.js",
    "@s/b/node_modules/g/i.js", "react-remove-scroll-bar/dist/i.js", "f/i.js"].map((p) => join(root, p))
    .concat(["\0virtual", join(scratch, "src", "App.tsx")]);

  it("reads each package once, sorted, with its license in each way a package.json gives it", () => {
    const found = packagesOf(ids);
    expect(found.map((p) => `${p.name} ${p.version} ${p.license}`)).toEqual(["@s/b 2.0.0 ISC", "a 1.0.0 MIT", "c 3.0.0 MIT OR Apache-2.0",
      "d 4.0.0 GPL-3.0-only", "e 5.0.0 MIT", "f 6.0.0 UNKNOWN", "g 1.0.0 MIT", "g 2.0.0 MIT", "react-remove-scroll-bar 2.3.8 MIT"]);
    expect(found.find((p) => p.name === "a")!.files.map((f) => f.name)).toEqual(["LICENSE", "license.md"]);
    expect(found.find((p) => p.name === "c")!.files.map((f) => f.name)).toEqual(["COPYING", "LICENSE-MIT", "LICENSE_APACHE.txt", "NOTICE"]);
    expect(found.find((p) => p.name === "react-remove-scroll-bar")!.supplied)
      .toEqual({ note: SUPPLIED["react-remove-scroll-bar"].note, files: [{ name: "LICENSE", text: KEPT }] });
    expect(found.find((p) => p.name === "e")!.supplied).toBeNull();
  });

  it("refuses a license the page does not take, and a package with no license file and none supplied", () => {
    expect(problems(packagesOf(ids))).toEqual([
      "d 4.0.0: its license, GPL-3.0-only, is not one the page takes",
      "e 5.0.0: it ships no license file, and none is supplied for it",
      "f 6.0.0: its license, UNKNOWN, is not one the page takes",
    ]);
  });

  it("stops the build on a package the page may not carry, naming every one, and writes nothing", () => {
    // as the desktop's and the phone's builds do (desktop/licenses.py): the hosted image's build runs no test
    const emitted: string[] = [];
    const context = {
      warn: () => { throw new Error("warned, and the build went on"); },
      error: (message: string) => { throw new Error(message); },
      emitFile: (file: { fileName: string }) => { emitted.push(file.fileName); return file.fileName; },
    };
    const generate = thirdPartyLicenses().generateBundle as unknown as (this: typeof context, options: unknown, bundle: unknown) => void;
    const bundle = (moduleIds: string[]) => ({ "index.js": { type: "chunk", moduleIds } });
    expect(() => generate.call(context, {}, bundle(ids))).toThrow("third-party licenses the page cannot carry:\n"
      + "  d 4.0.0: its license, GPL-3.0-only, is not one the page takes\n"
      + "  e 5.0.0: it ships no license file, and none is supplied for it\n"
      + "  f 6.0.0: its license, UNKNOWN, is not one the page takes");
    expect(emitted).toEqual([]);
    // one such package is enough
    expect(() => generate.call(context, {}, bundle(ids.filter((id) => !/[\\/]node_modules[\\/][ef][\\/]/.test(id)))))
      .toThrow("third-party licenses the page cannot carry:\n  d 4.0.0: its license, GPL-3.0-only, is not one the page takes");
    expect(emitted).toEqual([]);
    // without those three, it writes the notice and Proofpane Community's own two
    generate.call(context, {}, bundle(ids.filter((id) => !/[\\/]node_modules[\\/][def][\\/]/.test(id))));
    expect(emitted).toEqual([FILE, ...OWN]);
  });

  it("supplies its project's kept file only for that version under that license, as kept, and never over a file of its own", () => {
    expect(supplyFor("react-remove-scroll-bar", "2.3.8", "MIT")?.files).toEqual([{ name: "LICENSE", text: KEPT }]);
    expect(supplyFor("react-remove-scroll-bar", "2.3.9", "MIT")).toBeNull();          // a version its note is not about
    expect(supplyFor("react-remove-scroll-bar", "2.3.8", "ISC")).toBeNull();
    expect(supplyFor("e", "5.0.0", "MIT")).toBeNull();                                  // nothing kept for it: no holder made up
    expect(supplyFor("constructor", "1.0.0", "MIT")).toBeNull();                       // not the object's own keys
    const entry = SUPPLIED["react-remove-scroll-bar"];
    const { sha256, file } = entry;
    try {
      entry.sha256 = "0".repeat(64);                                                      // the kept file, not the one recorded
      expect(supplyFor("react-remove-scroll-bar", "2.3.8", "MIT")).toBeNull();
      entry.sha256 = sha256;
      entry.file = "third-party/react-remove-scroll-bar/GONE";
      expect(supplyFor("react-remove-scroll-bar", "2.3.8", "MIT")).toBeNull();
    } finally {
      entry.sha256 = sha256;
      entry.file = file;
    }
    const shipped = join(scratch, "shipped", "node_modules", "react-remove-scroll-bar");
    pkg(shipped, { name: "react-remove-scroll-bar", version: "2.3.8", license: "MIT" }, { LICENSE: "its own" });
    expect(packagesOf([join(shipped, "i.js")])[0].supplied).toBeNull();
  });

  it("keeps react-remove-scroll-bar's file as its repository holds it", () => {
    // git's id for a file's content, which the tree of commit 8ca9ba5 gives this one
    const bytes = readFileSync(join(UI, SUPPLIED["react-remove-scroll-bar"].file));
    const blob = createHash("sha1").update(Buffer.concat([Buffer.from(`blob ${bytes.length}\0`), bytes])).digest("hex");
    expect(blob).toBe("7c08c3990396ecefd90f99ff5d9a34f26f5b5616");
    expect(KEPT.split("\n").slice(0, 3)).toEqual(["MIT License", "", "Copyright (c) 2025 Anton Korzunov <thekashey@gmail.com>"]);
    const react = readFileSync(join(UI, "node_modules", "react", "LICENSE"), "utf8");
    const words = (t: string) => t.slice(t.indexOf("Permission is hereby granted")).replace(/\s+/g, " ").trim();
    expect(words(KEPT)).toBe(words(react));                                             // the MIT License, word for word
  });

  it("wraps a note between words, a word longer than a line kept whole", () => {
    expect(wrap("a b c", 3)).toEqual(["a b", "c"]);
    expect(wrap("  ")).toEqual([]);
    const url = `https://example.org/${"x".repeat(120)}`;
    expect(wrap(`${"word ".repeat(30)}from ${url}, done.`)).toEqual(["word ".repeat(22) + "word", "word word word word word word word from", `${url},`, "done."]);
  });

  it("takes an allowed license, or a choice holding one", () => {
    for (const ok of ["MIT", "ISC", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "0BSD", "(MIT OR GPL-3.0)", "Apache-2.0 OR MIT"]) {
      expect(allowed(ok), ok).toBe(true);
    }
    for (const no of ["GPL-3.0", "MIT AND GPL-3.0", "LGPL-2.1", "MPL-2.0", "UNKNOWN", "", "SEE LICENSE IN LICENSE", "mit"]) {
      expect(allowed(no), no).toBe(false);
    }
  });

  it("writes each package with its version, license and files, in order, and marks a supplied text", () => {
    const text = notice(packagesOf(ids));
    const headings = text.split("\n").filter((l) => /^\S+ \d+\.\d+\.\d+ \(/.test(l));
    expect(headings).toEqual(["@s/b 2.0.0 (ISC)", "a 1.0.0 (MIT)", "c 3.0.0 (MIT OR Apache-2.0)", "d 4.0.0 (GPL-3.0-only)", "e 5.0.0 (MIT)",
      "f 6.0.0 (UNKNOWN)", "g 1.0.0 (MIT)", "g 2.0.0 (MIT)", "react-remove-scroll-bar 2.3.8 (MIT)"]);
    expect(text).toContain("--- LICENSE\n\nMIT License\nCopyright (c) A\n");
    expect(text).toContain("(It ships no license file; its package.json names the license above.)");
    const rrsb = text.slice(text.indexOf("react-remove-scroll-bar 2.3.8 (MIT)"));
    expect(rrsb).toContain(`${wrap(SUPPLIED["react-remove-scroll-bar"].note).join("\n")}\n\n--- LICENSE\n\n${KEPT.replace(/\s+$/, "")}\n`);
    expect(rrsb.split("\n").slice(3, 8).every((line) => line.length <= 116)).toBe(true);
    expect(notice(packagesOf([...ids].reverse()))).toBe(text);              // the order of the modules does not matter
  });
});

describe("the page's build", () => {
  it("writes the file beside the page, and every package in it carries a license the page takes", async () => {
    const result = await build({
      root: UI, configFile: join(UI, "vite.config.ts"), logLevel: "silent",
      build: { write: false, outDir: join(scratch, "out"), emptyOutDir: false },
    }) as RolldownOutput | RolldownOutput[];
    const output = (Array.isArray(result) ? result : [result]).flatMap((r) => r.output);
    const asset = output.find((o) => o.type === "asset" && o.fileName === FILE);
    expect(asset, FILE).toBeTruthy();
    const text = asset!.type === "asset" ? String(asset!.source) : "";
    const packages = packagesOf(output.flatMap((o) => (o.type === "chunk" ? o.moduleIds : [])));
    expect(problems(packages)).toEqual([]);
    expect(packages.length).toBeGreaterThan(100);
    expect(text).toBe(notice(packages));
    const mlkem = packages.find((p) => p.name === "mlkem")!;
    expect([mlkem.version, mlkem.license]).toEqual(["2.7.0", "MIT"]);
    expect(text).toContain(`mlkem 2.7.0 (MIT)\n${"=".repeat(100)}\n\n--- LICENSE\n\n${readFileSync(join(UI, "node_modules", "mlkem", "LICENSE"), "utf8").replace(/\s+$/, "")}`);
    for (const name of ["react", "react-dom", "@xterm/xterm", "zustand"]) expect(packages.map((p) => p.name)).toContain(name);
    expect(packages.find((p) => p.name === "react-remove-scroll-bar")?.supplied?.files).toEqual([{ name: "LICENSE", text: KEPT }]);
    // and Proofpane Community's own, as open_edition/ holds them
    expect(OWN).toEqual(["LICENSE", "LICENSE-APACHE-2.0", "NOTICE"]);
    for (const name of OWN) {
      const own = output.find((o) => o.type === "asset" && o.fileName === name);
      expect(own && own.type === "asset" ? String(own.source) : null, name).toBe(readFileSync(join(UI, "..", name), "utf8"));
    }
  }, 120_000);
});
