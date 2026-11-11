import { readFileSync } from "fs";
import { resolve } from "path";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Logo } from "./TitleBar";

afterEach(cleanup);

// Proofpane's logo as the product ships it (the P in the Pane, v8): the favicon the pages name
const favicon = new DOMParser().parseFromString(readFileSync(resolve(__dirname, "../../../web/favicon.svg"), "utf8"), "image/svg+xml");
const css = readFileSync(resolve(__dirname, "../index.css"), "utf8");

/** Each shape's kind and geometry, its numbers as numbers, in order: what makes the glyph, not how it is written. */
function shapes(svg: Element | Document) {
  const nums = (v: string | null) => (v || "").match(/[A-Za-z]|-?\d*\.?\d+/g)?.map((t) => (/[A-Za-z]/.test(t) ? t.toUpperCase() : Number(t))) ?? [];
  return [...svg.querySelectorAll("rect, line, path")].map((el) => ({
    kind: el.tagName.toLowerCase(),
    geometry: ["x", "y", "width", "height", "rx", "x1", "y1", "x2", "y2"].map((a) => el.getAttribute(a)).filter((v) => v !== null).map(Number),
    d: nums(el.getAttribute("d")),
    stroke: el.getAttribute("stroke-width") ?? el.getAttribute("strokeWidth"),
    evenodd: (el.getAttribute("fill-rule") ?? el.getAttribute("fillRule")) === "evenodd",
  }));
}

describe("the logo", () => {
  it("is drawn in the app with the favicon's own shapes", () => {
    const { container } = render(<Logo size={24} />);
    expect(shapes(container.querySelector("svg")!)).toEqual(shapes(favicon));
    expect(shapes(favicon).map((s) => s.kind)).toEqual(["rect", "line", "line", "path"]);
  });

  it("is in the theme's accent, which on the dark theme is the logo's own cyan", () => {
    const { container } = render(<Logo />);
    const paints = [...container.querySelectorAll("[stroke], [fill]")].map((el) => el.getAttribute("stroke") || el.getAttribute("fill"));
    expect(new Set(paints.filter((p) => p !== "none"))).toEqual(new Set(["var(--color-accent)"]));
    const logoCyan = [...favicon.querySelectorAll("[stroke], [fill]")].map((el) => el.getAttribute("stroke") || el.getAttribute("fill")).filter((p) => p !== "none");
    // the dark theme's tokens are the @theme block's (the light theme's come after, under [data-theme="bright"])
    const darkAccent = css.match(/@theme\s*\{[^}]*--color-accent:\s*(#[0-9a-fA-F]{6})/)![1];
    expect(new Set(logoCyan)).toEqual(new Set([darkAccent]));
  });
});
