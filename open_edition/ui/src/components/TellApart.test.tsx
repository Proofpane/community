// Sessions told apart at a glance: a Claude chat's badge is not Claude Code's, every badge has a name a screen reader
// says, and the header says where a session started as its Changes tab does.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { shortRef, toSession } from "../store";
import type { SessionRecord } from "../api";
import { AgentBadge } from "./bits";
import { TitleBar } from "./TitleBar";

afterEach(cleanup);

describe("telling sessions apart", () => {
  it("gives a Claude chat a badge of its own, each named", () => {
    render(<><AgentBadge kind="claude" /><AgentBadge kind="chat" /></>);
    expect(screen.getByRole("img", { name: "Claude Code" }).textContent).toBe("C");
    expect(screen.getByRole("img", { name: "Claude chat" }).textContent).toBe("Ch");
  });

  it("names the branch a session started from as people say it", () => {
    expect(shortRef("refs/heads/dev")).toBe("dev");
    expect(shortRef("refs/remotes/origin/main")).toBe("origin/main");
    expect(shortRef("refs/pull/7/head")).toBe("#7");
    expect(shortRef("main")).toBe("main");               // a record from before refs were kept in full
    expect(shortRef(null)).toBeUndefined();
    const s = toSession({ id: "0123456789ab", program: "claude", repo: "demo", branch: "pp/0123456789ab", base: "036da10cfb468afb",
      base_ref: "refs/heads/dev", task: "Fix it", created_at: 1, running: false, finished: null, exit_code: 0, ended_at: 2, chat: null } as SessionRecord);
    expect([s.baseRef, s.base]).toEqual(["dev", "036da10"]);
  });

  it("names the account button by the account, not by its first letter", () => {
    render(<TitleBar workspace={{ provider: "fly", running: true }} pendingApprovals={0} onSearch={() => {}} onApprovals={() => {}} account="you@example.org" />);
    expect(screen.getByRole("button", { name: "Your account, you@example.org" })).toBeTruthy();
  });
});
