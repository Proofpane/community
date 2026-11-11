// The third-party licenses of what the page's build holds (open_edition/NOTICE): every npm package whose modules a
// build's chunks contain, with its version and license as its package.json names them, and the license files it ships.
// The build writes them beside the page as THIRD_PARTY_LICENSES.txt, with Proofpane Community's own LICENSE, NOTICE and
// the Apache License 2.0's text (LICENSE-APACHE-2.0, for the third-party works under it), in a browser's build (web/app/), the desktop app's (desktop/build/ui/) and the phone app's (phone/dist/) alike; the
// app's Licenses dialog shows them (src/components/Licenses.tsx). ui/src/licenses.test.ts builds the page and holds
// every package in it to a license ALLOWED names, with a license file: its own, or one SUPPLIED here.
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import type { Plugin } from "vite";

export const FILE = "THIRD_PARTY_LICENSES.txt";
/** Proofpane Community's own license, the Apache License 2.0's text for the third-party works under it, and the notice
 *  that says which (open_edition/), which every build carries beside the page too. */
export const OWN = ["LICENSE", "LICENSE-APACHE-2.0", "NOTICE"];
/** The licenses a package the page bundles may carry (SPDX identifiers): permissive ones, whose conditions for code
 *  carried in a built page are met by the license files the build writes beside it (each package's notice and license
 *  text, and an Apache NOTICE file among them where a package ships one). */
export const ALLOWED = new Set(["MIT", "ISC", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "0BSD"]);
const LICENSE_FILE = /^(licen[cs]e|copying|notice)([._-][a-z0-9._-]*)?$/i;
const MARK = "/node_modules/";

export interface Supply {
  /** Where the files come from, said in the notice. */
  note: string;
  files: { name: string; text: string }[];
}

export interface Package {
  name: string;
  version: string;
  license: string;
  dir: string;
  files: { name: string; text: string }[];
  /** For a package that ships no license file: what is SUPPLIED for it, while it is still that version under that
   *  license and the kept file is the one recorded. */
  supplied: Supply | null;
}

/** License files for the packages that ship none of their own, each its project's own, kept in ui/third-party/ from
 *  its repository (where its note says) and checked here by its SHA-256. Never a holder made up from an author's
 *  name: one that has no file of its project's stays a problem. */
export const SUPPLIED: Record<string, { license: string; version: string; file: string; sha256: string; note: string }> = {
  "react-remove-scroll-bar": {
    license: "MIT",
    version: "2.3.8",
    file: "third-party/react-remove-scroll-bar/LICENSE",
    // git's id for it is 7c08c3990396ecefd90f99ff5d9a34f26f5b5616, in the tree of commit 8ca9ba5ea52de03308fe8ced94f7b159a44d28ff
    sha256: "a79aae0c0f21990d9d963bb3c5a79cdcea9a46f8523ba55c58d7fe776b6ebc84",
    note: "(It ships no license file; its package.json names MIT. Its repository, "
      + "https://github.com/theKashey/react-remove-scroll-bar, held none when this version was published (2024-12-15, "
      + "from commit b3b1287aad81def2e2ae707274b74531b61ddbaf, as npm records it, which the repository does not hold); "
      + "it added the one below on 2025-05-21 (commit 7301c160fda44cb8cf2b9fdfde61efad35736196), as its default branch "
      + "holds it at commit 8ca9ba5ea52de03308fe8ced94f7b159a44d28ff.)",
  },
};

/** The text of a file beside this one (ui/), as kept. */
function kept(file: string): string {
  return readFileSync(new URL(file, import.meta.url), "utf8");
}

/** What SUPPLIED gives a package of `name`, `version` and `license` that ships no license file, or null: nothing when
 *  it gives nothing for it, it is another version or under another license, or the kept file is not the one recorded. */
export function supplyFor(name: string, version: string, license: string): Supply | null {
  const supply = Object.prototype.hasOwnProperty.call(SUPPLIED, name) ? SUPPLIED[name] : null;
  if (!supply || supply.version !== version || supply.license !== license) return null;
  let text: string;
  try {
    text = kept(supply.file);
  } catch {
    return null;
  }
  if (createHash("sha256").update(text, "utf8").digest("hex") !== supply.sha256) return null;
  return { note: supply.note, files: [{ name: supply.file.slice(supply.file.lastIndexOf("/") + 1), text }] };
}

/** The directory of the npm package a module id lies in: the path up to the name after its last node_modules (a scope
 *  and a name for a scoped package), its query left out. A virtual module's id that names a package's file counts as
 *  that package (listing a license more than needed costs nothing); null for one of the page's own modules. */
export function packageDir(id: string): string | null {
  const path = id.replace(/^\0/, "").split("?")[0].replace(/\\/g, "/");
  const at = path.lastIndexOf(MARK);
  if (at < 0) return null;
  const rest = path.slice(at + MARK.length).split("/");
  const name = rest[0].startsWith("@") ? rest.slice(0, 2) : rest.slice(0, 1);
  if (name.length !== (rest[0].startsWith("@") ? 2 : 1) || name.some((part) => !part)) return null;
  return path.slice(0, at + MARK.length) + name.join("/");
}

