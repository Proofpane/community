import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Agent } from "../agents";
import { useStore } from "../store";
import { AgentForm } from "./AgentForm";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useStore.setState(useStore.getInitialState()); });
beforeEach(() => useStore.setState({ repos: [{ name: "demo" }, { name: "gone", missing: true }, { name: "notes" }] }));

const agent = (over: Partial<Agent> = {}): Agent => ({
  id: "a1a2a3a4a5a6", name: "Triage", group: null, brief: "Label the new issues.", repo: "demo", base_ref: null,
  keep_worktree: true, account: null, chat_settings: {}, allow: ["Bash(npm test:*)"], brief_version: 3, paused: false,
  paused_reason: null, retired: false, from_session: null, created_at: 1, updated_at: 1, state: "idle",
  limits: { max_run_seconds: 3600, max_runs_per_day: 24, max_steps: 200, queue_max: 10, storage_mb: 200 },
  totals: { runs: 0, done: 0, failed: 0, blocked: 0, seconds: 0, tokens: 0, cost_est_usd: 0, last_run_at: null,
            last_state: null, runs_today: 0, est_usd_today: 0, success_rate: null },
  runs: [], mcp: { connectors: false, servers: [], accepted: {}, held: [] }, tools: {}, proposals: [],
  unattended_mode: "dontAsk", precheck: null, max_budget_usd: null, est_usd_per_day: null, triggers: [], autocompact_window: null, max_compactions: 2, may_dispatch: false, accepts_dispatch: false, max_dispatch_per_run: 5, ...over,
});

const serve = (calls: { method: string; url: string; body?: Record<string, unknown> }[] = []) => {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method: init.method || "GET", url: String(url), body });
    if (String(url).includes("/api/ade/agents/draft")) {
      return new Response(JSON.stringify({ name: "Release notes", brief: "Write the notes.", repo: "notes", base_ref: "pp/bbbbbbbbbbbb",
                                           from_session: "bbbbbbbbbbbb", chat_settings: { model: "opus" },
                                           candidates: ["Bash(git log:*)", "Read"] }), { status: 200 });
    }
    return new Response(JSON.stringify(agent({ ...(body ?? {}) })), { status: 200 });
  }));
  return calls;
};

