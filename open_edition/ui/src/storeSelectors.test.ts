import { readdirSync, readFileSync, statSync } from "fs";
import { join } from "path";
import { describe, expect, it } from "vitest";

// A store selector must give the same value back until the store changes: one that makes a new list or object at
// each read (a filter, a map, a literal, a fallback `|| []`) makes React render again without end (its error 185),
// which only a real render of that component shows. So no selector in the app builds one: it selects what the store
// holds, and the component derives the rest (useMemo).
const BUILDS = [/\.(filter|map|slice|concat|flatMap|sort|reverse)\(/, /\bObject\.(keys|values|entries|assign|fromEntries)\(/,
                /\bArray\.from\(/, /\bnew [A-Z]/, /=>\s*\[/, /=>\s*\(\s*\{/, /(\|\||\?\?)\s*(\[|\{)/, /\.\.\./];

/** Every `useStore(...)` call's selector in a source text: the text between its parentheses. */
export function selectors(text: string): string[] {
  const out: string[] = [];
  for (let at = text.indexOf("useStore("); at !== -1; at = text.indexOf("useStore(", at + 1)) {
    let depth = 0;
    let end = at + "useStore".length;
    for (; end < text.length; end++) {
      if (text[end] === "(") depth++;
      else if (text[end] === ")" && --depth === 0) break;
    }
    const inside = text.slice(at + "useStore(".length, end);
    if (inside.includes("=>")) out.push(inside);
  }
  return out;
}

export const builds = (selector: string) => BUILDS.some((b) => b.test(selector.slice(selector.indexOf("=>"))));

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
  });
}

describe("the store's selectors", () => {
  it("are told apart: what builds a value and what selects one", () => {
    expect(builds("(s) => s.repos.filter((r) => !r.missing)")).toBe(true);
    expect(builds("(s) => s.records.map((r) => r.id)")).toBe(true);
    expect(builds("(s) => [s.a, s.b]")).toBe(true);
    expect(builds("(s) => ({ a: s.a })")).toBe(true);
    expect(builds("(s) => s.list || []")).toBe(true);
    expect(builds("(s) => s.list ?? {}")).toBe(true);
    expect(builds("(s) => s.repos")).toBe(false);
    expect(builds("(s) => s.records.find((r) => r.id === id)")).toBe(false);
    expect(builds("(s) => s.records.length")).toBe(false);
    expect(selectors("const a = useStore((s) => s.x); const b = useStore((s) => s.y.map((z) => z.id));"))
      .toEqual(["(s) => s.x", "(s) => s.y.map((z) => z.id)"]);
  });

  it("never build a new list or object in the app's code", () => {
    const found: string[] = [];
    for (const path of sources(join(__dirname))) {
      for (const s of selectors(readFileSync(path, "utf8"))) if (builds(s)) found.push(`${path.replace(__dirname, "src")}: ${s}`);
    }
    expect(found).toEqual([]);
  });
});
