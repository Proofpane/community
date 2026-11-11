import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useEscape } from "./bits";

afterEach(cleanup);

function Drawer({ onClose, on = true, held, fields = false }: { onClose: () => void; on?: boolean; held?: () => boolean; fields?: boolean }) {
  useEscape(onClose, on, held);
  return <aside>a drawer {fields && <><textarea aria-label="Words" /> <input aria-label="A line" /></>}</aside>;
}

describe("Escape, over a drawer, and what is typed in it", () => {
  it("never closes it from a field that holds words, and closes it from one that holds none", () => {
    const onClose = vi.fn();
    const { getByLabelText } = render(<Drawer onClose={onClose} fields />);
    for (const label of ["Words", "A line"]) {
      const field = getByLabelText(label) as HTMLTextAreaElement | HTMLInputElement;
      fireEvent.change(field, { target: { value: "run the tests" } });
      field.focus();
      fireEvent.keyDown(field, { key: "Escape" });
      expect(onClose, label).not.toHaveBeenCalled();
      fireEvent.change(field, { target: { value: "" } });
      fireEvent.keyDown(field, { key: "Escape" });
      expect(onClose, label).toHaveBeenCalledTimes(1);
      onClose.mockClear();
    }
  });

  it("stays open while the drawer says it holds words elsewhere, wherever the key was pressed", () => {
    const onClose = vi.fn();
    let holding = true;
    render(<Drawer onClose={onClose} held={() => holding} />);
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
    holding = false;
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("Escape, over a drawer", () => {
  it("closes it", () => {
    const onClose = vi.fn();
    render(<Drawer onClose={onClose} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: "Enter" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does nothing while the drawer is not shown", () => {
    const onClose = vi.fn();
    render(<Drawer onClose={onClose} on={false} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("leaves it open when a dialog or the palette is over it, as Escape closes that first", () => {
    const onClose = vi.fn();
    const { rerender } = render(<><Drawer onClose={onClose} /><div role="dialog" aria-label="Over it" /></>);
    fireEvent.keyDown(window, { key: "Escape" });
    rerender(<><Drawer onClose={onClose} /><div cmdk-root="" /></>);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
    rerender(<Drawer onClose={onClose} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("leaves it open when a field used the key, to end its own editing", () => {
    const onClose = vi.fn();
    const { getByRole } = render(<><Drawer onClose={onClose} /><input onKeyDown={(e) => e.key === "Escape" && e.preventDefault()} /></>);
    fireEvent.keyDown(getByRole("textbox"), { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
