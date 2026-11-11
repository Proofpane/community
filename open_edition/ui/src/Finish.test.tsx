import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Api, type SessionRecord } from "./api";
import { finishWords, SessionActions } from "./App";
import { Verdict, verdictWords } from "./components/bits";
import { useStore } from "./store";

afterEach(() => { cleanup(); vi.restoreAllMocks(); useStore.setState(useStore.getInitialState()); });

const rec = (over: Partial<SessionRecord> = {}) =>
  ({ id: "0123456789ab", program: "claude", repo: "demo", branch: "pp/0123456789ab", base: "abc", task: "Fix it", created_at: 1,
     running: false, finished: null, exit_code: 0, ended_at: 2, chat: null, ...over }) as SessionRecord;
const NO_CHECKS = { id: "V-001", resolver: "ci.checks_green", reason: "no expected check applies to this change" };

describe("a verdict, in words", () => {
  it("says what each rule means for the person", () => {
    expect(verdictWords({ rule: "all-pass", verify: "success", claims: "success" })).toEqual({ text: "Verified: every check passed", tone: "done" });
    expect(verdictWords({ rule: "needs-person", because: [NO_CHECKS] })).toEqual({ text: "Needs your review: no checks are set up for this change", tone: "wait" });
    expect(verdictWords({ rule: "needs-person", because: [NO_CHECKS, { id: "V-002", reason: "pending: backend" }] }).text)
      .toBe("Needs your review: something required could not be checked here");
    expect(verdictWords({ rule: "any-fail" })).toEqual({ text: "Not verified: a required check failed", tone: "fail" });
    expect(verdictWords({ rule: "nothing-blocking" }).tone).toBe("wait");
    expect(verdictWords({ rule: "advisory-not-vouched" }).tone).toBe("done");
  });

  it("heads the verdict wherever it is shown, over the checks' own words, in its colour", () => {
    render(<Verdict verify="failure" claims="failure" rule="needs-person" because={[NO_CHECKS]} />);
    expect(screen.getByText("Needs your review: no checks are set up for this change").className).toContain("text-wait");
    expect(screen.getByText("needs-person")).toBeTruthy();
    for (const b of screen.getAllByText("failure")) expect(b.className).toContain("text-wait");     // waiting, not failed
    cleanup();
    render(<Verdict verify="failure" claims="failure" rule="any-fail" />);
    for (const b of screen.getAllByText("failure")) expect(b.className).toContain("text-fail");
  });

  it("and a finish: what it committed, and what the verdict means", () => {
    expect(finishWords({ status: "verified", committed: true, files: 1, rule: "needs-person", because: [NO_CHECKS] }))
      .toEqual({ text: "Committed; 1 file changed since it started. Needs your review: no checks are set up for this change.", tone: "wait" });
    expect(finishWords({ status: "verified", committed: false, files: 3, rule: "all-pass" }).text)
      .toBe("Nothing new to commit; 3 files changed since it started. Verified: every check passed.");
    expect(finishWords({ status: "skipped", committed: false, reason: "no change since the session's base" }))
      .toEqual({ text: "Nothing new to commit; not verified: no change since the session's base.", tone: "wait" });
    expect(finishWords({ status: "verified", committed: true, rule: "all-pass" }).text).toBe("Committed. Verified: every check passed.");
    expect(finishWords({ status: "error", committed: true, files: 2, reason: "verify-pr crashed" }).tone).toBe("fail");
  });
});

