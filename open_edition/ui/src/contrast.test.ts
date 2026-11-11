// Every colour the app writes text in stays readable on every surface it writes on, in both themes: the WCAG 2
// contrast ratio, 4.5:1 for text, the faint grey's included (it carries times, hints and headings).
// Read from the stylesheet itself, so a token changed there is checked here.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "index.css"), "utf8");

function block(start: RegExp): string {
  const at = css.search(start);
  if (at < 0) throw new Error(`no ${start} in index.css`);
  const open = css.indexOf("{", at);
  return css.slice(open + 1, css.indexOf("}", open));
}

function tokens(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of text.matchAll(/--color-([a-z-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) out[m[1]] = m[2].toLowerCase();
  return out;
}

const dark = tokens(block(/@theme\s*\{/));
const bright = { ...dark, ...tokens(block(/:root\[data-theme="bright"\]\s*\{/)) };

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// every surface text sits on: the page, a panel, a raised card or field, a hovered row, and a selected row or a
// hovered button (active)
const SURFACES = ["bg", "panel", "raised", "hover", "active"];
const TEXT = ["fg", "fg-dim", "muted", "accent", "run", "wait", "done", "fail"];

describe.each([["dark", dark], ["bright", bright]] as const)("the %s theme", (_name, t) => {
  it("names every colour it is checked for", () => {
    for (const k of [...SURFACES, ...TEXT, "faint", "primary", "primary-hover", "primary-ink"]) expect(t[k], k).toMatch(/^#[0-9a-f]{6}$/);
  });

  it.each(TEXT)("writes %s readably on every surface", (k) => {
    for (const s of SURFACES) expect(contrast(t[k], t[s]), `${k} on ${s}`).toBeGreaterThanOrEqual(4.5);
  });

  it("writes faint readably too, where it is used (times, hints, headings; never on a selected row)", () => {
    for (const s of SURFACES.filter((x) => x !== "active")) expect(contrast(t.faint, t[s]), `faint on ${s}`).toBeGreaterThanOrEqual(4.5);
  });

  it("writes a count on a waiting badge in the page's own colour, readably", () => {
    // the title bar's counts (approvals, linked sessions): the amber fill, and the page's colour on it
    expect(contrast(t.bg, t.wait)).toBeGreaterThanOrEqual(4.5);
  });

  it("labels the primary action readably, at rest and under the pointer", () => {
    expect(contrast(t["primary-ink"], t.primary)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(t["primary-ink"], t["primary-hover"])).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps a selected row apart from the surface under it", () => {
    for (const s of ["bg", "panel"]) expect(t.active, `active on ${s}`).not.toBe(t[s]);
    expect(t.hover).not.toBe(t.panel);
  });
});

describe("the ratio itself", () => {
  it("is WCAG's: black on white is 21, a colour on itself is 1, and white on cyan-600 is under 4.5", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#0e7490", "#0e7490")).toBe(1);
    expect(contrast("#ffffff", "#0891b2")).toBeLessThan(4.5);
    expect(contrast("#ffffff", "#0e7490")).toBeGreaterThan(5.3);
  });
});
