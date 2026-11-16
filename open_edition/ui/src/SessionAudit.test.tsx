import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { SessionRecord } from "./api";
import { SessionActions } from "./App";
import { useStore } from "./store";

// AUDIT-BUTTON (the work queue's section 4.311), its second part: a session's header links to that session's rows in
// the audit in Proofpane One, where the App gives it the address (proofpaneAudit.ts's sessionAudit).

afterEach(() => { cleanup(); useStore.setState(useStore.getInitialState()); });

const rec = { id: "0123456789ab", program: "claude", repo: "demo", branch: "pp/0123456789ab", base: "abc", task: "Fix it",
  created_at: 1, running: false, finished: null, exit_code: 0, ended_at: null, chat: null } as unknown as SessionRecord;
const AT = "https://app.example.test/audit?session=0123456789ab";

describe("a session's link to its audit in Proofpane One", () => {
  it("is in its header where the App gives the address, opening beside the page", () => {
    render(<SessionActions rec={rec} grouped={false} onShip={() => {}} audit={AT} />);
    const link = screen.getByRole("link", { name: "This session in Proofpane One's audit at app.example.test" });
    expect(link.getAttribute("href")).toBe(AT);
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("is not there where the App gives none (guard)", () => {
    render(<SessionActions rec={rec} grouped={false} onShip={() => {}} audit={null} />);
    expect(screen.queryByRole("link", { name: /This session in Proofpane One's audit/ })).toBeNull();
  });
});
