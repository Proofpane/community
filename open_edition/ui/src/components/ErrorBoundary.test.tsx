import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary, isChunkError } from "./ErrorBoundary";

let fail: Error | null = null;
function Part() {
  if (fail) throw fail;
  return <span>the part</span>;
}

describe("a part of the page that fails", () => {
  afterEach(() => { fail = null; vi.restoreAllMocks(); });

  it("is replaced by what happened and a way to try again, not a blank page", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    fail = new Error("the server said no");
    render(<ErrorBoundary what="The files tab"><Part /></ErrorBoundary>);
    expect(screen.getByRole("alert").textContent).toContain("The files tab stopped on an error: the server said no");
    fail = null;
    fireEvent.click(screen.getByText("Try again"));
    expect(screen.getByText("the part")).toBeTruthy();
  });

  it("that could not be loaded after an update offers only a reload", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    fail = new TypeError("Failed to fetch dynamically imported module: https://x/app/assets/CodeEditor-abc.js");
    render(<ErrorBoundary what="The editor"><Part /></ErrorBoundary>);
    expect(screen.getByRole("alert").textContent).toContain("The editor could not load: the app may have been updated");
    expect(screen.queryByText("Try again")).toBeNull();
    expect(screen.getByText("Reload the page")).toBeTruthy();
  });

  it("tells a missing part from other errors", () => {
    expect(isChunkError(new TypeError("Importing a module script failed."))).toBe(true);
    expect(isChunkError(new Error("error loading dynamically imported module"))).toBe(true);
    expect(isChunkError(new Error("Cannot read properties of undefined"))).toBe(false);
  });

  it("offers Close where it is given a way to close the part, and closes it there", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    fail = new Error("a time no Date can hold");
    const closed: string[] = [];
    const { unmount } = render(<ErrorBoundary what="The board" inline onClose={() => closed.push("board")}><Part /></ErrorBoundary>);
    expect(screen.getByRole("alert").textContent).toContain("The board stopped on an error: a time no Date can hold");
    fireEvent.click(screen.getByText("Close"));
    expect(closed).toEqual(["board"]);
    unmount();
    render(<ErrorBoundary what="The files tab"><Part /></ErrorBoundary>);
    expect(screen.queryByText("Close")).toBeNull();                  // none where no way to close is given
  });
});

