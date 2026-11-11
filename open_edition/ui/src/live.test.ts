import { describe, expect, it } from "vitest";
import { addDelta, settle, shown, type ChatDelta, type Live } from "./live";
import type { ChatEvent } from "./api";

const grin = String.fromCodePoint(0x1f600);   // two UTF-16 units, as the agent counts them
const feed = (pieces: ChatDelta[], last = 3, start?: Live) => pieces.reduce<Live | undefined>((l, d) => addDelta(l, d, last), start);
const ev = (kind: string, more: Record<string, unknown> = {}): ChatEvent => ({ seq: 9, kind, ...more });

describe("what claude is writing, as the page shows it", () => {
  it("pieces of a block join up, counted in UTF-16 units", () => {
    const l = feed([{ after: 3, block: 2, offset: 0, text: "a" + grin }, { after: 3, block: 2, offset: 3, text: "b" }]);
    expect(l?.blocks).toEqual([{ block: 2, text: "a" + grin + "b", lost: false }]);
    expect(shown(l)).toEqual({ blocks: l!.blocks, any: true });
  });

  it("a block joined in the middle, or out of step, waits for the log, and the rest go on", () => {
    const l = feed([
      { after: 3, block: 1, offset: 5, text: "tail" },                     // joined in the middle
      { after: 3, block: 2, offset: 0, text: "ab" }, { after: 3, block: 2, offset: 9, text: "x" },   // a piece missed
      { after: 3, block: 2, offset: 3, text: "c" },                        // and nothing more is taken for it
      { after: 3, block: 3, offset: 0, text: "next" },
    ]);
    expect(l?.blocks.map((b) => [b.block, b.lost, b.text])).toEqual([[1, true, ""], [2, true, ""], [3, false, "next"]]);
    expect(shown(l).blocks.map((b) => b.text)).toEqual(["next"]);
  });

  it("thinking and a tool call show until text comes; a piece from before what the page holds is ignored", () => {
    let l = feed([{ after: 3, block: 1, thinking: true }]);
    expect(l).toEqual({ blocks: [], thinking: true, tool: null });
    expect(shown(l).any).toBe(true);
    l = feed([{ after: 3, block: 2, tool: "Bash" }], 3, l);
    expect([l?.thinking, l?.tool]).toEqual([false, "Bash"]);
    l = feed([{ after: 3, block: 3, offset: 0, text: "hi" }], 3, l);
    expect([l?.thinking, l?.tool]).toEqual([false, null]);
    expect(feed([{ after: 2, block: 4, offset: 0, text: "old" }], 3, l)).toBe(l);
    expect(feed([{ after: 3, block: 4 }], 3, l)).toBe(l);           // nothing it knows
  });

  it("a logged block of text, a tool call or the turn's end leaves nothing live; other events leave it", () => {
    const l = feed([{ after: 3, block: 1, offset: 0, text: "hi" }]);
    for (const e of [ev("text"), ev("tool"), ev("result"), ev("error"), ev("status", { state: "idle" }), ev("status", { state: "stopped" })]) {
      expect(settle(l, e)).toBeUndefined();
    }
    for (const e of [ev("status", { state: "running" }), ev("status", { state: "needs_you" }), ev("mcp_change"), ev("user")]) {
      expect(settle(l, e)).toBe(l);
    }
    expect(settle(undefined, ev("text"))).toBeUndefined();
    expect(shown(undefined)).toEqual({ blocks: [], any: false });
    expect(shown(feed([{ after: 3, block: 1, offset: 0, text: "  \n" }])).any).toBe(false);   // only whitespace yet
  });
});

describe("the caret, after the last thing written", () => {
  const el = (tagName: string, children: object[] = []) => ({ type: "element", tagName, properties: {}, children });
  const text = (value: string) => ({ type: "text", value });
  const carets = (n: { tagName?: string; properties?: { className?: string[] }; children?: object[] }): string[] =>
    (n.children || []).flatMap((c) => {
      const k = c as typeof n;
      return k.properties?.className?.includes("caret") ? [n.tagName || "root"] : carets(k);
    });
  it("goes into a list's last item, a code block, or after a paragraph's text", async () => {
    const { caretAtTheEnd } = await import("./components/ChatPane");
    const list = { type: "root", children: [el("p", [text("a")]), text("\n"), el("ol", [el("li", [text("one")]), text("\n"), el("li")]), text("\n")] };
    caretAtTheEnd()(list as never);
    expect(carets(list)).toEqual(["li"]);
    const code = { type: "root", children: [el("pre", [el("code", [text("x = 1")])])] };
    caretAtTheEnd()(code as never);
    expect(carets(code)).toEqual(["code"]);
    const para = { type: "root", children: [el("p", [text("a "), el("strong", [text("b")]), text(" and more")])] };
    caretAtTheEnd()(para as never);
    expect(carets(para)).toEqual(["p"]);
    const empty = { type: "root", children: [] };
    caretAtTheEnd()(empty as never);
    expect(carets(empty)).toEqual(["root"]);
  });
});
