import { describe, expect, it } from "vitest";
import { forDiff, highlightLines, languageOf } from "./highlight";

describe("syntax colour", () => {
  it("knows a language by a path's name, and says nothing for one it cannot colour", () => {
    expect(languageOf("src/App.tsx")).toBe("tsx");
    expect(languageOf("lib/a.PY")).toBe("python");
    expect(languageOf("Dockerfile")).toBe("docker");
    expect(languageOf("deploy/Dockerfile.prod")).toBe("docker");
    expect(languageOf("infra/main.tf")).toBe("hcl");
    expect(languageOf("README")).toBeNull();
    expect(languageOf("a.unknownext")).toBeNull();
    expect(languageOf("")).toBeNull();
  });

  it("colours a line at a time, a token over lines on each of them", () => {
    const lines = highlightLines("/* one\ntwo */\nconst x = \"s\";\n", "javascript");
    expect(lines.length).toBe(4);
    expect(lines[0]).toEqual([{ text: "/* one", cls: "token comment" }]);
    expect(lines[1]).toEqual([{ text: "two */", cls: "token comment" }]);
    expect(lines[2].find((s) => s.text === "const")?.cls).toBe("token keyword");
    expect(lines[2].find((s) => s.text === "\"s\"")?.cls).toBe("token string");
    expect(lines[2].map((s) => s.text).join("")).toBe("const x = \"s\";");
    expect(lines[3]).toEqual([]);
  });

  it("gives react-diff-view the tree's children, as refractor 3 did", () => {
    const nodes = forDiff.highlight("let a = 1", "javascript");
    expect(Array.isArray(nodes)).toBe(true);
    expect(nodes.some((n) => n.type === "element")).toBe(true);
  });

  it("never gives back markup: a tag in the code stays text", () => {
    const lines = highlightLines("<img src=x onerror=alert(1)>", "javascript");
    expect(lines.flat().map((s) => s.text).join("")).toBe("<img src=x onerror=alert(1)>");
  });
});
