import { describe, expect, it } from "vitest";
import { diffSections, headerPaths, sectionPaths, unquote } from "./diffPaths";
import { diffFiles } from "./components/DiffView";

const R = String.raw;
// what git writes for these names (core.quotepath on: each byte past ASCII as \ooo)
const DIFF = [
  "diff --git a/a b.txt b/a b.txt", "new file mode 100644", "index 0000000..587be6b", "--- /dev/null", "+++ b/a b.txt\t", "@@ -0,0 +1 @@", "+x",
  R`diff --git "a/q\"x.txt" "b/q\"x.txt"`, "new file mode 100644", "--- /dev/null", R`+++ "b/q\"x.txt"`, "@@ -0,0 +1 @@", "+x",
  R`diff --git "a/r\303\251sum\303\251.md" "b/r\303\251sum\303\251.md"`, "deleted file mode 100644", R`--- "a/r\303\251sum\303\251.md"`, "+++ /dev/null", "@@ -1 +0,0 @@", "-x",
  "diff --git a/x b/y b/x b/y", "old mode 100644", "new mode 100755",
  "diff --git a/a b.txt b/moved b.txt", "similarity index 100%", "rename from a b.txt", "rename to moved b.txt",
  R`diff --git "a/q\"x.txt" b/plain.txt`, "similarity index 90%", R`rename from "q\"x.txt"`, "rename to plain.txt",
  R`--- "a/q\"x.txt"`, "+++ b/plain.txt", "@@ -1 +1 @@", "-x", "+y",
  "diff --git a/src/app.py b/src/app.py", "index 1..2 100644", "--- a/src/app.py", "+++ b/src/app.py", "@@ -1 +1 @@", "-a", "+b",
].join("\n") + "\n";

describe("a file's names in a diff, as git wrote them", () => {
  it("are read back from git's quotes and escapes", () => {
    expect(unquote(R`"q\"x \\ \t.txt"`)).toBe('q"x \\ \t.txt');
    expect(unquote(R`"r\303\251sum\303\251.md"`)).toBe("r\xe9sum\xe9.md");
    expect(unquote("plain name.txt")).toBe("plain name.txt");
    expect(unquote(R`"not closed`)).toBe(R`"not closed`);
    expect(unquote(R`"\q"`)).toBe(R`"\q"`);                 // an escape git never writes: left as it is
  });

  it("are the same name on both sides of a file not renamed, whatever is in it", () => {
    expect(headerPaths("diff --git a/x b/y b/x b/y")).toEqual(["x b/y", "x b/y"]);
    expect(headerPaths("diff --git a/a b.txt b/a b.txt")).toEqual(["a b.txt", "a b.txt"]);
    expect(headerPaths(R`diff --git "a/q\"x" "b/q\"x"`)).toEqual(['q"x', 'q"x']);
    expect(headerPaths(R`diff --git a/plain "b/q\"x"`)).toEqual(["plain", 'q"x']);
    expect(headerPaths("index 1..2")).toBeNull();
  });

  it("are each file's names, from its rename lines, its --- and +++ lines, or its first line", () => {
    const sections = DIFF.split(/^(?=diff --git )/m);
    expect(sections.map(sectionPaths)).toEqual([
      ["/dev/null", "a b.txt"], ["/dev/null", 'q"x.txt'], ["r\xe9sum\xe9.md", "/dev/null"], ["x b/y", "x b/y"],
      ["a b.txt", "moved b.txt"], ['q"x.txt', "plain.txt"], ["src/app.py", "src/app.py"],
    ]);
  });

  it("name the files the page shows", () => {
    const files = diffFiles(DIFF);
    expect(files.map((f) => [f.type, f.oldPath, f.newPath])).toEqual([
      ["add", "/dev/null", "a b.txt"], ["add", "/dev/null", 'q"x.txt'], ["delete", "r\xe9sum\xe9.md", "/dev/null"],
      ["modify", "x b/y", "x b/y"], ["rename", "a b.txt", "moved b.txt"], ["rename", 'q"x.txt', "plain.txt"],
      ["modify", "src/app.py", "src/app.py"],
    ]);
    expect(files[5].hunks.length).toBe(1);
  });
});

describe("a diff's names where only git's own line breaks count", () => {
  const ls = String.fromCodePoint(0x2028);
  const bom = String.fromCodePoint(0xfeff);
  it("keeps a byte order mark that starts a quoted name", () => {
    expect(unquote(R`"\357\273\277x"`)).toBe(bom + "x");
  });
  it("reads a name that holds U+2028 or a carriage return", () => {
    expect(headerPaths(`diff --git a/a${ls}b b/a${ls}c`)).toEqual([`a${ls}b`, `a${ls}c`]);
    expect(sectionPaths(`diff --git a/x${ls}y b/x${ls}y\nnew file mode 100644\n--- /dev/null\n+++ b/x${ls}y\n@@ -0,0 +1 @@\n+z\n`))
      .toEqual(["/dev/null", `x${ls}y`]);
  });
  it("starts a file only at a line git ended with a newline", () => {
    const diff = "diff --git a/one b/one\n--- a/one\n+++ b/one\n@@ -1 +1 @@\n-a\n+a\rdiff --git a/b b/b\n+" + ls + "diff --git a/c b/c\n" +
      "diff --git a/two b/two\n--- a/two\n+++ b/two\n@@ -1 +1 @@\n-x\n+y\n";
    expect(diffSections(diff).map((s) => s.split("\n")[0])).toEqual(["diff --git a/one b/one", "diff --git a/two b/two"]);
    expect(diffFiles(diff).map((f) => f.newPath)).toEqual(["one", "two"]);
  });
});
