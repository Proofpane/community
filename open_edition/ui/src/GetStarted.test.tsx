import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Empty, workspaceWords } from "./App";

afterEach(cleanup);

const base = { hasRepos: false, hasSessions: false, claude: null, github: null, onNew: () => {}, onClone: () => {}, onConnections: () => {} };

describe("linking a Mac, from the first steps", () => {
  it("is offered beside them while none is linked, never as the next step, and ticked off once one is", () => {
    const onLink = vi.fn();
    render(<Empty {...base} claude github={false} agents={2} claudeInstalled macLinked={false} onLink={onLink} />);
    const step = screen.getAllByRole("listitem").find((li) => li.textContent?.includes("Link your Mac"))!;
    expect(step.textContent).toContain("optional");
    const link = within(step).getByRole("button", { name: "Link" });
    expect(link.className).not.toContain("btn-primary");
    fireEvent.click(link);
    expect(onLink).toHaveBeenCalledTimes(1);
    cleanup();
    render(<Empty {...base} claude github={false} agents={2} claudeInstalled macLinked onLink={onLink} />);
    const done = screen.getAllByRole("listitem").find((li) => li.textContent?.includes("Link your Mac"))!;
    expect(done.textContent).toContain("Done: Link your Mac");
    expect(within(done).queryByRole("button")).toBeNull();
  });

  it("is offered under Pick a session while none is linked, and not once one is", () => {
    const onLink = vi.fn();
    render(<Empty {...base} hasRepos hasSessions claude macLinked={false} onLink={onLink} />);
    fireEvent.click(screen.getByRole("button", { name: "Link your Mac" }));
    expect(onLink).toHaveBeenCalledTimes(1);
    cleanup();
    render(<Empty {...base} hasRepos hasSessions claude macLinked onLink={onLink} />);
    expect(screen.queryByRole("button", { name: "Link your Mac" })).toBeNull();
  });
});

describe("a workspace that does not run, in words", () => {
  it("is called stopped, keeping its work, only once it was ever started; one never started is to be started", () => {
    const never = workspaceWords({ enabled: true, provider: "fly", running: false, made: false });
    expect(never.title).toBe("Start your workspace");
    expect(`${never.title} ${never.body}`).not.toMatch(/stopped|kept/);
    const stopped = workspaceWords({ enabled: true, provider: "fly", running: false, made: true });
    expect(stopped.title).toBe("Your workspace is stopped");
    expect(stopped.body).toMatch(/kept/);
    // a server that does not say: words true either way
    const unsaid = workspaceWords({ enabled: true, provider: "fly", running: false });
    expect(`${unsaid.title} ${unsaid.body}`).not.toMatch(/stopped|kept/);
    // starting for the first time on Fly takes longer, and says so
    expect(workspaceWords({ enabled: true, provider: "fly", running: false, made: false, starting: true }).body).toMatch(/minute or two/);
    expect(workspaceWords({ enabled: true, provider: "local", running: false, made: false, starting: true }).body).toMatch(/few seconds/);
    expect(workspaceWords({ enabled: true, provider: "fly", running: false, made: true, starting: true }).body).toMatch(/come back/);
  });
});

describe("the first steps, on a workspace that runs the person's own agents", () => {
  it("start with installing an agent when none is there", () => {
    render(<Empty {...base} agents={0} claudeInstalled={false} />);
    const first = screen.getAllByRole("listitem")[0];
    expect(first.textContent).toContain("Install a coding agent");
    expect(first.textContent).toContain("npm install -g @anthropic-ai/claude-code");
    expect(first.querySelector("button")?.textContent).toBe("Reload");
    expect(screen.queryByText("Sign in to Claude")).toBeNull();
  });

  it("say Claude Code is not installed, without a sign-in it cannot do, when other agents are", () => {
    render(<Empty {...base} agents={2} claudeInstalled={false} />);
    const first = screen.getAllByRole("listitem")[0];
    expect(first.textContent).toContain("Claude Code is not installed");
    expect(first.querySelector("button")).toBeNull();
    expect(screen.queryByText("Sign in to Claude")).toBeNull();
  });

  it("offer Claude's sign-in when Claude Code is there", () => {
    render(<Empty {...base} agents={8} claudeInstalled />);
    expect(screen.getByText("Sign in to Claude")).toBeTruthy();
    render(<Empty {...base} />);          // nothing known yet: as before
    expect(screen.getAllByText("Sign in to Claude").length).toBe(2);
  });
});

describe("the first steps' one filled action", () => {
  it("is the next step on the way to a first session, never GitHub's, which is offered beside it", () => {
    render(<Empty {...base} claude github={false} agents={2} claudeInstalled />);
    const filled = Array.from(document.querySelectorAll(".btn-primary")).map((b) => b.textContent);
    expect(filled).toEqual(["Clone"]);
    expect(screen.getByRole("button", { name: "Connect" }).className).not.toContain("btn-primary");
  });

  it("is signing in to Claude while that is not done", () => {
    render(<Empty {...base} claude={false} github={false} agents={2} claudeInstalled />);
    expect(Array.from(document.querySelectorAll(".btn-primary")).map((b) => b.textContent)).toEqual(["Sign in"]);
  });
});

describe("a first step already done", () => {
  it("says so in words, not only with a tick and a line through it", () => {
    render(<Empty {...base} claude github={false} agents={2} claudeInstalled />);
    const done = screen.getAllByRole("listitem").find((li) => li.textContent?.includes("Sign in to Claude"))!;
    expect(done.textContent).toContain("Done: Sign in to Claude");
  });
});

