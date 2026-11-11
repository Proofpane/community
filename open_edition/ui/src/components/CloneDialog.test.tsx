import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Api, type WorkspaceState } from "../api";
import type { Session } from "../types";
import { useStore } from "../store";
import { CloneDialog } from "./CloneDialog";
import { Sidebar } from "./Sidebar";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const ws = (provider: string) => ({ enabled: true, allowed: true, running: true, provider }) as WorkspaceState;

describe("a repository from a folder on this Mac", () => {
  it("is offered where the workspace is on the person's own machine, and added by its path", async () => {
    const linked = vi.spyOn(Api, "linkRepo").mockResolvedValue({ name: "parser", label: "acme/parser", linked: "/Users/ana/code/parser" });
    useStore.setState({ ws: ws("local"), github: { installed: true, user: null, repos: [], capped: false },
      refresh: async () => {}, refreshGithub: async () => {} });
    const onCloned = vi.fn();
    render(<CloneDialog onClose={() => {}} onConnections={() => {}} onCloned={onCloned} />);
    expect(screen.getByText("Add a repository")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("The repository's folder"), { target: { value: "  /Users/ana/code/parser  " } });
    fireEvent.click(screen.getByText("Add"));
    await vi.waitFor(() => expect(onCloned).toHaveBeenCalledWith("parser"));
    expect(linked).toHaveBeenCalledWith("/Users/ana/code/parser");
  });

  it("is picked in the system's own dialog in the desktop app, and added as soon as it is picked", async () => {
    const linked = vi.spyOn(Api, "linkRepo").mockResolvedValue({ name: "parser", label: "acme/parser", linked: "/Users/ana/code/parser" });
    useStore.setState({ ws: ws("local"), github: { installed: true, user: null, repos: [], capped: false },
      refresh: async () => {}, refreshGithub: async () => {} });
    const onCloned = vi.fn();
    const pick = vi.fn<() => Promise<string | null>>().mockResolvedValueOnce(null).mockResolvedValueOnce("/Users/ana/code/parser");
    render(<CloneDialog onClose={() => {}} onConnections={() => {}} onCloned={onCloned} pickFolder={pick} />);
    expect(screen.queryByText(/copies a folder's path/)).toBeNull();      // a dialog, not a typed path
    fireEvent.click(screen.getByText("Choose…"));                         // cancelled: nothing is added
    await vi.waitFor(() => expect(pick).toHaveBeenCalledTimes(1));
    await vi.waitFor(() => expect((screen.getByText("Choose…").closest("button") as HTMLButtonElement).disabled).toBe(false));
    expect(linked).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Choose…"));
    await vi.waitFor(() => expect(onCloned).toHaveBeenCalledWith("parser"));
    expect(linked).toHaveBeenCalledWith("/Users/ana/code/parser");
  });

  it("opens the system's dialog once at a time: a second click waits for the first", async () => {
    vi.spyOn(Api, "linkRepo").mockResolvedValue({ name: "parser", label: "acme/parser", linked: "/p" });
    useStore.setState({ ws: ws("local"), github: { installed: true, user: null, repos: [], capped: false },
      refresh: async () => {}, refreshGithub: async () => {} });
    let answer: (v: string | null) => void = () => {};
    const pick = vi.fn(() => new Promise<string | null>((resolve) => { answer = resolve; }));
    render(<CloneDialog onClose={() => {}} onConnections={() => {}} onCloned={() => {}} pickFolder={pick} />);
    fireEvent.click(screen.getByText("Choose…"));
    fireEvent.click(screen.getByText("Choose…"));
    expect(pick).toHaveBeenCalledTimes(1);
    answer(null);
    await vi.waitFor(() => expect((screen.getByText("Choose…").closest("button") as HTMLButtonElement).disabled).toBe(false));
  });

  it("is not offered for a hosted workspace, which has no folder of the person's", () => {
    useStore.setState({ ws: ws("fly"), github: { installed: true, user: null, repos: [], capped: false },
      refresh: async () => {}, refreshGithub: async () => {} });
    render(<CloneDialog onClose={() => {}} onConnections={() => {}} />);
    expect(screen.getByText("Clone a repository")).toBeTruthy();
    expect(screen.queryByLabelText("The repository's folder")).toBeNull();
  });

  it("says what the workspace said when it is not one", async () => {
    vi.spyOn(Api, "linkRepo").mockRejectedValue(new Error("That folder is not a repository's own: choose the folder that holds its .git folder."));
    useStore.setState({ ws: ws("local"), github: { installed: true, user: null, repos: [], capped: false },
      refresh: async () => {}, refreshGithub: async () => {} });
    render(<CloneDialog onClose={() => {}} onConnections={() => {}} />);
    fireEvent.change(screen.getByLabelText("The repository's folder"), { target: { value: "/Users/ana/Downloads" } });
    fireEvent.keyDown(screen.getByLabelText("The repository's folder"), { key: "Enter" });
    expect(await screen.findByText(/not a repository's own/)).toBeTruthy();
  });

  it("is marked in the list, and taken out only once it has no sessions, its folder left as it is", () => {
    const onUnlink = vi.fn();
    const session: Session = { id: "0123456789ab", repo: "kept", title: "t", state: "idle", agent: "shell" };
    render(<Sidebar repos={["parser", "kept", "cloned"]} linked={{ parser: "/Users/ana/code/parser", kept: "/Users/ana/code/kept" }}
      onUnlink={onUnlink} local sessions={[session]} selected={null} onSelect={() => {}} onNew={() => {}} onSettings={() => {}} />);
    expect(screen.getAllByLabelText("on this Mac")).toHaveLength(2);
    expect(screen.getByTitle("On this Mac, used where it is: /Users/ana/code/parser")).toBeTruthy();
    expect(screen.queryByTitle(/Take kept out/)).toBeNull();          // it has a session
    expect(screen.queryByTitle(/Take cloned out/)).toBeNull();        // a clone is not taken out here
    fireEvent.click(screen.getByTitle("Take parser out of the workspace: its folder stays as it is"));
    expect(onUnlink).toHaveBeenCalledWith("parser");
  });
});

describe("the dialog's filled action", () => {
  it("is the one whose field has something in it, and none before", () => {
    render(<CloneDialog onClose={() => {}} onConnections={() => {}} pickFolder={async () => null} />);
    expect(document.querySelectorAll(".btn-primary").length).toBe(0);
    fireEvent.change(screen.getByLabelText(/address/i), { target: { value: "https://github.com/acme/demo" } });
    expect(Array.from(document.querySelectorAll(".btn-primary")).map((b) => b.textContent?.trim())).toEqual(["Clone"]);
  });
});