describe("a session's actions", () => {
  it("offer Ship where its repository has a remote, and make Finish the one to take where it has none", () => {
    const changed = rec({ diff: { files: 2, added: 5, removed: 1 } });       // ended, with something to ship
    useStore.setState({ repos: [{ name: "demo", origin: true }] });
    render(<SessionActions rec={changed} grouped={false} onShip={() => {}} />);
    expect(screen.getByText("Ship").closest("button")?.className).toContain("btn-primary");
    expect(screen.getByText("Finish").closest("button")?.className).not.toContain("btn-primary");
    cleanup();
    useStore.setState({ repos: [{ name: "demo", origin: false }] });
    render(<SessionActions rec={changed} grouped={false} onShip={() => {}} />);
    expect(screen.queryByText("Ship")).toBeNull();
    expect(screen.getByText("Finish").closest("button")?.className).toContain("btn-primary");
  });

  it("fill neither while there is nothing to ship yet, or while the agent is at work", () => {
    useStore.setState({ repos: [{ name: "demo", origin: true }] });
    render(<SessionActions rec={rec({ diff: { files: 0, added: 0, removed: 0 } })} grouped={false} onShip={() => {}} />);
    expect(screen.getByText("Ship").closest("button")?.className).not.toContain("btn-primary");
    cleanup();
    const working = rec({ running: true, diff: { files: 2, added: 5, removed: 1 }, activity: { state: "working", at: 1 } } as never);
    render(<SessionActions rec={working} grouped={false} onShip={() => {}} />);
    expect(screen.getByText("Ship").closest("button")?.className).not.toContain("btn-primary");
  });

  it("offer Continue in a new session where the page passes it, and press nothing else", () => {
    useStore.setState({ repos: [{ name: "demo", origin: true }] });
    const onContinue = vi.fn();
    render(<SessionActions rec={rec()} grouped={false} onShip={() => {}} onContinue={onContinue} />);
    fireEvent.click(screen.getByRole("button", { name: "Continue in a new session" }));
    expect(onContinue).toHaveBeenCalledTimes(1);
    cleanup();
    render(<SessionActions rec={rec()} grouped={false} onShip={() => {}} />);
    expect(screen.queryByRole("button", { name: "Continue in a new session" })).toBeNull();
  });

  it("say what a finish did once it is done", async () => {
    useStore.setState({ repos: [{ name: "demo", origin: false }], refresh: async () => {} });
    vi.spyOn(Api, "finish").mockResolvedValue({ status: "verified", committed: true, files: 2, rule: "needs-person", because: [NO_CHECKS] });
    render(<SessionActions rec={rec()} grouped={false} onShip={() => {}} />);
    fireEvent.click(screen.getByText("Finish"));
    await vi.waitFor(() => expect(useStore.getState().notice).toEqual({
      text: "Committed to pp/0123456789ab; 2 files changed since it started. Needs your review: no checks are set up for this change. "
        + "To bring it into your branch: git merge pp/0123456789ab", tone: "wait" }));
  });
});

describe("the next step on a session's screen", () => {
  it("is Finish only while something is left to commit, where a repository is not shipped", () => {
    useStore.setState({ repos: [{ name: "demo", origin: false }] });
    render(<SessionActions rec={rec({ diff: { files: 2, added: 5, removed: 1, uncommitted: true } })} grouped={false} onShip={() => {}} />);
    expect(screen.getByRole("button", { name: "Finish" }).className).toContain("btn-primary");
    cleanup();
    render(<SessionActions rec={rec({ finished: { head: "abc", at: 2 }, diff: { files: 2, added: 5, removed: 1, uncommitted: false } })} grouped={false} onShip={() => {}} />);
    expect(screen.getByRole("button", { name: "Finish again" }).className).not.toContain("btn-primary");
  });

  it("is Ship until what changed is shipped, and not offered for a remote elsewhere than GitHub", () => {
    useStore.setState({ repos: [{ name: "demo", origin: true, github: true }] });
    const shipped = { url: "https://github.com/acme/demo/pull/7", at: 3, commits: 1 };
    render(<SessionActions rec={rec({ shipped, diff: { files: 2, added: 5, removed: 1, uncommitted: false } } as never)} grouped={false} onShip={() => {}} />);
    expect(screen.getByText("Ship").closest("button")?.className).not.toContain("btn-primary");
    cleanup();
    useStore.setState({ repos: [{ name: "demo", origin: true, github: false }] });
    render(<SessionActions rec={rec({ diff: { files: 2, added: 5, removed: 1, uncommitted: true } })} grouped={false} onShip={() => {}} />);
    expect(screen.queryByText("Ship")).toBeNull();
    expect(screen.getByRole("button", { name: "Finish" }).className).toContain("btn-primary");
  });
});