/** A package.json's license as one SPDX expression: its `license`, or the type of an old `license` object, or the old
 *  `licenses` list's types joined with OR; "UNKNOWN" when it names none. */
function licenseOf(meta: Record<string, unknown>): string {
  const one = meta.license;
  if (typeof one === "string" && one.trim()) return one.trim();
  if (one && typeof one === "object" && typeof (one as { type?: unknown }).type === "string") return (one as { type: string }).type;
  const many = meta.licenses;
  if (Array.isArray(many) && many.length && many.every((l) => l && typeof l.type === "string")) {
    return many.map((l: { type: string }) => l.type).join(" OR ");
  }
  return "UNKNOWN";
}

/** The packages the module ids lie in, each once, sorted by name and then version. */
export function packagesOf(ids: Iterable<string>): Package[] {
  const dirs = new Set<string>();
  for (const id of ids) {
    const dir = packageDir(id);
    if (dir) dirs.add(dir);
  }
  const found = new Map<string, Package>();
  for (const dir of dirs) {
    const meta = JSON.parse(readFileSync(`${dir}/package.json`, "utf8")) as Record<string, unknown>;
    const name = String(meta.name ?? dir.slice(dir.lastIndexOf(MARK) + MARK.length));
    const version = String(meta.version ?? "");
    const files = readdirSync(dir).filter((f) => LICENSE_FILE.test(f) && statSync(`${dir}/${f}`).isFile()).sort()
      .map((f) => ({ name: f, text: readFileSync(`${dir}/${f}`, "utf8") }));
    const license = licenseOf(meta);
    const supplied = files.length ? null : supplyFor(name, version, license);
    found.set(`${name}@${version}`, { name, version, license, dir, files, supplied });
  }
  return [...found.values()].sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : a.version < b.version ? -1 : 1));
}

/** Whether an SPDX expression is a license ALLOWED names, or a choice (OR) one of whose terms is. */
export function allowed(license: string): boolean {
  const expression = license.replace(/^\((.*)\)$/, "$1");
  return expression.split(/\s+OR\s+/).some((term) => ALLOWED.has(term.trim()));
}

/** What keeps a package from the page: a license not ALLOWED, or no license file to keep its notice from. */
export function problems(packages: Package[]): string[] {
  return packages.flatMap((p) => [
    ...(allowed(p.license) ? [] : [`${p.name} ${p.version}: its license, ${p.license}, is not one the page takes`]),
    ...(p.files.length || p.supplied ? [] : [`${p.name} ${p.version}: it ships no license file, and none is supplied for it`]),
  ]);
}

/** The file, as the build writes it. */
export function notice(packages: Package[]): string {
  const out = [
    "Third-party software in this build of Proofpane Community's page",
    "",
    "Proofpane Community is open source under the Apache License 2.0 (its LICENSE and NOTICE files). This build",
    "also holds the code of the npm packages below, each under its own license: each is named with its version and",
    "the license its package.json gives, then the license files it ships, as it ships them; for one that ships none,",
    "its project's own license file, kept in ui/third-party/, as its note says. Written by the build (ui/licenses.ts)",
    "from the packages whose modules the build's chunks contain.",
    "",
  ];
  for (const p of packages) {
    out.push("=".repeat(100), `${p.name} ${p.version} (${p.license})`, "=".repeat(100), "");
    if (p.supplied) {
      out.push(...wrap(p.supplied.note), "");
    } else if (!p.files.length) {
      out.push("(It ships no license file; its package.json names the license above.)", "");
    }
    for (const f of [...p.files, ...(p.supplied?.files ?? [])]) out.push(`--- ${f.name}`, "", f.text.replace(/\s+$/, ""), "");
  }
  return out.join("\n");
}

/** A paragraph in lines of at most 116 characters, broken between words; a word longer than a line (an address) kept
 *  whole on its own. */
export function wrap(text: string, width = 116): string[] {
  const lines: string[] = [];
  let line = "";
  for (const word of text.split(/\s+/).filter(Boolean)) {
    if (line && line.length + 1 + word.length > width) {
      lines.push(line);
      line = word;
    } else {
      line = line ? `${line} ${word}` : word;
    }
  }
  return line ? [...lines, line] : lines;
}

/** The plugin that writes the file beside the page, from the modules in the build's chunks, and Proofpane Community's
 *  own LICENSE, LICENSE-APACHE-2.0 and NOTICE (OWN) with it. */
export function thirdPartyLicenses(): Plugin {
  return {
    name: "proofpane-third-party-licenses",
    apply: "build",
    generateBundle(_options, bundle) {
      const ids = Object.values(bundle).flatMap((out) => (out.type === "chunk" ? out.moduleIds : []));
      const packages = packagesOf(ids);
      // a package the page may not carry stops the build, every one named at once and nothing written, as the
      // desktop's and the phone's builds stop on theirs (desktop/licenses.py): so no build, the hosted image's (which
      // runs no test) among them, carries a notice saying a package ships no license
      const found = problems(packages);
      if (found.length) this.error(`third-party licenses the page cannot carry:\n  ${found.join("\n  ")}`);
      this.emitFile({ type: "asset", fileName: FILE, source: notice(packages) });
      for (const name of OWN) this.emitFile({ type: "asset", fileName: name, source: kept(`../${name}`) });
    },
  };
}
