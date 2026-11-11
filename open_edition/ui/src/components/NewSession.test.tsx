import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HELD_HERE, HELD_NONE_HERE, HELD_REFUSED_HERE, MAX_TASK, NewSession, TELEPORT_TASK, heldSentence, taskFromLinear, taskFromPull } from "./NewSession";
import { Api, type Held, type LinearIssue, type PullListed } from "../api";
import { useStore } from "../store";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const pr = (over: Partial<PullListed> = {}): PullListed => ({
  number: 7, title: "Add the settings page", body: "It adds a form.", cut: false, url: "https://github.com/acme/demo/pull/7",
  branch: "settings", base: "main", author: "ana", draft: false, updated_at: "2026-10-01T10:00:00Z", ...over,
});

describe("a session started from a pull request", () => {
  it("is told to review it, with its title, text and address", () => {
    expect(taskFromPull(pr())).toBe("Review pull request #7: Add the settings page\n\nIt adds a form.\n\n[The pull request: https://github.com/acme/demo/pull/7]");
    expect(taskFromPull(pr({ body: "", url: null }))).toBe("Review pull request #7: Add the settings page");
    const long = taskFromPull(pr({ body: "x".repeat(MAX_TASK * 2), cut: true }));
    expect(long.length).toBeLessThanOrEqual(MAX_TASK);
    expect(long).toContain("[It goes on, in the pull request.]");
    expect(long.endsWith("[The pull request: https://github.com/acme/demo/pull/7]")).toBe(true);
  });

  it("is picked from the open pull requests and starts from the head in place of a base", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ repo: "demo", pulls: [pr(), pr({ number: 9, title: "Draft work", draft: true })] }), { status: 200 })));
    const onStart = vi.fn();
    render(<NewSession repos={["demo"]} githubRepos={["demo"]} agents={[{ kind: "claude", available: true }]}
      onStart={onStart} onClose={() => {}} onClone={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText("the default branch"), { target: { value: "release" } });
    fireEvent.click(screen.getByText("From a pull request"));
    fireEvent.click(await screen.findByText("Add the settings page"));
    expect(screen.getByText("From pull request #7")).toBeTruthy();
    expect(screen.queryByPlaceholderText("the default branch")).toBeNull();          // the head, not a base
    expect((screen.getByPlaceholderText(/What should the agent do/) as HTMLTextAreaElement).value).toMatch(/^Review pull request #7/);
    fireEvent.click(screen.getByRole("button", { name: /^Start$/ }));
    await vi.waitFor(() => expect(onStart).toHaveBeenCalled());
    expect(onStart.mock.calls[0][0]).toMatchObject({ repo: "demo", pull: 7, base: undefined, agents: ["claude"] });
  });
});

const lin = (over: Partial<LinearIssue> = {}): LinearIssue => ({
  identifier: "ENG-12", title: "Totals are off by a cent", description: "Steps:\n1. Add two lines", cut: false,
  url: "https://linear.app/acme/issue/ENG-12/totals", state: "Todo", state_type: "unstarted", team: "Engineering",
  team_key: "ENG", assignee: "ana", priority: "High", updated_at: "2026-10-01T10:00:00Z", labels: ["bug"], ...over,
});

