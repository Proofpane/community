import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OpenIn, worktreeHere } from "./OpenIn";

afterEach(cleanup);

describe("a session's worktree in the person's own tools", () => {
  it("is shown in the Finder or opened in an editor installed here, named by the session's id alone", async () => {
    const run = vi.fn().mockResolvedValue(undefined);
    render(<OpenIn session="0123456789ab" run={run} editors={async () => ["Cursor", "Zed"]} />);
    fireEvent.click(screen.getByTitle("Open the worktree in the Finder or an editor"));
    expect(await screen.findByRole("menuitem", { name: "Open in Cursor" })).toBeTruthy();
    fireEvent.click(screen.getByRole("menuitem", { name: "Open in Cursor" }));
    expect(run).toHaveBeenLastCalledWith("open_worktree", { session: "0123456789ab", with: "Cursor" });
    fireEvent.click(screen.getByTitle("Open the worktree in the Finder or an editor"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Show in Finder" }));
    expect(run).toHaveBeenLastCalledWith("open_worktree", { session: "0123456789ab", with: null });
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("is offered only where the worktree is on this machine: this Mac's sessions, in the desktop app", () => {
    expect(worktreeHere()).toBe(false);          // a browser's page, here: its files are the workspace's, elsewhere
  });
});
