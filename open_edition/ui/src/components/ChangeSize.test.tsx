import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { SessionRecord } from "../api";
import { toSession } from "../store";
import { sizeWords } from "./bits";
import { Sidebar } from "./Sidebar";

afterEach(cleanup);

const rec = (over: Partial<SessionRecord> = {}) =>
  ({ id: "0123456789ab", program: "claude", repo: "demo", branch: "pp/0123456789ab", base: "abc", task: "Fix it", created_at: 1,
     running: false, finished: null, exit_code: 0, ended_at: 2, chat: null, ...over }) as SessionRecord;

describe("a session's change size", () => {
  it("comes from the workspace's record, and none until it has worked it out", () => {
    expect(toSession(rec({ diff: { files: 4, added: 12, removed: 3 } })).diff).toEqual({ files: 4, added: 12, removed: 3 });
    expect(toSession(rec({ diff: null })).diff).toBeUndefined();
    expect(toSession(rec()).diff).toBeUndefined();
  });

  it("shows on the session's row in the list, and says itself in words", () => {
    render(<Sidebar repos={["demo"]} sessions={[toSession(rec({ diff: { files: 1, added: 12, removed: 3 } }))]} selected={null}
      onSelect={() => {}} onNew={() => {}} onSettings={() => {}} />);
    const size = screen.getByTitle("12 lines added and 3 removed, in 1 file");
    expect(size.textContent).toBe("+12 −3");
  });

  it("is not shown for a session with nothing changed", () => {
    render(<Sidebar repos={["demo"]} sessions={[toSession(rec({ diff: { files: 0, added: 0, removed: 0 } }))]} selected={null}
      onSelect={() => {}} onNew={() => {}} onSettings={() => {}} />);
    expect(screen.queryByTitle(/lines? added/)).toBeNull();
  });

  it("of a binary file alone is said as files, never as +0 -0", () => {
    render(<Sidebar repos={["demo"]} sessions={[toSession(rec({ diff: { files: 1, added: 0, removed: 0 } }))]} selected={null}
      onSelect={() => {}} onNew={() => {}} onSettings={() => {}} />);
    const size = screen.getByTitle("1 file changed, with no lines added or removed");
    expect(size.textContent).toBe("1 file");
  });

  it("is worded for one and for many", () => {
    expect(sizeWords({ files: 1, added: 1, removed: 0 })).toBe("1 line added and 0 removed, in 1 file");
    expect(sizeWords({ files: 3, added: 0, removed: 7 })).toBe("0 lines added and 7 removed, in 3 files");
  });
});