describe("a session started from a Linear issue", () => {
  it("is its identifier and title, its text, and where it is", () => {
    expect(taskFromLinear(lin())).toBe("ENG-12: Totals are off by a cent\n\nSteps:\n1. Add two lines\n\n[The issue in Linear: https://linear.app/acme/issue/ENG-12/totals]");
    expect(taskFromLinear(lin({ description: "", url: null }))).toBe("ENG-12: Totals are off by a cent");
    const long = taskFromLinear(lin({ description: "x".repeat(MAX_TASK * 2), cut: true }));
    expect(long.length).toBeLessThanOrEqual(MAX_TASK);
    expect(long).toContain("[It goes on, in Linear.]");
    expect(long.endsWith("[The issue in Linear: https://linear.app/acme/issue/ENG-12/totals]")).toBe(true);
  });

  it("is picked from the person's issues once Linear is connected, on any repository", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(
      String(url).includes("/linear/issues") ? { issues: [lin(), lin({ identifier: "ENG-13", title: "Another" })] }
        : String(url).endsWith("/linear") ? { set: true, user: { name: "Ana", email: null } } : {}), { status: 200 })));
    const onStart = vi.fn();
    render(<NewSession repos={["local"]} githubRepos={[]} agents={[{ kind: "claude", available: true }]}
      onStart={onStart} onClose={() => {}} onClone={() => {}} />);
    expect(screen.queryByText("From a GitHub issue")).toBeNull();          // not on GitHub
    fireEvent.click(await screen.findByText("From Linear"));
    fireEvent.click(await screen.findByText("Totals are off by a cent"));
    expect(screen.getByText("From ENG-12")).toBeTruthy();
    expect((screen.getByPlaceholderText(/What should the agent do/) as HTMLTextAreaElement).value).toMatch(/^ENG-12: Totals/);
    fireEvent.click(screen.getByRole("button", { name: /^Start$/ }));
    await vi.waitFor(() => expect(onStart).toHaveBeenCalled());
    expect(onStart.mock.calls[0][0]).toMatchObject({ repo: "local", agents: ["claude"] });
  });

  it("is not offered while Linear is not connected", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ set: false }), { status: 200 })));
    render(<NewSession repos={["demo"]} githubRepos={["demo"]} agents={[{ kind: "claude", available: true }]}
      onStart={() => {}} onClose={() => {}} onClone={() => {}} />);
    await screen.findByText("From a GitHub issue");
    expect(screen.queryByText("From Linear")).toBeNull();
  });
});

describe("the agent a new session starts with", () => {
  const start = async (onStart: ReturnType<typeof vi.fn>) => {
    fireEvent.change(screen.getByPlaceholderText(/What should the agent do/), { target: { value: "Fix the bug" } });
    fireEvent.click(screen.getByRole("button", { name: /^Start$/ }));
    await vi.waitFor(() => expect(onStart).toHaveBeenCalled());
    return onStart.mock.calls[0][0].agents;
  };

  it("is the first that can start: with Claude not signed in, not Claude Code", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ set: false }), { status: 200 })));
    const onStart = vi.fn();
    render(<NewSession repos={["demo"]} onStart={onStart} onClose={() => {}} onClone={() => {}} agents={[
      { kind: "claude", available: false, note: "Sign in to Claude first (Connections)" }, { kind: "codex", available: true },
      { kind: "chat", available: false }, { kind: "shell", available: true }]} />);
    expect(await start(onStart)).toEqual(["codex"]);
  });

  it("leaves the choice once the workspace says it cannot start, and can always be taken off", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ set: false }), { status: 200 })));
    const onStart = vi.fn();
    const offered = (claude: boolean) => [{ kind: "claude" as const, available: claude, note: "Sign in to Claude first (Connections)" },
      { kind: "codex" as const, available: true }, { kind: "shell" as const, available: true }];
    const { rerender } = render(<NewSession repos={["demo"]} agents={offered(true)} onStart={onStart} onClose={() => {}} onClone={() => {}} />);
    rerender(<NewSession repos={["demo"]} agents={offered(false)} onStart={onStart} onClose={() => {}} onClone={() => {}} />);
    expect(await start(onStart)).toEqual(["codex"]);
  });

  it("is a shell where no agent can start", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ set: false }), { status: 200 })));
    const onStart = vi.fn();
    render(<NewSession repos={["demo"]} onStart={onStart} onClose={() => {}} onClone={() => {}} agents={[
      { kind: "claude", available: false }, { kind: "codex", available: false }, { kind: "shell", available: true }]} />);
    fireEvent.click(screen.getByRole("button", { name: /^Start$/ }));
    await vi.waitFor(() => expect(onStart).toHaveBeenCalled());
    expect(onStart.mock.calls[0][0].agents).toEqual(["shell"]);
  });
});

