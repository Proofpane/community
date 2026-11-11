import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { HistorySession } from "../api";
import type { Session } from "../types";
import { machineRepo, Sidebar } from "./Sidebar";

afterEach(cleanup);

const ours = (over: Partial<Session>): Session => ({ id: "w1", title: "a workspace session", repo: "app", agent: "claude", state: "done", ...over });
const theirs = (over: Partial<HistorySession>): HistorySession => ({
  id: "m1", title: "on the Mac", project: "~/work/app", branch: "main", started_at: 1_790_000_000, last_at: 1_790_003_600,
  link_id: "L1", link: "mac", kind: "local", listed_at: Date.now() / 1000 - 60, client: "vscode", ...over,
});

const list = (machine: HistorySession[], onMachine = vi.fn(), sessions = [ours({})]) =>
  render(<Sidebar repos={["app"]} sessions={sessions} selected={null} onSelect={vi.fn()} onNew={vi.fn()} onSettings={vi.fn()}
    machine={machine} onMachine={onMachine} />);

describe("the list of sessions", () => {
  it("lists each machine's sessions with the workspace's, under the repository each worked in first, marked where they ran", () => {
    const onMachine = vi.fn();
    const mac = theirs({ id: "m1", title: "on the Mac", repositories: ["~/work/app"] });
    list([mac, theirs({ id: "m2", title: "elsewhere", repositories: ["~/other/tools"], client: "terminal" })], onMachine);
    const app = screen.getByText("app").closest("div.mb-1\\.5") as HTMLElement;
    expect(within(app).getByText("a workspace session")).toBeTruthy();
    expect(within(app).getByText("on the Mac")).toBeTruthy();               // one group per repository
    expect(within(app).getByText("mac · VS Code · main")).toBeTruthy();
    const tools = screen.getByText("tools").closest("div.mb-1\\.5") as HTMLElement;
    expect(within(tools).getByText("elsewhere")).toBeTruthy();
    expect(within(tools).queryByTitle("New session in tools")).toBeNull();   // not a repository of the workspace
    fireEvent.click(screen.getByText("on the Mac"));
    expect(onMachine).toHaveBeenCalledWith(mac);
  });

  it("keeps a withheld session's title on its machine", () => {
    list([theirs({ title: null, branch: null, withheld: true })]);
    expect(screen.getByText("Title kept on mac's machine")).toBeTruthy();
  });

  it("shows a machine's sessions under All, and under Active only while its machine's word says they run", () => {
    list([theirs({ id: "a", title: "running", live: "working" }), theirs({ id: "b", title: "idle" }),
      theirs({ id: "c", title: "said long ago", live: "working", listed_at: 1 })]);
    fireEvent.click(screen.getByRole("button", { name: "Active" }));
    expect(screen.getByText("running")).toBeTruthy();
    expect(screen.queryByText("idle")).toBeNull();
    expect(screen.queryByText("said long ago")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Needs you" }));
    expect(screen.queryByText("running")).toBeNull();
  });

  it("names a machine's session's group by its repository's folder, else its own folder's", () => {
    expect(machineRepo({ repositories: ["~/work/app/"], project: "~/work/app/sub" })).toBe("app");
    expect(machineRepo({ repositories: [], project: "/srv/site" })).toBe("site");
    expect(machineRepo({ project: "~" })).toBe("~");
    expect(machineRepo({ project: null })).toBe("~");
  });
});

describe("the list while the workspace does not run", () => {
  const stopped = (machine: HistorySession[], more: Record<string, unknown> = {}) =>
    render(<Sidebar repos={[]} sessions={[]} selected={null} onSelect={vi.fn()} onNew={vi.fn()} onSettings={vi.fn()}
      machine={machine} onMachine={vi.fn()} workspace={false} {...more} />);

  it("lists the linked machines' sessions, says the workspace's show while it runs, and offers nothing that needs it", () => {
    stopped([theirs({ repositories: ["~/work/app"] })]);
    expect(screen.getByText("on the Mac")).toBeTruthy();
    expect(screen.getByText("The workspace's sessions are listed here while it runs.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^New/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Connections" })).toBeNull();
    expect(screen.queryByText("⌘N")).toBeNull();
    expect(screen.queryByText("⌥1–9")).toBeNull();
    expect(screen.queryByText("No repositories yet.")).toBeNull();
  });

  it("offers to link the Mac when nothing is listed and none is linked, and says what comes once one is", () => {
    const onLink = vi.fn();
    stopped([], { onLink, macLinked: false });
    fireEvent.click(screen.getByRole("button", { name: "Link your Mac" }));
    expect(onLink).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("No repositories yet.")).toBeNull();          // not to clone into a stopped workspace
    cleanup();
    stopped([], { onLink, macLinked: true });
    expect(screen.getByText(/Your Mac is linked: its Claude Code sessions are listed here once its link sends their list/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Link your Mac" })).toBeNull();
  });

  it("says nothing of an empty list until it is read", () => {
    stopped([], { onLink: vi.fn(), macLinked: false, loading: true });
    expect(screen.queryByRole("button", { name: "Link your Mac" })).toBeNull();
    expect(screen.queryByText(/once it is linked/)).toBeNull();
  });

  it("is the phone's drawer with a Close of its own", () => {
    const onClose = vi.fn();
    stopped([], { onClose });
    fireEvent.click(screen.getByRole("button", { name: "Close the list" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("sessions on more than one account", () => {
  it("are listed in a group for each account, its name after the repository's", () => {
    render(<Sidebar repos={["grove", "app"]} selected={null} onSelect={vi.fn()} onNew={vi.fn()} onSettings={vi.fn()} machine={[]}
      sessions={[ours({ id: "w1", title: "first one", repo: "grove", accountName: "Default" }),
                 ours({ id: "w2", title: "second one", repo: "grove", accountName: "Alma" }),
                 ours({ id: "w3", title: "a shell", repo: "app" })]} />);
    const own = screen.getByLabelText("grove, on Default");
    const alma = screen.getByLabelText("grove, on Alma");
    expect(within(own).getByText("first one")).toBeTruthy();
    expect(within(own).queryByText("second one")).toBeNull();
    expect(within(alma).getByText("second one")).toBeTruthy();
    expect(within(alma).getByText("· Alma")).toBeTruthy();
    expect(within(own).getByTitle("New session in grove")).toBeTruthy();       // the repository's own, on its first
    expect(within(alma).queryByTitle("New session in grove")).toBeNull();
    fireEvent.click(within(alma).getByTitle("grove: its sessions on the account Alma"));
    expect(screen.queryByText("second one")).toBeNull();                         // each group closes alone
    expect(screen.getByText("first one")).toBeTruthy();
    expect(screen.getByText("app")).toBeTruthy();                                // sessions naming no account: one group
    expect(screen.getByText("a shell")).toBeTruthy();
    expect(screen.queryByText(/^· /, { selector: "div[aria-label^='app'] span" })).toBeNull();
  });

  it("keeps the groups in one order whatever their sessions do, and the Mac's sessions in the repository's own", () => {
    // every session names an account, yet the Mac's are not listed under the first account's group, and the groups
    // do not change places as their sessions' states change
    const mac = theirs({ id: "m1", title: "on the Mac", repositories: ["~/work/grove"] });
    const draw = (alma: Session["state"], own: Session["state"]) => render(
      <Sidebar repos={["grove"]} selected={null} onSelect={vi.fn()} onNew={vi.fn()} onSettings={vi.fn()} machine={[mac]}
        sessions={[ours({ id: "w1", title: "on Default", repo: "grove", accountName: "Default", state: own }),
                   ours({ id: "w2", title: "on Alma", repo: "grove", accountName: "Alma", state: alma })]} />);
    const order = () => [...document.querySelectorAll("div.mb-1\\.5")].map((g) => g.getAttribute("aria-label") || g.textContent?.split("\n")[0]);
    draw("working", "done");
    const first = order();
    expect(first[1]).toBe("grove, on Default");
    expect(first[2]).toBe("grove, on Alma");
    const own = screen.getAllByText("grove")[0].closest("div.mb-1\\.5") as HTMLElement;
    expect(own.getAttribute("aria-label")).toBeNull();                  // the repository's own group, no account named
    expect(within(own).getByText("on the Mac")).toBeTruthy();
    expect(within(own).getByTitle("New session in grove")).toBeTruthy();
    expect(within(screen.getByLabelText("grove, on Alma")).queryByText("on the Mac")).toBeNull();
    cleanup();
    draw("done", "working");                                             // the other one at work now: the same order
    expect(order()).toEqual(first);
  });
});

describe("closing a session", () => {
  it("leaves a closed one out of the list, says so where none of a repository's is open, and closes one with its own button", () => {
    const onCloseSession = vi.fn();
    const onSelect = vi.fn();
    const onOrder = vi.fn();
    render(<Sidebar repos={["app", "tools"]} selected={null} onSelect={onSelect} onNew={vi.fn()} onSettings={vi.fn()} machine={[]}
      onCloseSession={onCloseSession} onOrder={onOrder} linked={{ tools: "/Users/me/tools" }} onUnlink={vi.fn()} local
      sessions={[ours({ id: "w1", title: "still open" }), ours({ id: "w2", title: "closed one", closed: true }),
                 ours({ id: "w3", title: "closed in tools", repo: "tools", closed: true })]} />);
    expect(screen.getByText("still open")).toBeTruthy();
    expect(screen.queryByText("closed one")).toBeNull();
    expect(screen.queryByText("closed in tools")).toBeNull();
    expect(onOrder).toHaveBeenLastCalledWith(["w1"]);                                // ⌥1–9 and ⌥↑/↓ skip it too
    const tools = screen.getByText("tools").closest("div.mb-1\\.5") as HTMLElement;
    expect(within(tools).getByText("1 closed: search (⌘K) finds it")).toBeTruthy();
    // still there, so its repository is not offered to be taken out, as one with a session open is not
    expect(within(tools).queryByTitle(/Take tools out of the workspace/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Close still open" }));
    expect(onCloseSession).toHaveBeenCalledWith("w1");
    expect(onSelect).not.toHaveBeenCalled();                                          // closing one is not choosing it
  });

  it("offers no close where none is given", () => {
    list([], vi.fn(), [ours({ title: "a workspace session" })]);
    expect(screen.queryByRole("button", { name: "Close a workspace session" })).toBeNull();
  });
});
