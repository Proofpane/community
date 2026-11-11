import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useRef, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Modal, useFocusInto } from "./Modal";

afterEach(cleanup);

function Page({ onClose, autoFocusSecond = false }: { onClose?: () => void; autoFocusSecond?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Open it</button>
      {open && (
        <Modal title="A dialog" onClose={() => { onClose?.(); setOpen(false); }} footer={<button>Save</button>}>
          <input aria-label="First" />
          <input aria-label="Second" autoFocus={autoFocusSecond}
            onKeyDown={(e) => { if (e.key === "Escape") e.preventDefault(); }} />
          <button disabled>Not reachable</button>
          <div hidden><button>Hidden</button></div>
        </Modal>
      )}
    </>
  );
}

const tab = (shift = false) => fireEvent.keyDown(document.activeElement || document.body, { key: "Tab", shiftKey: shift });

describe("a dialog and the keyboard", () => {
  it("takes the keyboard to its first field, keeps it in the dialog both ways round, and gives it back on closing", () => {
    render(<Page />);
    const opener = screen.getByText("Open it");
    opener.focus();
    fireEvent.click(opener);
    expect(document.activeElement).toBe(screen.getByLabelText("First"));
    expect(screen.getByRole("dialog").getAttribute("aria-modal")).toBe("true");
    screen.getByText("Save").focus();                  // the last thing the keyboard reaches
    tab();
    expect(document.activeElement).toBe(screen.getByLabelText("Close"));    // round to the first
    tab(true);
    expect(document.activeElement).toBe(screen.getByText("Save"));         // and back the other way
    screen.getByRole("dialog").focus();                // the dialog itself, as when a click lands on its text
    tab(true);
    expect(document.activeElement).toBe(screen.getByText("Save"));         // backwards from it stays in it too
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("starts on what asks for the keyboard, and a field that uses Escape keeps the dialog open", () => {
    const onClose = vi.fn();
    render(<Page onClose={onClose} autoFocusSecond />);
    fireEvent.click(screen.getByText("Open it"));
    expect(document.activeElement).toBe(screen.getByLabelText("Second"));
    fireEvent.keyDown(screen.getByLabelText("Second"), { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByLabelText("First"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

function Drawer({ onClose }: { onClose: () => void }) {
  const panel = useRef<HTMLElement>(null);
  useFocusInto(panel);
  return <aside ref={panel} tabIndex={-1} aria-label="A drawer"><button onClick={onClose}>Close it</button></aside>;
}

function WithDrawer() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Open the drawer</button>
      {open && <Drawer onClose={() => setOpen(false)} />}
    </>
  );
}

describe("a drawer and the keyboard", () => {
  it("takes the keyboard to itself when it opens, and gives it back to what opened it", () => {
    render(<WithDrawer />);
    const opener = screen.getByText("Open the drawer");
    opener.focus();
    fireEvent.click(opener);
    expect(document.activeElement).toBe(screen.getByLabelText("A drawer"));
    fireEvent.click(screen.getByText("Close it"));
    expect(document.activeElement).toBe(opener);
  });
});

describe("the palette over the app's dialogs", () => {
  it("is rendered after every one of them, so it is the top layer and one Escape closes it alone", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const app = readFileSync(join(__dirname, "..", "App.tsx"), "utf8");
    const palette = app.indexOf("{palette && (");
    for (const dialog of ["{channelOpen && (", "{approvalsOpen && (", "{connections && <Connections", "{shipping && byId",
      "{account && <AccountDialog", "{keys && <Shortcuts", "{cloneOpen && <CloneDialog", "<NewSession"]) {
      expect(app.indexOf(dialog), dialog).toBeGreaterThan(0);
      expect(palette, dialog).toBeGreaterThan(app.indexOf(dialog));
    }
  });
});
