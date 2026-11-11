import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import type { Session, Tab } from "../types";
import { SessionView } from "./SessionView";
import { Sidebar } from "./Sidebar";

afterEach(cleanup);

const session: Session = { id: "0123456789ab", title: "Fix it", repo: "demo", agent: "claude", state: "idle" };

function Tabs() {
  const [tab, setTab] = useState<Tab>("agent");
  return <SessionView session={session} tab={tab} onTab={setTab}><div /></SessionView>;
}

describe("a session's tabs", () => {
  it("say which is open, and the arrows move along them", () => {
    render(<Tabs />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.getAttribute("aria-selected"))).toEqual(["true", "false", "false", "false", "false"]);
    expect(tabs.map((t) => t.tabIndex)).toEqual([0, -1, -1, -1, -1]);       // one stop for Tab; the arrows inside
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: /Changes/ }).getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: /Changes/ }));
    fireEvent.keyDown(document.activeElement!, { key: "ArrowLeft" });
    fireEvent.keyDown(document.activeElement!, { key: "ArrowLeft" });    // past the first, round to the last
    expect(screen.getByRole("tab", { name: /Activity/ }).getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(document.activeElement!, { key: "Home" });
    expect(screen.getByRole("tab", { name: /Agent/ }).getAttribute("aria-selected")).toBe("true");
  });
});

describe("a tab's name", () => {
  it("says what it is, and its count in words, whichever of its parts a narrow pane shows", () => {
    const busy: Session = { ...session, diff: { files: 2, added: 9, removed: 0 }, shell: true };
    const { rerender } = render(<SessionView session={busy} tab="agent" onTab={() => {}}><div /></SessionView>);
    expect(screen.getAllByRole("tab").map((t) => t.getAttribute("aria-label")))
      .toEqual(["Agent", "Changes, 2 files", "Files", "Shell, a shell open", "Activity"]);
    // the header's size, whose "2 files" a narrow pane hides, is named in words too, never "+9 −0"
    expect(screen.getByRole("button", { name: "Review the changes: 9 lines added and 0 removed, in 2 files" })).toBeTruthy();
    rerender(<SessionView session={{ ...busy, diff: { files: 1, added: 1, removed: 0 }, shell: false }} tab="agent" onTab={() => {}}><div /></SessionView>);
    expect(screen.getByRole("tab", { name: "Changes, 1 file" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Shell" })).toBeTruthy();
  });
});

describe("a split session's tabs", () => {
  function Split() {
    const [tab, setTab] = useState<Tab>("changes");
    return <SessionView session={session} tab={tab} onTab={setTab} split onSplit={() => {}}><div /></SessionView>;
  }

  it("move along what is beside the agent, never to the agent, which would end the split", () => {
    render(<Split />);
    const changes = screen.getByRole("tab", { name: /Changes/ });
    changes.focus();
    fireEvent.keyDown(changes, { key: "ArrowLeft" });              // round past the agent to the last
    expect(screen.getByRole("tab", { name: /Activity/ }).getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: /Changes/ }).getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(document.activeElement!, { key: "Home" });
    expect(screen.getByRole("tab", { name: /Changes/ }).getAttribute("aria-selected")).toBe("true");
  });
});

describe("the list's filters", () => {
  it("say which is on", () => {
    render(<Sidebar repos={["demo"]} sessions={[session]} selected={null} onSelect={() => {}} onNew={() => {}} onSettings={() => {}} />);
    const all = screen.getByRole("button", { name: /^All/ });
    expect(all.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: /^Needs you/ }));
    expect(all.getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByRole("button", { name: /^Needs you/ }).getAttribute("aria-pressed")).toBe("true");
  });
});

describe("the list", () => {
  it("fills none of its buttons: the one filled action on a screen is the screen's own", () => {
    const { container } = render(<Sidebar repos={["demo"]} sessions={[session]} selected={null} onSelect={() => {}} onNew={() => {}} onSettings={() => {}} />);
    expect(container.querySelectorAll(".btn-primary").length).toBe(0);
    expect(screen.getByTitle("New session (⌘N)")).toBeTruthy();
  });
});
