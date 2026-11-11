import { describe, expect, it } from "vitest";
import { e2eFlag, pairLine } from "./Channel";

describe("the page's line's --e2e (setup, as pair before it)", () => {
  const ticket = { ticket: "PP-ABCDEFGH", expires_at: 1790000000, url: "https://pp.example", keys: ["a".repeat(32)] };
  const mac = { name: "mac", kind: "local" as const };

  it("carries the account's newest statement as the ticket's answer gives it, so a new link refuses anything older", () => {
    expect(pairLine({ ...ticket, e2e: { seq: 4, key_id: "0f".repeat(16) } }, mac)).toBe(
      `python3 -I "$HOME/.proofpane-ade/ade_link.py" setup --url https://pp.example --ticket PP-ABCDEFGH --keys ${"a".repeat(32)} --e2e 4:${"0f".repeat(16)} --name 'mac' --kind local --mirror`);
    expect(pairLine({ ...ticket, e2e: { seq: 5, key_id: null } }, mac)).toContain(" --e2e 5:off ");
    expect(pairLine({ ...ticket, e2e: null }, mac)).toContain(" --e2e 0:none ");
  });

  it("says nothing of it when the answer does not, or gives something else, since the line goes into a shell", () => {
    expect(pairLine(ticket, mac)).not.toContain("--e2e");
    expect(e2eFlag({ seq: 2, key_id: "abc; rm -rf ~" })).toBeNull();
    expect(e2eFlag({ seq: 2, key_id: "AB".repeat(16) })).toBeNull();
    expect(e2eFlag({ seq: 0, key_id: null })).toBeNull();
    expect(e2eFlag({ seq: 1.5, key_id: null })).toBeNull();
    expect(e2eFlag({ seq: "3", key_id: null })).toBeNull();
    expect(e2eFlag(undefined)).toBeNull();
    expect(e2eFlag(null)).toBe("0:none");
  });
});
