import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KeyBar } from "./TerminalPane";

afterEach(cleanup);

// The keys under a terminal on a touch screen. jsdom lays nothing out: what keeps them on the screen is that they share
// the row (each flex-1 with no width of its own, min-w-0) and the bar does not scroll sideways; on a 390-pixel phone
// nine keys were 40 pixels each at least, and the last one ran past the screen's edge to x 400.
describe("the terminal's keys on a touch screen", () => {
  it("share one row, none with a width of its own that could push the row past the screen, each 44 pixels tall", () => {
    render(<KeyBar onKey={() => {}} onAttach={() => {}} />);
    const bar = screen.getByRole("toolbar", { name: "Keys" });
    expect(bar.className).not.toMatch(/overflow-x/);
    const keys = within(bar).getAllByRole("button");
    expect(keys).toHaveLength(9);
    for (const k of keys) {
      const name = k.getAttribute("aria-label") || "";
      expect(k.className, name).toMatch(/(^| )flex-1( |$)/);
      expect(k.className, name).toMatch(/(^| )min-w-0( |$)/);
      expect(k.className, name).toMatch(/(^| )h-11( |$)/);
      expect(k.className, name).not.toMatch(/min-w-(?!0( |$))/);
      expect(k.className, name).toMatch(/(^| )px-0( |$)/);
    }
  });

  it("send each key's bytes, named for a screen reader, and attach a file", () => {
    const sent = vi.fn();
    const attach = vi.fn();
    render(<KeyBar onKey={sent} onAttach={attach} />);
    fireEvent.click(screen.getByRole("button", { name: "Up arrow" }));
    fireEvent.click(screen.getByRole("button", { name: "Control C" }));
    fireEvent.click(screen.getByRole("button", { name: "Escape" }));
    expect(sent.mock.calls.map((c) => c[0])).toEqual(["\x1b[A", "\x03", "\x1b"]);
    fireEvent.click(screen.getByRole("button", { name: "Attach a file" }));
    expect(attach).toHaveBeenCalledTimes(1);
    cleanup();
    render(<KeyBar onKey={sent} />);
    expect(within(screen.getByRole("toolbar")).getAllByRole("button")).toHaveLength(8);
  });
});
