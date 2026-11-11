import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { DiffView, reviewText } from "./DiffView";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeAll(() => {
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
});

const DIFF = `diff --git a/a.txt b/a.txt
index 1111111..2222222 100644
--- a/a.txt
+++ b/a.txt
@@ -1,1 +1,2 @@
 one
+two
`;

function comment(_container: HTMLElement, text: string) {
  fireEvent.click(screen.getByRole("button", { name: "Comment on line 2 of a.txt" }));       // the added line's
  fireEvent.change(screen.getByPlaceholderText("What should change here?"), { target: { value: text } });
  fireEvent.click(screen.getByText("Add comment"));
}

describe("a review of a session's changes", () => {
  it("is kept when it did not reach the agent, and said to be sent when it did", async () => {
    const send = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const { container } = render(<DiffView diff={DIFF} onSendReview={send} />);
    comment(container, "Name it better");
    expect(screen.getByText("1 comment in this review")).toBeTruthy();
    fireEvent.click(screen.getByText("Send to the agent"));
    await vi.waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("1 comment in this review")).toBeTruthy();        // not lost
    fireEvent.click(screen.getByText("Send to the agent"));
    expect(await screen.findByText(/Sent to the agent as one review/)).toBeTruthy();
    expect(screen.queryByText("1 comment in this review")).toBeNull();
    expect(send.mock.calls[1][0]).toEqual([{ file: "a.txt", line: 2, side: "new", text: "Name it better" }]);
  });

  it("is kept, and offered as text to copy, where the agent cannot take one", async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText: write } });
    const { container } = render(<DiffView diff={DIFF} />);
    expect(screen.getByText(/the comments make one review, to copy for the agent/)).toBeTruthy();
    comment(container, "Name it better");
    expect(screen.queryByText("Send to the agent")).toBeNull();
    expect(screen.getByText(/cannot take a review here/)).toBeTruthy();
    fireEvent.click(screen.getByText("Copy as text"));
    expect(await screen.findByText("Copied")).toBeTruthy();
    expect(write).toHaveBeenCalledWith(reviewText([{ file: "a.txt", line: 2, side: "new", text: "Name it better" }]));
    expect(screen.getByText("1 comment in this review")).toBeTruthy();
  });
});

const TWO = `diff --git a/a.txt b/a.txt
index 1111111..2222222 100644
--- a/a.txt
+++ b/a.txt
@@ -1,2 +1,2 @@
 one
-two
+three
`;

describe("commenting on a line", () => {
  it("is reached by the keyboard: one stop in the file, the arrows from line to line, Enter to comment", () => {
    render(<DiffView diff={TWO} onSendReview={async () => true} />);
    const lines = screen.getAllByRole("button", { name: /^Comment on / });
    expect(lines.map((b) => b.getAttribute("aria-label"))).toEqual(
      ["Comment on line 1 of a.txt", "Comment on the removed line 2 of a.txt", "Comment on line 2 of a.txt"]);
    expect(lines.map((b) => b.tabIndex)).toEqual([0, -1, -1]);
    lines[0].focus();
    fireEvent.keyDown(lines[0], { key: "ArrowDown" });
    expect(document.activeElement).toBe(lines[1]);
    fireEvent.keyDown(lines[1], { key: "End" });
    expect(document.activeElement).toBe(lines[2]);
    fireEvent.click(lines[1]);                      // a button: Enter or Space presses it as a click does
    expect(screen.getByPlaceholderText("What should change here?")).toBeTruthy();
  });

  it("gives the focus back to the line once the comment is added or let go", async () => {
    render(<DiffView diff={TWO} onSendReview={async () => true} />);
    const line = () => screen.getByRole("button", { name: "Comment on line 2 of a.txt" });
    fireEvent.click(line());
    fireEvent.change(screen.getByPlaceholderText("What should change here?"), { target: { value: "Say three" } });
    fireEvent.click(screen.getByText("Add comment"));
    await vi.waitFor(() => expect(document.activeElement).toBe(line()));
    fireEvent.click(line());
    fireEvent.click(screen.getByText("Cancel"));
    await vi.waitFor(() => expect(document.activeElement).toBe(line()));
  });
});
