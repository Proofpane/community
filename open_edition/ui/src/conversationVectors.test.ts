import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { HistoryTalk, LinkEvent } from "./api";
import { conversation, sessionSaid } from "./components/Channel";

// The server joins a conversation for the Claude app connector (proofpane_open/ade/connector.py) by the rule this page
// joins it by: the same vectors are read by the server's test (open_edition/tests/test_connector_tools.py), so a
// change to one side that the other does not make fails here or there.
interface Vector {
  name: string;
  kind: "events" | "machine";
  events?: LinkEvent[];
  talk?: HistoryTalk["messages"] | null;
  notes?: LinkEvent[];
  want: { who: string; text: string }[];
  steps?: string[];     // the lines of calls the page shows apart, where its live view's rounds hold them
}
const VECTORS: Vector[] = JSON.parse(
  readFileSync(join(__dirname, "..", "..", "tests", "fixtures", "conversation-vectors.json"), "utf8"));

/** The page's words for who said it, as the server names them: the owner's signed commands apart, and what the session
 *  reports the owner typed said to be its report. */
function whoOf(s: { who: string; head?: string }): string {
  if (s.who === "you") return /^You (signed|answered)/.test(s.head ?? "") ? "owner, signed" : "owner, as the session reports it";
  return s.who === "it" ? "session" : "note";
}

describe("a conversation joined as the connector joins it", () => {
  it("has vectors to read", () => expect(VECTORS.length).toBeGreaterThanOrEqual(8));
  for (const v of VECTORS) {
    it(v.name, () => {
      const said = v.kind === "machine"
        ? sessionSaid(v.talk ? { messages: v.talk, earlier: 0, updated_at: 0 } : null, v.notes ?? [], "VS Code")
        : conversation(v.events ?? [], "mac");
      // a live view's steps are the page's alone: the connector's tool reads a conversation's words, so what both
      // join leaves them out, and the page shows each one apart
      // a step note's lines; a live view's rounds name theirs (one note a round, its calls among its items)
      const steps = v.steps ?? (v.events ?? []).filter((e) => e.mirror === "step").map((e) => e.text);
      expect(said.filter((s) => s.who === "step").map((s) => s.text)).toEqual(steps);
      expect(said.filter((s) => s.who !== "step").map((s) => ({ who: whoOf(s), text: s.text }))).toEqual(v.want);
    });
  }
});
