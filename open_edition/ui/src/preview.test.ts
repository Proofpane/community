import { describe, expect, it } from "vitest";
import { inWorktree, isImage, isMarkdown, isPdf } from "./preview";

describe("what a file is shown as", () => {
  it("knows an image and a Markdown file by name", () => {
    expect([isImage("docs/Shot.PNG"), isImage("logo.svg"), isImage("notes.txt"), isImage("png")]).toEqual([true, true, false, false]);
    expect([isPdf("docs/Report.PDF"), isPdf("a.pdf.txt"), isPdf("pdf"), isPdf("notes.md")]).toEqual([true, false, false, false]);
    expect([isMarkdown("README.md"), isMarkdown("docs/guide.markdown"), isMarkdown("md")]).toEqual([true, true, false]);
  });
});

describe("where a link in a Markdown file leads", () => {
  it("follows a relative path from the file's folder, and a rooted one from the worktree's top", () => {
    expect(inWorktree("docs/guide.md", "setup.md")).toBe("docs/setup.md");
    expect(inWorktree("docs/guide.md", "./img/a.png")).toBe("docs/img/a.png");
    expect(inWorktree("docs/guide.md", "../src/app.py#L3")).toBe("src/app.py");
    expect(inWorktree("docs/guide.md", "/LICENSE")).toBe("LICENSE");
    expect(inWorktree("README.md", "My%20Notes.md")).toBe("My Notes.md");
  });

  it("does not follow a link elsewhere, to a place in the same file, or above the worktree", () => {
    for (const href of ["https://example.com/a.md", "mailto:a@b.c", "#usage", "//cdn.example/x.png", "javascript:alert(1)", "../../x.md", ""]) {
      expect(inWorktree("docs/guide.md", href)).toBeNull();
    }
  });
});
