import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { CommandPalette } from "./CommandPalette";
import { Modal } from "./Modal";
import { NewSession, readDraft } from "./NewSession";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });
beforeAll(() => {
  Element.prototype.scrollIntoView ??= () => {};
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
});

const open = (onStart = vi.fn()) => render(<NewSession repos={["demo"]} onStart={onStart} onClose={() => {}} onClone={() => {}}
  agents={[{ kind: "claude", available: true }]} />);

describe("a task being written in New session", () => {
  it("is kept when the dialog closes, and gone once its sessions start", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ set: false }), { status: 200 })));
    const first = open();
    fireEvent.change(screen.getByPlaceholderText(/What should the agent do/), { target: { value: "Fix the parser's dates" } });
    first.unmount();                                            // Escape, or a click beside it
    const onStart = vi.fn();
    open(onStart);
    expect((screen.getByPlaceholderText(/What should the agent do/) as HTMLTextAreaElement).value).toBe("Fix the parser's dates");
    fireEvent.click(screen.getByRole("button", { name: /^Start$/ }));
    await vi.waitFor(() => expect(onStart).toHaveBeenCalled());
    await vi.waitFor(() => expect(readDraft()).toBe(""));
  });
});

describe("Escape, with the palette over a dialog", () => {
  it("closes the palette alone, and the dialog under it stays", () => {
    const closeDialog = vi.fn();
    const closePalette = vi.fn();
    render(<>
      <Modal title="New session" onClose={closeDialog}><input aria-label="Task" /></Modal>
      <CommandPalette sessions={[]} actions={[]} onPick={() => {}} onClose={closePalette} />
    </>);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(closePalette).toHaveBeenCalled();
    expect(closeDialog).not.toHaveBeenCalled();
  });

  it("closes a dialog that is the top layer", () => {
    const closeDialog = vi.fn();
    render(<Modal title="New session" onClose={closeDialog}><input aria-label="Task" /></Modal>);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(closeDialog).toHaveBeenCalled();
  });
});
