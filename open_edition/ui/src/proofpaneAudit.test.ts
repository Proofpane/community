import { beforeEach, describe, expect, it } from "vitest";
import type { ProofpaneStatus, SessionRecord } from "./api";
import { auditAddress, auditWords, sessionAudit, sessionAuditWords, siteTaken, takeSite } from "./proofpaneAudit";

// AUDIT-BUTTON (the work queue's section 4.311): where the workspace's link to the audit in Proofpane One opens, from
// its pairing's status. One's own address for people (`site_url`) is the one it gave at pairing; the kept cloud_url is
// its API's.

const OWN = ["https://ws.example"];
// a device's id as Proofpane One makes it, a UUID (backend/app/api/routes/devices.py)
const DEV = "0f8f6c2e-9d3b-4c1a-8e2f-5b6a7c8d9e01";
const OTHER = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d";
const AT = `https://app.example.test/audit?device=${DEV}`;
const paired: ProofpaneStatus = {
  paired: true, plugin: true, name: "proofpane-core", skills: [], cloud_url: "https://api.example.test", device_id: DEV,
  device_name: "box", paired_at: null, holds: [], site_url: "https://app.example.test",
  mcp: { given: false, why: "machine" }, accounts: [],
};

// the site is one this browser took for the device, as a pairing made from it takes it; the cases of a site not taken
// clear it first
beforeEach(() => { localStorage.clear(); takeSite(paired); });

describe("the audit in Proofpane One, from the workspace's pairing", () => {
  it("opens at the address One gave at pairing, for this workspace's device", () => {
    expect(auditAddress(paired, OWN)).toBe(AT);
  });

  it("is none unpaired, with no address from One, with no device, or at an address that is not another site's http or https", () => {
    const none: (ProofpaneStatus | null | undefined)[] = [
      null, undefined, { ...paired, paired: false }, { ...paired, site_url: undefined }, { ...paired, site_url: null },
      { ...paired, site_url: "" }, { ...paired, device_id: undefined }, { ...paired, site_url: "javascript:alert(1)" },
      { ...paired, site_url: "https://ws.example" },
    ];
    for (const st of none) {
      takeSite(st);                    // taken where it can be, so that what refuses it is the check named, not the take
      expect(auditAddress(st, OWN)).toBeNull();
    }
  });

  it("is none for a device whose id is not in One's form, such as a token-shaped value a program put in the record", () => {
    const tokenShaped = { ...paired, device_id: "tok-SECRET-1234567890abcdefghij" };
    takeSite(tokenShaped);
    expect(siteTaken(tokenShaped)).toBe(true);
    expect(auditAddress(tokenShaped, OWN)).toBeNull();
    expect(sessionAudit(tokenShaped, { id: "0123456789ab", program: "claude" } as SessionRecord, OWN)).toBeNull();
  });

  it("is made of the site and the device alone: a token or a code that reached the status would not reach the address", () => {
    const leaky = { ...paired, token: "tok-SECRET-1234567890", code: "ABC-DEF-GHJ" } as ProofpaneStatus;
    const out = auditAddress(leaky, OWN);
    expect(out).toBe(AT);
    expect(out).not.toMatch(/tok-SECRET|ABC-DEF-GHJ/);
  });
});

describe("a session's audit in Proofpane One", () => {
  const session = (id: string, program: string) => ({ id, program }) as SessionRecord;

  it("names the session alone, encoded, so that its rows under an earlier pairing's device are reached too", () => {
    expect(auditAddress(paired, OWN, "0123456789ab")).toBe("https://app.example.test/audit?session=0123456789ab");
    expect(auditAddress(paired, OWN, "a b&c")).toBe("https://app.example.test/audit?session=a%20b%26c");
  });

  it("is a Claude Code terminal's or chat's, or a Codex session's, whose rows name it, and no other program's", () => {
    expect(sessionAudit(paired, session("0123456789ab", "claude"), OWN))
      .toBe("https://app.example.test/audit?session=0123456789ab");
    expect(sessionAudit(paired, session("0123456789ac", "chat"), OWN))
      .toBe("https://app.example.test/audit?session=0123456789ac");
    expect(sessionAudit(paired, session("0123456789ae", "codex"), OWN))
      .toBe("https://app.example.test/audit?session=0123456789ae");
    for (const program of ["gemini", "kimi", "opencode", "shell"])
      expect(sessionAudit(paired, session("0123456789ad", program), OWN)).toBeNull();
    expect(sessionAudit({ ...paired, site_url: null }, session("0123456789ab", "claude"), OWN)).toBeNull();
    expect(sessionAudit(null, session("0123456789ab", "claude"), OWN)).toBeNull();
  });
});

describe("the audit links' words and the page's own sites", () => {
  it("name the site the link opens", () => {
    expect(auditWords(AT)).toBe("Open the audit in Proofpane One at app.example.test");
    expect(sessionAuditWords("https://app.example.test:8443/audit?session=s"))
      .toBe("This session in Proofpane One's audit at app.example.test:8443");
  });

  it("is none at the page's own site when no list of sites is given, as where the page itself calls it", () => {
    const own = { ...paired, site_url: location.origin };
    takeSite(own);
    expect(siteTaken(own)).toBe(true);
    expect(auditAddress(own)).toBeNull();
    takeSite(paired);
    expect(auditAddress(paired)).toBe(AT);
  });
});

describe("the site this browser took", () => {
  it("opens nothing at a site this browser has not taken for the device, as a site written into the record later", () => {
    localStorage.clear();
    expect(auditAddress(paired, OWN)).toBeNull();
    expect(takeSite(paired)).toBe(true);
    expect(auditAddress(paired, OWN)).toBe(AT);
    const rewritten = { ...paired, cloud_url: "https://api.example-signin.test", site_url: "https://app.example-signin.test" };
    expect(siteTaken(rewritten)).toBe(false);
    expect(auditAddress(rewritten, OWN)).toBeNull();
    expect(sessionAudit(rewritten, { id: "0123456789ab", program: "claude" } as SessionRecord, OWN)).toBeNull();
  });

  it("is the device's own: another device's status, with the same site, opens nothing until taken for it", () => {
    expect(auditAddress({ ...paired, device_id: OTHER }, OWN)).toBeNull();
  });

  it("takes nothing from a status unpaired or with no site, and says so", () => {
    localStorage.clear();
    expect(takeSite({ ...paired, paired: false })).toBe(false);
    expect(takeSite({ ...paired, site_url: null })).toBe(false);
    expect(siteTaken(paired)).toBe(false);
  });
});