describe("an agent that waits for a sign-in or a key", () => {
  it("is a click from Connections, where it gets one; and nothing is offered where no reason names Connections", () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ set: false }), { status: 200 })));
    const onConnections = vi.fn();
    const shown = (agents: { kind: "claude" | "codex" | "gemini" | "shell"; available: boolean; note?: string }[]) => {
      cleanup();
      render(<NewSession repos={["demo"]} agents={agents} onStart={() => {}} onClose={() => {}} onClone={() => {}}
        onConnections={onConnections} />);
      fireEvent.click(screen.getByRole("button", { name: /more, not ready here/ }));
      return screen.queryByRole("button", { name: "Open Connections, to sign in or add a key" });
    };
    const link = shown([{ kind: "codex", available: false, note: "Sign in to Codex first (Connections)" }, { kind: "shell", available: true }]);
    fireEvent.click(link!);
    expect(onConnections).toHaveBeenCalledTimes(1);
    expect(shown([{ kind: "gemini", available: false, note: "Not installed in this workspace" }, { kind: "shell", available: true }])).toBeNull();
  });
});

describe("trying a session's task with another agent", () => {
  it("keeps the task's own repository: the commit it starts from is that repository's", async () => {
    const onStart = vi.fn();
    render(<NewSession repos={["demo", "other"]} agents={[{ kind: "claude", available: true }, { kind: "codex", available: true }]}
      again={{ id: "0123456789ab", title: "Fix it", repo: "other", task: "Fix it", base: "a".repeat(40) }}
      onStart={onStart} onClose={() => {}} onClone={() => {}} />);
    const field = screen.getByLabelText("Repository");
    expect(field.tagName).not.toBe("SELECT");
    expect(field.textContent).toBe("other");
    expect(screen.queryByText("Clone…")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Codex/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Start$/ }));
    await vi.waitFor(() => expect(onStart).toHaveBeenCalled());
    expect(onStart.mock.calls[0][0]).toMatchObject({ repo: "other", base: "a".repeat(40), agents: ["codex"] });
  });
});

describe("the agents offered", () => {
  it("are those that can start; the rest, with why, under a row that opens", () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ set: false }), { status: 200 })));
    render(<NewSession repos={["demo"]} onStart={() => {}} onClose={() => {}} onClone={() => {}}
      customAgents={[{ id: "aaaaaaaaaaaa", name: "Aider", command: "aider", task: "argument", available: false }]}
      agents={[{ kind: "claude", available: true }, { kind: "gemini", available: false },
        { kind: "kimi", available: false, note: "Add your Kimi K3 API key in Connections" }, { kind: "shell", available: true }]} />);
    expect(screen.getByRole("button", { name: /Claude Code/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Gemini CLI/ })).toBeNull();
    const more = screen.getByRole("button", { name: "3 more, not ready here" });
    expect(more.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(more);
    expect((screen.getByRole("button", { name: /Gemini CLI/ }) as HTMLButtonElement).disabled).toBe(true);
    const why = screen.getByText("Add your Kimi K3 API key in Connections");
    expect(why.className).not.toContain("truncate");                         // said in full, wrapping
    expect(why.closest("button")!.className).not.toContain("opacity");      // and readable
    expect(screen.getByText("Not found in this workspace")).toBeTruthy();
  });

  it("show the rest at once where none can start", () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ set: false }), { status: 200 })));
    render(<NewSession repos={["demo"]} onStart={() => {}} onClose={() => {}} onClone={() => {}}
      agents={[{ kind: "claude", available: false }, { kind: "codex", available: false }]} />);
    expect(screen.getByRole("button", { name: /Claude Code/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /more, not ready here/ }).getAttribute("aria-expanded")).toBe("true");
  });
});

