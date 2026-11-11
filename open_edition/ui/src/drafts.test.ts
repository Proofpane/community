import { describe, expect, it } from "vitest";
import { draftKey, drafts, keepDrafts } from "./drafts";

describe("unsaved edits", () => {
  it("are forgotten with their session, and kept for those still there", () => {
    drafts.clear();
    drafts.set(draftKey("s1", "app.py"), { text: "a", base: "x" });
    drafts.set(draftKey("s2", "b/c.md"), { text: "b", base: "y" });
    keepDrafts(["s2", "s3"]);
    expect([...drafts.keys()]).toEqual([draftKey("s2", "b/c.md")]);
  });
});