describe("removing a session", () => {
  it("says so in words while it waits for the second click, and then what became of its branch", async () => {
    useStore.setState({ repos: [{ name: "demo", origin: false }], refresh: async () => {} });
    vi.spyOn(Api, "removeSession").mockResolvedValue({ removed: "0123456789ab", branch: "pp/0123456789ab", branch_kept: true });
    render(<SessionActions rec={rec()} grouped={false} onShip={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove the session" }));
    const armed = screen.getByRole("button", { name: "Click again to remove the session" });
    expect(armed.textContent).toContain("Click again to remove");
    fireEvent.click(armed);
    await vi.waitFor(() => expect(useStore.getState().notice?.text).toBe("Removed. Its branch pp/0123456789ab stays in demo."));
  });
});


describe("a session in a repository added from this machine", () => {
  const local = { name: "demo", origin: false, linked: "/Users/you/demo", branch: "main" };
  const finished = { head: "f".repeat(40), at: 5 };

  it("is merged into its branch once finished: Merge is the next step then, Finish while something is left", () => {
    useStore.setState({ repos: [local] });
    render(<SessionActions rec={rec({ finished, diff: { files: 2, added: 5, removed: 1, uncommitted: false } })} grouped={false} onShip={() => {}} />);
    expect(screen.getByText("Merge into main").closest("button")?.className).toContain("btn-primary");
    expect(screen.getByRole("button", { name: "Finish again" }).className).not.toContain("btn-primary");
    cleanup();
    render(<SessionActions rec={rec({ finished, diff: { files: 2, added: 5, removed: 1, uncommitted: true } })} grouped={false} onShip={() => {}} />);
    expect(screen.getByText("Merge into main").closest("button")?.className).not.toContain("btn-primary");
    expect(screen.getByRole("button", { name: "Finish again" }).className).toContain("btn-primary");
  });

  it("says what the merge did, and is merged after it", async () => {
    useStore.setState({ repos: [local], refresh: async () => {} });
    const land = vi.spyOn(Api, "land").mockResolvedValue({ into: "main", head: "f".repeat(40), commits: 1 });
    render(<SessionActions rec={rec({ finished, diff: { files: 2, added: 5, removed: 1, uncommitted: false } })} grouped={false} onShip={() => {}} />);
    fireEvent.click(screen.getByText("Merge into main"));
    await vi.waitFor(() => expect(useStore.getState().notice?.text).toBe("Merged pp/0123456789ab into main: 1 commit, as a fast-forward."));
    expect(land).toHaveBeenCalledWith("0123456789ab");
    cleanup();
    render(<SessionActions rec={rec({ finished, landed: { into: "main", head: "f".repeat(40), at: 6 }, diff: { files: 2, added: 5, removed: 1, uncommitted: false } })} grouped={false} onShip={() => {}} />);
    const done = screen.getByText("Merged into main").closest("button")!;
    expect(done.disabled).toBe(true);
    expect(done.className).not.toContain("btn-primary");
  });

  it("waits while its agent is at work", () => {
    useStore.setState({ repos: [local] });
    const working = rec({ running: true, finished, activity: { state: "working", at: 1 }, diff: { files: 2, added: 5, removed: 1, uncommitted: false } } as never);
    render(<SessionActions rec={working} grouped={false} onShip={() => {}} />);
    expect((screen.getByText("Merge into main").closest("button") as HTMLButtonElement).disabled).toBe(true);
  });

  it("after a Finish, says that Merge is the way on", async () => {
    useStore.setState({ repos: [local], refresh: async () => {} });
    vi.spyOn(Api, "finish").mockResolvedValue({ status: "verified", committed: true, files: 2, rule: "all-pass" });
    render(<SessionActions rec={rec({ diff: { files: 2, added: 5, removed: 1, uncommitted: true } })} grouped={false} onShip={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Finish" }));
    await vi.waitFor(() => expect(useStore.getState().notice?.text)
      .toBe("Committed to pp/0123456789ab; 2 files changed since it started. Verified: every check passed. Then merge it into main, with Merge."));
  });

  it("is not offered before it is finished, nor for a repository cloned into the workspace", () => {
    useStore.setState({ repos: [local] });
    render(<SessionActions rec={rec({ diff: { files: 2, added: 5, removed: 1, uncommitted: true } })} grouped={false} onShip={() => {}} />);
    expect(screen.queryByText(/Merge into/)).toBeNull();
    cleanup();
    useStore.setState({ repos: [{ name: "demo", origin: true, github: false }] });
    render(<SessionActions rec={rec({ finished, diff: { files: 2, added: 5, removed: 1, uncommitted: false } })} grouped={false} onShip={() => {}} />);
    expect(screen.queryByText(/Merge into/)).toBeNull();
  });
});