describe("a session continued from claude.ai", () => {
  const row = (id: string, name: string) => ({ id, name, created_at: null, sessions: 0, status: null, usage: null });
  const accounts = { claude: { active: "default", accounts: [row("default", "Default"), row("a1", "Alma")] },
                     codex: { active: "default", accounts: [row("default", "Default")] } };

  it("says whose cloud sessions it lists and that one on two repositories is under one, and offers the others", () => {
    render(<NewSession repos={["grove", "proofpane"]} agents={[{ kind: "claude", available: true }]} accounts={accounts}
      onStart={() => {}} onClose={() => {}} onClone={() => {}} />);
    fireEvent.change(screen.getByLabelText("Claude Code account"), { target: { value: "a1" } });
    fireEvent.click(screen.getByText("From claude.ai"));
    expect(screen.getByText(/lists the cloud sessions of the account Alma recorded on grove to pick one/)).toBeTruthy();
    expect(screen.getByText(/only in the one repository Claude Code recorded it under, its link given here included/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /From claude.ai/ }).getAttribute("title")).toContain("of the account Alma");
    fireEvent.click(screen.getByRole("button", { name: "proofpane" }));
    expect((screen.getByLabelText("Repository") as HTMLSelectElement).value).toBe("proofpane");
    expect(screen.getByText(/recorded on proofpane to pick one/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "grove" })).toBeTruthy();         // and back
  });

  it("names the workspace's own sign-in when it is the one chosen, and starts with the page's own task", async () => {
    const onStart = vi.fn();
    render(<NewSession repos={["grove"]} agents={[{ kind: "claude", available: true }]} accounts={accounts}
      onStart={onStart} onClose={() => {}} onClone={() => {}} />);
    fireEvent.click(screen.getByText("From claude.ai"));
    expect(screen.getByText(/lists the cloud sessions of Claude Code's own sign-in here recorded on grove/)).toBeTruthy();
    expect(screen.queryByText(/Not listed here\? Try it in/)).toBeNull();           // no other repository to try
    fireEvent.click(screen.getByRole("button", { name: /^Continue from claude\.ai$/ }));
    await vi.waitFor(() => expect(onStart).toHaveBeenCalled());
    expect(onStart.mock.calls[0][0]).toMatchObject({ repo: "grove", task: TELEPORT_TASK, teleport: "", accounts: { claude: "default" } });
  });
});

describe("the approval queue in a hosted workspace", () => {
  const open = (provider: string, held: Held | null) => render(<NewSession repos={["demo"]}
    agents={[{ kind: "claude", available: true }]} provider={provider} held={held} onStart={() => {}} onClose={() => {}}
    onClone={() => {}} />);

  it("is named only where the workspace's agent says its machine holds, and otherwise said as it is", () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    const said = (provider: string, held: Held | null) => {
      const r = open(provider, held);
      const text = [HELD_HERE, HELD_REFUSED_HERE, HELD_NONE_HERE].filter((t) => screen.queryByText(t) !== null);
      r.unmount();
      return text;
    };
    expect(said("fly", "held")).toEqual([HELD_HERE]);
    expect(said("fly", "refused")).toEqual([HELD_REFUSED_HERE]);
    expect(said("fly", "none")).toEqual([HELD_NONE_HERE]);
    expect(said("fly", null)).toEqual([]);                          // its agent has not said yet: nothing claimed
    for (const held of ["held", "refused", "none", null] as const) expect(said("local", held)).toEqual([]);
    expect(heldSentence(undefined, "held")).toBeNull();
  });

  it("names what it holds by the hook's list, and what it does not reach", () => {
    for (const named of ["git push", "gh pr merge or gh release", "fly deploy", "npm publish", "rm -rf",
      "git -C dir push", "an MCP server's tool", "an agent's run, a replay or a task's attempt", "the other programs",
      "a shell's own commands", "a claude started in one", "--bare", "rewrites its own settings", "managed settings",
      "your own agents", "what a command does"]) {
      expect(HELD_HERE).toContain(named);
    }
    expect(HELD_REFUSED_HERE).toContain("refused rather than held");
    expect(HELD_NONE_HERE).toContain("does not wait for your approval");
  });

  it("is what the workspace's agent says, and none where an agent says nothing of it", async () => {
    useStore.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true } });   // its account's read
    for (const [answer, held] of [[{ programs: {} }, "none"], [{ programs: {}, held: "held" }, "held"],
      [{ programs: {}, held: "refused" }, "refused"], [{ programs: {}, held: "yes" }, "none"]] as const) {
      vi.spyOn(Api, "programs").mockResolvedValue(answer);
      await useStore.getState().refreshPrograms();
      expect(useStore.getState().held).toBe(held);
    }
    useStore.setState(useStore.getInitialState());
  });
});
