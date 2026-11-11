import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { focusPane, showStarted } from "./App";
import { useStore } from "./store";

afterEach(cleanup);

describe("the focus, given to a session", () => {
  it("goes to its terminal once it shows, waiting for it", async () => {
    const { rerender } = render(<div />);
    focusPane("0123456789ab", undefined, 60);
    await new Promise((r) => setTimeout(r, 200));
    rerender(<div data-pane="0123456789ab"><textarea className="xterm-helper-textarea" aria-label="Terminal input" /></div>);
    await new Promise((r) => setTimeout(r, 200));
    expect(document.activeElement?.className).toBe("xterm-helper-textarea");
  });

  it("goes to a chat's box where there is no terminal", async () => {
    render(<div data-pane="0123456789ab"><textarea aria-label="Message" /></div>);
    focusPane("0123456789ab");
    await new Promise((r) => setTimeout(r, 120));
    expect(document.activeElement?.getAttribute("aria-label")).toBe("Message");
  });
});

describe("sessions just started", () => {
  afterEach(() => useStore.setState(useStore.getInitialState()));

  it("are shown, the first selected, and what is typed next goes to it", async () => {
    render(<><div data-pane="aaaaaaaaaaaa"><textarea aria-label="A" /></div><div data-pane="bbbbbbbbbbbb"><textarea aria-label="B" /></div></>);
    showStarted([{ id: "aaaaaaaaaaaa" }, { id: "bbbbbbbbbbbb" }]);
    expect(useStore.getState().panes).toEqual(["aaaaaaaaaaaa", "bbbbbbbbbbbb"]);
    expect(useStore.getState().selected).toBe("aaaaaaaaaaaa");
    await new Promise((r) => setTimeout(r, 120));
    expect(document.activeElement?.getAttribute("aria-label")).toBe("A");
  });

  it("beside the session whose task they try", () => {
    showStarted([{ id: "bbbbbbbbbbbb" }], { id: "aaaaaaaaaaaa" });
    expect(useStore.getState().panes).toEqual(["aaaaaaaaaaaa", "bbbbbbbbbbbb"]);
    expect(useStore.getState().selected).toBe("bbbbbbbbbbbb");
  });
});
