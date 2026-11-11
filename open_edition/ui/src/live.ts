import type { ChatEvent } from "./api";

/** A piece of what claude is writing, as it writes it (the agent's Chat._stream): never logged. `after` is the last
 *  logged event it follows; a piece of text says its block and where in the block it starts, in UTF-16 units. */
export interface ChatDelta { after: number; block: number; offset?: number; text?: string; thinking?: boolean; tool?: string }
export interface LiveBlock { block: number; text: string; lost: boolean }
/** What a chat shows live until the log has it: the blocks of text so far, and whether claude is thinking or about
 *  to call a tool. A block the page joined in the middle of, or missed a piece of, is lost: it waits for the log. */
export interface Live { blocks: LiveBlock[]; thinking: boolean; tool: string | null }

export function addDelta(live: Live | undefined, d: ChatDelta, last: number): Live | undefined {
  if (d.after < last) return live;                // from before what the page holds: the log has it whole
  const cur = live || { blocks: [], thinking: false, tool: null };
  if (typeof d.text === "string") {
    const i = cur.blocks.findIndex((b) => b.block === d.block);
    let blocks: LiveBlock[];
    if (i < 0) {
      blocks = [...cur.blocks, d.offset === 0 ? { block: d.block, text: d.text, lost: false } : { block: d.block, text: "", lost: true }];
    } else {
      const b = cur.blocks[i];
      if (b.lost) return { ...cur, thinking: false, tool: null };
      const next = b.text.length === d.offset ? { ...b, text: b.text + d.text } : { ...b, text: "", lost: true };
      blocks = cur.blocks.map((x, j) => (j === i ? next : x));
    }
    return { blocks, thinking: false, tool: null };
  }
  if (d.thinking) return { ...cur, thinking: true, tool: null };
  if (typeof d.tool === "string") return { ...cur, thinking: false, tool: d.tool };
  return live;
}

/** What a logged event leaves: a block of text or a tool call logged, or the turn over, and what was live is the log's. */
export function settle(live: Live | undefined, ev: ChatEvent): Live | undefined {
  if (!live) return live;
  if (ev.kind === "text" || ev.kind === "tool" || ev.kind === "result" || ev.kind === "error") return undefined;
  if (ev.kind === "status" && ev.state !== "running" && ev.state !== "needs_you") return undefined;
  return live;
}

/** The blocks worth showing, and whether anything at all is live. */
export function shown(live: Live | undefined): { blocks: LiveBlock[]; any: boolean } {
  const blocks = (live?.blocks || []).filter((b) => !b.lost && b.text.trim());
  return { blocks, any: blocks.length > 0 || !!live?.thinking || !!live?.tool };
}
