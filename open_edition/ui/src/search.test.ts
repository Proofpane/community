import { describe, expect, it } from "vitest";
import { byFile, matcher, segments, type Match } from "./search";

const m = (path: string, line: number, text = "x"): Match => ({ path, line, column: 1, text, cut: false });

describe("searching a worktree", () => {
  it("groups the matches by file, in git's order", () => {
    const got = byFile([m("b.py", 3), m("a.py", 1), m("b.py", 9)]);
    expect(got.map((g) => [g.path, g.matches.map((x) => x.line)])).toEqual([["b.py", [3, 9]], ["a.py", [1]]]);
  });

  it("marks a literal query as text, its special characters included", () => {
    const re = matcher("a.c(", false, false);
    expect(segments("abc( a.c( A.C(", re)).toEqual([
      { text: "abc( ", hit: false }, { text: "a.c(", hit: true }, { text: " ", hit: false }, { text: "A.C(", hit: true },
    ]);
  });

  it("marks case only when asked to match it", () => {
    expect(segments("Total total", matcher("total", true, false)).filter((s) => s.hit).map((s) => s.text)).toEqual(["total"]);
    expect(segments("Total total", matcher("total", false, false)).filter((s) => s.hit)).toHaveLength(2);
  });

  it("marks an expression's matches, and nothing when the browser cannot read it", () => {
    expect(segments("def total(items):", matcher("^def [a-z]+", false, true))).toEqual([
      { text: "def total", hit: true }, { text: "(items):", hit: false },
    ]);
    expect(matcher("a(b", false, true)).toBeNull();
    expect(segments("a(b", null)).toEqual([{ text: "a(b", hit: false }]);
  });

  it("steps past an empty match instead of looping on it", () => {
    expect(segments("abc", matcher("x*", false, true))).toEqual([{ text: "abc", hit: false }]);
    expect(segments("", matcher("x*", false, true))).toEqual([{ text: "", hit: false }]);
  });
});
