// The theme a person picks reaches the page as the attribute the stylesheet keys its light palette on, and is kept.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { useTheme } from "./theme";

afterEach(() => { useTheme.getState().set("dark"); localStorage.clear(); });

describe("the theme", () => {
  it("puts the light palette's attribute on the page, the one Proofpane's own frontend uses", () => {
    useTheme.getState().set("light");
    expect(document.documentElement.dataset.theme).toBe("bright");
    expect(useTheme.getState().theme).toBe("light");
    expect(localStorage.getItem("pp-theme")).toBe("light");
    useTheme.getState().set("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("is the attribute the stylesheet's light palette is written for", () => {
    const css = readFileSync(join(__dirname, "index.css"), "utf8");
    expect(css).toContain(':root[data-theme="bright"] {');
    expect(css).not.toContain('data-theme="light"');
  });
});
