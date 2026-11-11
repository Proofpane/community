import { describe, expect, it } from "vitest";
import { failedWords, startEach } from "./fanout";

describe("a fan-out's sessions, started each in turn", () => {
  it("keeps going past one that fails, and says which and why", async () => {
    const made: string[] = [];
    const r = await startEach(["echo", "aider", "claude"], (x) => x, async (x) => {
      if (x === "aider") throw new Error("aider is not installed in this workspace.");
      made.push(x);
      return x;
    });
    expect(made).toEqual(["echo", "claude"]);
    expect(r.created).toEqual(["echo", "claude"]);
    expect(failedWords(r)).toBe("Started 2 of 3. aider: aider is not installed in this workspace.");
  });

  it("says the reason alone when the only one fails, and nothing when all start", async () => {
    const none = await startEach(["aider"], (x) => x, async () => { throw new Error("aider is not installed."); });
    expect(failedWords(none)).toBe("aider is not installed.");
    const all = await startEach(["a", "b"], (x) => x, async (x) => x);
    expect(failedWords(all)).toBe("");
  });
});
