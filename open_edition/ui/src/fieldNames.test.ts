import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// Every field the person types or chooses in has a name a screen reader says: its own aria-label, an id a <label> names,
// or a <label> around it. A placeholder is not a name: it goes as the person types.
function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    if (statSync(p).isDirectory()) return sources(p);
    return p.endsWith(".tsx") && !p.endsWith(".test.tsx") ? [p] : [];
  });
}

describe("the app's fields", () => {
  it("each have a name of their own", () => {
    const unnamed: string[] = [];
    for (const file of sources(join(__dirname))) {
      const text = readFileSync(file, "utf8");
      for (const m of text.matchAll(/<(input|textarea|select)\b([\s\S]*?)\/?>/g)) {
        const attrs = m[2];
        if (/aria-label(ledby)?=|\bid=|type="(hidden|file|checkbox|radio)"/.test(attrs)) continue;
        const before = text.slice(Math.max(0, (m.index ?? 0) - 400), m.index);
        if (/<label\b[^>]*>(?![\s\S]*<\/label>)/.test(before)) continue;        // inside a <label> just opened
        unnamed.push(`${file.slice(file.indexOf("src/"))}:${text.slice(0, m.index).split("\n").length}`);
      }
    }
    expect(unnamed).toEqual([]);
  });

  it("and so does every handle that resizes a part of the page", () => {
    const unnamed: string[] = [];
    for (const file of sources(join(__dirname))) {
      const text = readFileSync(file, "utf8");
      for (const m of text.matchAll(/<Separator\b([^>]*)\/?>/g)) {
        if (!/aria-label=/.test(m[1])) unnamed.push(`${file.slice(file.indexOf("src/"))}:${text.slice(0, m.index).split("\n").length}`);
      }
    }
    expect(unnamed).toEqual([]);
  });
});
