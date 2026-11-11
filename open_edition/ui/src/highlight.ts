// Syntax colour for the diff and the file viewer: refractor 5 (Prism's grammars, without the DOM), with the languages
// a repository most often holds. It gives back a tree of spans; here that becomes text and class names, which React
// renders as elements: nothing it gives back is ever HTML.
import { refractor } from "refractor/core";
import bash from "refractor/bash";
import c from "refractor/c";
import cpp from "refractor/cpp";
import csharp from "refractor/csharp";
import css from "refractor/css";
import dart from "refractor/dart";
import diff from "refractor/diff";
import docker from "refractor/docker";
import elixir from "refractor/elixir";
import go from "refractor/go";
import graphql from "refractor/graphql";
import hcl from "refractor/hcl";
import ini from "refractor/ini";
import java from "refractor/java";
import javascript from "refractor/javascript";
import json from "refractor/json";
import jsx from "refractor/jsx";
import kotlin from "refractor/kotlin";
import lua from "refractor/lua";
import makefile from "refractor/makefile";
import markdown from "refractor/markdown";
import markup from "refractor/markup";
import php from "refractor/php";
import protobuf from "refractor/protobuf";
import python from "refractor/python";
import ruby from "refractor/ruby";
import rust from "refractor/rust";
import scala from "refractor/scala";
import scss from "refractor/scss";
import sql from "refractor/sql";
import swift from "refractor/swift";
import toml from "refractor/toml";
import tsx from "refractor/tsx";
import typescript from "refractor/typescript";
import yaml from "refractor/yaml";

for (const lang of [bash, c, cpp, csharp, css, dart, diff, docker, elixir, go, graphql, hcl, ini, java, javascript, json, jsx,
  kotlin, lua, makefile, markdown, markup, php, protobuf, python, ruby, rust, scala, scss, sql, swift, toml, tsx, typescript, yaml]) {
  refractor.register(lang);
}

const BY_EXT: Record<string, string> = {
  js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "jsx", ts: "typescript", mts: "typescript", cts: "typescript",
  tsx: "tsx", json: "json", jsonc: "json", json5: "json", py: "python", pyi: "python", go: "go", rs: "rust", java: "java",
  kt: "kotlin", kts: "kotlin", swift: "swift", c: "c", h: "c", cc: "cpp", cpp: "cpp", cxx: "cpp", hpp: "cpp", hh: "cpp",
  cs: "csharp", rb: "ruby", php: "php", sh: "bash", bash: "bash", zsh: "bash", yml: "yaml", yaml: "yaml", toml: "toml",
  md: "markdown", markdown: "markdown", sql: "sql", css: "css", scss: "scss", html: "markup", htm: "markup", xml: "markup",
  svg: "markup", vue: "markup", graphql: "graphql", gql: "graphql", lua: "lua", ini: "ini", cfg: "ini", diff: "diff",
  patch: "diff", tf: "hcl", hcl: "hcl", proto: "protobuf", scala: "scala", dart: "dart", ex: "elixir", exs: "elixir",
};
const BY_NAME: Record<string, string> = { dockerfile: "docker", makefile: "makefile", gnumakefile: "makefile", gemfile: "ruby", rakefile: "ruby" };

/** The language a path's name says it holds, if this page can colour it. */
export function languageOf(path: string): string | null {
  const base = (path.split("/").pop() || "").toLowerCase();
  const lang = BY_NAME[base] || (base.startsWith("dockerfile.") ? "docker" : base.includes(".") ? BY_EXT[base.slice(base.lastIndexOf(".") + 1)] : undefined);
  return lang && refractor.registered(lang) ? lang : null;
}

// what a Markdown code fence names its language, where that is not the grammar's own name
const FENCE: Record<string, string> = {
  js: "javascript", mjs: "javascript", cjs: "javascript", ts: "typescript", py: "python", sh: "bash", shell: "bash",
  zsh: "bash", console: "bash", yml: "yaml", md: "markdown", html: "markup", xml: "markup", svg: "markup",
  dockerfile: "docker", rs: "rust", rb: "ruby", kt: "kotlin", cs: "csharp", "c++": "cpp", hcl: "hcl", tf: "hcl",
  proto: "protobuf", gql: "graphql", jsonc: "json", patch: "diff",
};

/** The grammar a code fence's language names, if this page can colour it. */
export function fenceLanguage(name: string | undefined): string | null {
  if (!name) return null;
  const lang = FENCE[name.toLowerCase()] || name.toLowerCase();
  return refractor.registered(lang) ? lang : null;
}

/** For react-diff-view's tokenize, which was written for refractor 3: its highlight gave the tree's children. */
export const forDiff = { highlight: (text: string, language: string) => refractor.highlight(text, language).children };

export interface Segment { text: string; cls: string }
interface HastNode { type: string; value?: string; properties?: { className?: unknown }; children?: HastNode[] }

/** A text coloured, a line at a time: each line is runs of text with the class names of every token around them,
 *  so a token that runs over lines (a comment, a string) is coloured on each. */
export function highlightLines(text: string, language: string): Segment[][] {
  const lines: Segment[][] = [[]];
  const walk = (n: HastNode, cls: string) => {
    if (n.type === "text") {
      (n.value || "").split("\n").forEach((part, i) => {
        if (i > 0) lines.push([]);
        if (part) lines[lines.length - 1].push({ text: part, cls });
      });
      return;
    }
    let here = cls;
    if (n.type === "element") {
      const c = n.properties?.className;
      const names = Array.isArray(c) ? c.filter((x): x is string => typeof x === "string") : typeof c === "string" ? [c] : [];
      here = [cls, ...names].filter(Boolean).join(" ");
    }
    for (const k of n.children || []) walk(k, here);
  };
  walk(refractor.highlight(text, language) as unknown as HastNode, "");
  return lines;
}