describe("an agent's form", () => {
  it("keeps a chat as an agent, filled in from the chat, with what was allowed there to tick", async () => {
    const calls = serve();
    const saved = vi.fn();
    render(<AgentForm mode={{ mode: "keep", session: "bbbbbbbbbbbb" }} onClose={() => {}} onSaved={saved} />);
    const form = await screen.findByRole("dialog", { name: "Keep as an agent" });
    expect(await within(form).findByDisplayValue("Release notes")).toBeTruthy();
    expect(within(form).getByDisplayValue("Write the notes.")).toBeTruthy();
    expect(within(form).getByRole("button", { name: /Draft the brief from this chat/ })).toBeTruthy();
    fireEvent.click(within(form).getByRole("checkbox", { name: "Bash(git log:*)" }));
    fireEvent.click(within(form).getByRole("button", { name: /Keep it/ }));
    await vi.waitFor(() => expect(saved).toHaveBeenCalled());
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.url).toMatch(/\/api\/ade\/agents$/);
    expect(post.body).toMatchObject({ name: "Release notes", repo: "notes", base_ref: "pp/bbbbbbbbbbbb", from_session: "bbbbbbbbbbbb",
                                      allow: ["Bash(git log:*)"], chat_settings: { model: "opus" } });
  });

  it("offers the repositories still there, and keeps it from saving without a name and a brief", async () => {
    serve();
    render(<AgentForm mode={{ mode: "new" }} onClose={() => {}} onSaved={() => {}} />);
    const form = screen.getByRole("dialog", { name: "New agent" });
    const repo = within(form).getByRole("combobox", { name: /Repository/ }) as HTMLSelectElement;
    expect(Array.from(repo.options).map((o) => o.value)).toEqual(["demo", "notes", ""]);
    const keep = within(form).getByRole("button", { name: /Keep it/ }) as HTMLButtonElement;
    expect(keep.disabled).toBe(true);
    fireEvent.change(within(form).getByPlaceholderText("Release notes"), { target: { value: "Notes" } });
    fireEvent.change(within(form).getByRole("textbox", { name: /Brief/ }), { target: { value: "Write them." } });
    expect(keep.disabled).toBe(false);
  });

  it("sets how a run nobody watches may act, its precheck and its budgets", async () => {
    const calls = serve();
    render(<AgentForm mode={{ mode: "edit", agent: agent() }} onClose={() => {}} onSaved={() => {}} />);
    const form = screen.getByRole("dialog", { name: "Edit Triage" });
    fireEvent.change(within(form).getByRole("combobox", { name: /When nobody watches it/ }), { target: { value: "auto" } });
    fireEvent.change(within(form).getByRole("textbox", { name: /Precheck/ }), { target: { value: "test -f TODO" } });
    fireEvent.change(within(form).getByRole("spinbutton", { name: /A run's budget/ }), { target: { value: "2.5" } });
    fireEvent.change(within(form).getByRole("spinbutton", { name: /A day's est. cost/ }), { target: { value: "0" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    expect(await within(form).findByText(/A day's estimated cost is a number of US dollars/)).toBeTruthy();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
    fireEvent.change(within(form).getByRole("spinbutton", { name: /A day's est. cost/ }), { target: { value: "" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ unattended_mode: "auto", precheck: "test -f TODO",
                                                                        max_budget_usd: 2.5, est_usd_per_day: null });
  });

  it("lets its runs start other agents' runs, so many, and take another run's message as an instruction", async () => {
    const calls = serve();
    render(<AgentForm mode={{ mode: "edit", agent: agent() }} onClose={() => {}} onSaved={() => {}} />);
    const form = screen.getByRole("dialog", { name: "Edit Triage" });
    const width = within(form).getByRole("spinbutton", { name: /Runs one run may start/ }) as HTMLInputElement;
    expect(width.disabled).toBe(true);
    fireEvent.click(within(form).getByRole("checkbox", { name: /Its runs may start other agents' runs/ }));
    expect(width.disabled).toBe(false);
    fireEvent.change(width, { target: { value: "40" } });
    expect(width.value).toBe("20");
    fireEvent.change(width, { target: { value: "3" } });
    fireEvent.click(within(form).getByRole("checkbox", { name: /Take another run's message as an instruction/ }));
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ may_dispatch: true, accepts_dispatch: true, max_dispatch_per_run: 3 });
  });

  it("sets when its memory files are archived, and the weekly re-verify run", async () => {
    const calls = serve();
    render(<AgentForm mode={{ mode: "edit", agent: agent() }} onClose={() => {}} onSaved={() => {}} />);
    const form = screen.getByRole("dialog", { name: "Edit Triage" });
    const decay = within(form).getByRole("spinbutton", { name: /A memory file unused for/ }) as HTMLInputElement;
    expect(decay.value).toBe("30");
    fireEvent.change(decay, { target: { value: "5000" } });
    expect(decay.value).toBe("1000");
    fireEvent.change(decay, { target: { value: "12" } });
    fireEvent.click(within(form).getByRole("checkbox", { name: /Re-verify what goes stale, weekly/ }));
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ decay_runs: 12, reverify: true });
  });

  it("turns the judge and the pause on, and writes its acceptance checks, refusing a fallback with no model", async () => {
    const calls = serve();
    render(<AgentForm mode={{ mode: "edit", agent: agent() }} onClose={() => {}} onSaved={() => {}} />);
    const form = screen.getByRole("dialog", { name: "Edit Triage" });
    fireEvent.click(within(form).getByRole("checkbox", { name: /A judge grades its runs/ }));
    fireEvent.click(within(form).getByRole("checkbox", { name: /Pause a run that goes wrong/ }));
    fireEvent.click(within(form).getByRole("checkbox", { name: /Let the loop improve it/ }));
    fireEvent.change(within(form).getByRole("textbox", { name: /Hold JSON with these keys/ }), { target: { value: "labels, risk_tier ," } });
    fireEvent.change(within(form).getByRole("combobox", { name: /Name a risk tier no higher than/ }), { target: { value: "medium" } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Values at paths, a line each" }),
                     { target: { value: 'status = "ok"\n\nissues.0.count = 3\nnote = plain words' } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Words it must not contain, a line each" }), { target: { value: "TODO\n \nFIXME" } });
    fireEvent.change(within(form).getByRole("combobox", { name: /When a check fails/ }), { target: { value: "fallback" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    expect(await within(form).findByText(/A fallback needs its model/)).toBeTruthy();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
    fireEvent.change(within(form).getByRole("textbox", { name: "The fallback model" }), { target: { value: "sonnet" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({
      judge_runs: true, anomaly_pause: true, improve: true,
      acceptance: { required_keys: ["labels", "risk_tier"], max_risk_tier: "medium", forbidden_substrings: ["TODO", "FIXME"],
                    json_path_equals: { status: "ok", "issues.0.count": 3, note: "plain words" }, on_fail: "fallback", fallback_model: "sonnet" },
    });
  });

  it("sends a fallback model only when a failure falls back", async () => {
    const calls = serve();
    render(<AgentForm mode={{ mode: "edit", agent: agent() }} onClose={() => {}} onSaved={() => {}} />);
    const form = screen.getByRole("dialog", { name: "Edit Triage" });
    fireEvent.change(within(form).getByRole("textbox", { name: /Hold JSON with these keys/ }), { target: { value: "labels" } });
    const onFail = within(form).getByRole("combobox", { name: /When a check fails/ });
    fireEvent.change(onFail, { target: { value: "fallback" } });
    fireEvent.change(within(form).getByRole("textbox", { name: "The fallback model" }), { target: { value: "sonnet" } });
    fireEvent.change(onFail, { target: { value: "hitl" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ acceptance: { on_fail: "hitl", fallback_model: null } });
  });

  it("shows an agent's checks as kept, sends none when they are cleared, and names a line that is no path", async () => {
    const calls = serve();
    const kept = { required_keys: ["labels"], json_path_equals: { status: "ok" }, max_risk_tier: null, forbidden_substrings: [],
                   on_fail: "hitl" as const, fallback_model: null };
    render(<AgentForm mode={{ mode: "edit", agent: agent({ acceptance: kept, judge_runs: true }) }} onClose={() => {}} onSaved={() => {}} />);
    const form = screen.getByRole("dialog", { name: "Edit Triage" });
    expect((within(form).getByRole("checkbox", { name: /A judge grades its runs/ }) as HTMLInputElement).checked).toBe(true);
    const paths = within(form).getByRole("textbox", { name: "Values at paths, a line each" }) as HTMLTextAreaElement;
    expect(paths.value).toBe('status = "ok"');
    expect((within(form).getByRole("combobox", { name: /When a check fails/ }) as HTMLSelectElement).value).toBe("hitl");
    fireEvent.change(paths, { target: { value: "just words" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    expect(await within(form).findByText('"just words" is not a path and its value, such as status = "ok".')).toBeTruthy();
    fireEvent.change(paths, { target: { value: "" } });
    fireEvent.change(within(form).getByRole("textbox", { name: /Hold JSON with these keys/ }), { target: { value: "" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ acceptance: null, judge_runs: true, anomaly_pause: false,
                                                                        improve: false });
  });

  it("refuses an allow rule that is not one, before sending anything", async () => {
    const calls = serve();
    render(<AgentForm mode={{ mode: "edit", agent: agent() }} onClose={() => {}} onSaved={() => {}} />);
    const form = screen.getByRole("dialog", { name: "Edit Triage" });
    expect((within(form).getByRole("combobox", { name: /Repository/ }) as HTMLSelectElement).disabled).toBe(true);
    fireEvent.change(within(form).getByRole("textbox", { name: /Brief/ }), { target: { value: "Label them all." } });
    expect(within(form).getByText(/version 4/)).toBeTruthy();
    fireEvent.change(within(form).getByRole("textbox", { name: /Allow rules/ }), { target: { value: "rm -rf /" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    expect(await within(form).findByText(/is not an allow rule/)).toBeTruthy();
    expect(calls.some((c) => c.method !== "GET")).toBe(false);
  });

  // the workspace's Claude Code accounts as the store keeps them (accounts.py): its own and the ones added; Spare
  // (bbbb) signed out, Later (cccc) with its sign-in not read yet, and the one new sessions start on as `active` says
  const NAMES: Record<string, string> = { default: "Default", aaaaaaaaaaaa: "Work", bbbbbbbbbbbb: "Spare", cccccccccccc: "Later" };
  const accounts = (ids: string[], active = "default") => ({
    claude: { active, accounts: ids.map((id) => ({ id, name: NAMES[id], created_at: 1, sessions: 0,
      status: id === "cccccccccccc" ? null : { installed: true, signed_in: id !== "bbbbbbbbbbbb" }, usage: null })) },
    codex: { active: "default", accounts: [] },
  });
  const NONE = "None: the account new sessions start on, at each run";

  it("offers the Claude Code account its runs use where the workspace has more than one, and sends the one picked", async () => {
    const calls = serve();
    useStore.setState({ accounts: accounts(["default"]) });
    const { unmount } = render(<AgentForm mode={{ mode: "new" }} onClose={() => {}} onSaved={() => {}} />);
    expect(screen.queryByRole("combobox", { name: /Claude Code account its runs use/ })).toBeNull();      // one account: nothing to pick
    unmount();
    useStore.setState({ accounts: accounts(["default", "aaaaaaaaaaaa"]) });                             // two: the first that is a choice
    const two = render(<AgentForm mode={{ mode: "new" }} onClose={() => {}} onSaved={() => {}} />);
    const pair = screen.getByRole("combobox", { name: /Claude Code account its runs use/ }) as HTMLSelectElement;
    expect(Array.from(pair.options).map((o) => o.textContent)).toEqual([`${NONE} (now Default)`, "Work"]);
    two.unmount();
    useStore.setState({ accounts: accounts(["default", "aaaaaaaaaaaa", "bbbbbbbbbbbb"]) });
    const saved = vi.fn();
    render(<AgentForm mode={{ mode: "new" }} onClose={() => {}} onSaved={saved} />);
    const form = screen.getByRole("dialog", { name: "New agent" });
    const picker = within(form).getByRole("combobox", { name: /Claude Code account its runs use/ }) as HTMLSelectElement;
    expect(Array.from(picker.options).map((o) => o.textContent)).toEqual([`${NONE} (now Default)`, "Work", "Spare (not signed in)"]);
    expect(picker.value).toBe("");
    fireEvent.change(picker, { target: { value: "aaaaaaaaaaaa" } });
    fireEvent.change(within(form).getByPlaceholderText("Release notes"), { target: { value: "Triage" } });
    fireEvent.change(within(form).getByRole("textbox", { name: /Brief/ }), { target: { value: "Label them." } });
    fireEvent.click(within(form).getByRole("button", { name: /Keep it/ }));
    await vi.waitFor(() => expect(saved).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ account: "aaaaaaaaaaaa" });
  });

  it("shows the account an agent is on as kept, one the workspace no longer lists by its id, and sends none when it goes back to the workspace's own", async () => {
    const calls = serve();
    useStore.setState({ accounts: accounts(["default", "aaaaaaaaaaaa"]) });
    const saved = vi.fn();
    const { unmount } = render(<AgentForm mode={{ mode: "edit", agent: agent({ account: "aaaaaaaaaaaa" }) }} onClose={() => {}} onSaved={saved} />);
    const form = screen.getByRole("dialog", { name: "Edit Triage" });
    const picker = within(form).getByRole("combobox", { name: /Claude Code account its runs use/ }) as HTMLSelectElement;
    expect(picker.value).toBe("aaaaaaaaaaaa");
    expect(Array.from(picker.options).map((o) => o.textContent)).toEqual([`${NONE} (now Default)`, "Work"]);    // Work once
    fireEvent.change(picker, { target: { value: "" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(saved).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ account: null });
    unmount();
    // on an account the workspace no longer lists: offered as itself, so that it can be moved, with one account only
    useStore.setState({ accounts: accounts(["default"]) });
    render(<AgentForm mode={{ mode: "edit", agent: agent({ account: "cccccccccccc" }) }} onClose={() => {}} onSaved={() => {}} />);
    const gone = within(screen.getByRole("dialog", { name: "Edit Triage" })).getByRole("combobox", { name: /Claude Code account its runs use/ }) as HTMLSelectElement;
    expect(gone.value).toBe("cccccccccccc");
    expect(Array.from(gone.options).map((o) => o.textContent)).toEqual([`${NONE} (now Default)`, "cccccccccccc: no longer in the workspace"]);
    cleanup();
    // before the workspace's accounts are read (it is stopped, or the read has not landed): the page does not know
    // whether the account is still there, and says so, not "no longer"
    useStore.setState({ accounts: null });
    render(<AgentForm mode={{ mode: "edit", agent: agent({ account: "aaaaaaaaaaaa" }) }} onClose={() => {}} onSaved={() => {}} />);
    const unknown = within(screen.getByRole("dialog", { name: "Edit Triage" })).getByRole("combobox", { name: /Claude Code account its runs use/ }) as HTMLSelectElement;
    expect(unknown.value).toBe("aaaaaaaaaaaa");
    expect(Array.from(unknown.options).map((o) => o.textContent)).toEqual([NONE, "aaaaaaaaaaaa: the workspace's accounts are not read yet"]);
  });

  it("says what none chosen means: each run on the account new sessions start on then, a task on the one they start on when it is made; and offers an account whose sign-in is not read yet as it is", async () => {
    serve();
    // new sessions start on Work; Later's sign-in is not read yet, which is not a sign-out
    useStore.setState({ accounts: accounts(["default", "aaaaaaaaaaaa", "cccccccccccc"], "aaaaaaaaaaaa") });
    render(<AgentForm mode={{ mode: "new" }} onClose={() => {}} onSaved={() => {}} />);
    const form = screen.getByRole("dialog", { name: "New agent" });
    const picker = within(form).getByRole("combobox", { name: /Claude Code account its runs use/ }) as HTMLSelectElement;
    expect(Array.from(picker.options).map((o) => o.textContent)).toEqual([`${NONE} (now Work)`, "Work", "Later"]);
    expect(within(form).getByText(/With none, each run starts on the account new sessions start on when it starts, and a task of its begins on the one they start on when the task is made, which its New task form names\./)).toBeTruthy();
    expect(within(form).queryByText(/Default: the workspace's own sign-in/)).toBeNull();
  });
});
