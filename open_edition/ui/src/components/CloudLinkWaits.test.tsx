// CLOUD-LINK-3 (queue 4.320): one paste is enough. The block for a session on claude.ai, pasted before the environment
// lets the ADE's host through, waits for it while its ticket lives, trying again every ten seconds, and then goes on by
// itself; where the ticket ends first it says to make a new one. The page writes the ticket's end into the block. The
// block's words are kept in tests/vectors/cloud_link_page.json, which tests/test_cloud_link.py runs in a shell.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { AGAIN_EVERY, BLOCK_WAITS, ticketEnded } from "../cloudLink";
import { pairLine, type TicketOut } from "./Channel";

const FILE = join(__dirname, "../../../tests/vectors/cloud_link_page.json");
const vectors = () => JSON.parse(readFileSync(FILE, "utf8"));

describe("the block's wait for the environment's setting", () => {
  it("says the words the vectors file keeps, which the client's cases run", () => {
    const v = vectors();
    expect(BLOCK_WAITS).toBe(v.block_waits);
    expect(ticketEnded("{host}")).toBe(v.ticket_ended);
  });

  it("waits until the ticket's end the server wrote, and gives no block for an answer without one", () => {
    const v = vectors();
    const answer = (expires: unknown) => ({ ...v.answer, url: "https://ade.example.test", expires_at: expires }) as TicketOut;
    const block = pairLine(answer(1893456000), { name: v.name, kind: "cloud" }) ?? "";
    expect(block.split("\n")).toContain("UNTIL = 1893456000");
    for (const bad of [1893456000.5, -1, 0, "1893456000", null, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(pairLine(answer(bad), { name: v.name, kind: "cloud" }), String(bad)).toBeNull();
    }
  });

  it("says the interval it waits, one number for its words and its program, and leaves the link 20 seconds", () => {
    const v = vectors();
    const block = pairLine({ ...v.answer, url: "https://ade.example.test" } as TicketOut, { name: v.name, kind: "cloud" }) ?? "";
    expect(BLOCK_WAITS).toContain(`every ${AGAIN_EVERY} seconds`);
    expect(block.split("\n")).toContain(`    time.sleep(${AGAIN_EVERY})`);
    // no try starts later than the ticket's end less the interval and 20 seconds for the fetch, setup and the pair
    expect(block.split("\n")).toContain(`    if time.time() + ${AGAIN_EVERY} + 20 > UNTIL:`);
  });
});
