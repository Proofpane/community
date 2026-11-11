import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountOwnerView, AccountRow, RiskTextView, SessionRecord } from "../api";
import { ONE_CHECK, WHAT_IT_DOES } from "../handover";
import { useStore } from "../store";
import { ContinueSession } from "./ContinueSession";
import { forgetPage } from "./RiskTerms";

// Continue in a new session: the dialog the owner's press opens. Its words, the account a program starts on,
// risk point 17's text with the parts the choices bring, and the one request it makes on the owner's press, with the
// task as the owner left it. The texts' words here are this test's own, not the design's.

const SID = "0123456789ab";
const TASK = "Add retries\n\nKeep the change small.";
const row = (id: string, name: string): AccountRow => ({ id, name, created_at: 1, sessions: 0, status: null, usage: null });
const owner = (account: string, grp: string | null, grp_kind: AccountOwnerView["grp_kind"]) =>
  ({ id: `o-${account}`, account, name: null, report: null, org_id: null, mismatch: null, state: "confirmed", grp, grp_kind,
     position: 0, in_relays: true, reserve_lifted: false, ready: true, windows: [], hold: null }) as AccountOwnerView;
const point17: RiskTextView = {
  point: 17, title: "Continuing a session in a new one",
  design: [
    { case: null, text: "The new session starts from this session's commit, with none of its conversation." },
    { case: "other_account", text: "Using several of your accounts in turn is your choice." },
    { case: "other_group", text: "Work moves between an organisation and a personal plan." },
    { case: "onto_seat", text: "The organisation's own settings apply on its seats." },
    { case: "codex", text: "OpenAI's terms were not read for this." },
  ],
  quotations: [], unread: false, digest: "e".repeat(64),
};
const TERMS = "Terms: Continuing a session in a new one";

let calls: { method: string; url: string; body: unknown }[];
let kept: string | null;
let groups: AccountOwnerView[];
let answer: () => Response;

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

beforeEach(() => {
  calls = [];
  kept = TASK;
  groups = [owner("default", "personal", "personal"), owner("bbbbbbbbbbbb", "org:acme", "team")];
  answer = () => json({ finish: { head: "f".repeat(40) }, session: { id: "ba9876543210" },
                        handover: { id: "h", from_session: SID, to_session: "ba9876543210", head: "f".repeat(40),
                                    program: "codex", account: "cccccccccccc", finish_id: null, commits: 2, files: 1, at: 1 } });
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    calls.push({ method, url: String(url), body: init.body ? JSON.parse(String(init.body)) : undefined });
    if (url === "/api/ade/risks") return json({ texts: [point17] });
    if (url === `/api/ade/workspace/sessions/${SID}/handover`) return json({ task: kept, continued_from: null, continued_in: [] });
    if (url === "/api/ade/agents/accounts") return json({ accounts: groups });
    if (url === `/api/ade/workspace/sessions/${SID}/continue`) return answer();
    return json({ detail: "not here" }, 404);
  }));
  useStore.setState({
    user: { id: "u1", email: "owner@example.org" } as never,
    accounts: { claude: { active: "default", accounts: [row("default", "Default"), row("bbbbbbbbbbbb", "Work seat")] },
                codex: { active: "cccccccccccc", accounts: [row("default", "Default"), row("cccccccccccc", "ChatGPT plan")] } },
    refresh: async () => {},
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  forgetPage();
  localStorage.clear();
  useStore.setState(useStore.getInitialState());
});

const rec = (over: Partial<SessionRecord> = {}) =>
  ({ id: SID, program: "claude", repo: "demo", branch: `pp/${SID}`, base: "abc", task: "Something else", created_at: 1,
     running: true, finished: null, exit_code: null, ended_at: null, chat: null, account: null, ...over }) as SessionRecord;
const settled = () => act(() => new Promise<void>((done) => setTimeout(done, 0)));
const terms = () => screen.queryByRole("dialog", { name: TERMS });
const parts = () => {
  const box = terms();
  return box ? point17.design.filter((p) => within(box).queryByText(p.text)).map((p) => p.case) : null;
};

describe("Continue in a new session's dialog", () => {
  it("says what the press does and its one check, holds the task as the owner gave the session, and opens point 17", async () => {
    render(<ContinueSession rec={rec()} onClose={() => {}} />);
    expect(screen.getByText(WHAT_IT_DOES)).toBeTruthy();
    expect(screen.getByText(ONE_CHECK)).toBeTruthy();
    const task = screen.getByLabelText("Task") as HTMLTextAreaElement;
    await vi.waitFor(() => expect(task.value).toBe(TASK));                  // the app's copy, not the session's record
    await settled();
    expect(parts()).toEqual([null]);                                        // the same program and account: its own words
    expect(screen.getByText("Your task as you gave this session, to change as you like.")).toBeTruthy();
    expect(calls.filter((c) => c.method !== "GET")).toEqual([]);            // opening it does nothing
  });

  it("brings the parts each choice makes apply: another account in another group onto a seat, then Codex", async () => {
    render(<ContinueSession rec={rec()} onClose={() => {}} />);
    await settled();
    await vi.waitFor(() => expect(calls.some((c) => c.url === "/api/ade/agents/accounts")).toBe(true));
    await settled();
    fireEvent.change(screen.getByLabelText("Account"), { target: { value: "bbbbbbbbbbbb" } });
    await settled();
    expect(parts()).toEqual([null, "other_account", "other_group", "onto_seat"]);
    fireEvent.click(screen.getByRole("radio", { name: "Chat" }));            // a chat runs Claude Code: the choice stays
    await settled();
    expect((screen.getByLabelText("Account") as HTMLSelectElement).value).toBe("bbbbbbbbbbbb");
    expect(parts()).toEqual([null, "other_account", "other_group", "onto_seat"]);
    fireEvent.click(screen.getByRole("radio", { name: "Codex" }));
    await settled();
    expect((screen.getByLabelText("Account") as HTMLSelectElement).value).toBe("cccccccccccc");   // Codex's own active
    expect(parts()).toEqual([null, "codex"]);
    fireEvent.click(screen.getByRole("radio", { name: "Chat" }));            // back to Claude Code's accounts
    await settled();
    expect((screen.getByLabelText("Account") as HTMLSelectElement).value).toBe("default");
    expect(parts()).toEqual([null]);
  });

  it("on the press, sends the program, the account and the task as the owner left it, then opens the new session", async () => {
    const onClose = vi.fn();
    render(<ContinueSession rec={rec()} onClose={onClose} />);
    const task = screen.getByLabelText("Task") as HTMLTextAreaElement;
    await vi.waitFor(() => expect(task.value).toBe(TASK));
    fireEvent.click(screen.getByRole("radio", { name: "Codex" }));
    fireEvent.change(task, { target: { value: "  Add retries, then the tests\n" } });
    fireEvent.click(screen.getByRole("button", { name: /Finish and continue/ }));
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", url: `/api/ade/workspace/sessions/${SID}/continue`,
      body: { program: "codex", account: "cccccccccccc", task: "Add retries, then the tests", cols: 120, rows: 32 } }]);
    expect(useStore.getState().selected).toBe("ba9876543210");
    expect(useStore.getState().notice?.text).toBe(
      "Finished and continued in a new session from ffffffffffff: its first prompt names 2 commits and 1 file.");
  });

  it("sends the account the owner chose, not the one the program starts on", async () => {
    const onClose = vi.fn();
    render(<ContinueSession rec={rec()} onClose={onClose} />);
    await vi.waitFor(() => expect((screen.getByLabelText("Task") as HTMLTextAreaElement).value).toBe(TASK));
    fireEvent.change(screen.getByLabelText("Account"), { target: { value: "bbbbbbbbbbbb" } });
    fireEvent.click(screen.getByRole("button", { name: /Finish and continue/ }));
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(calls.filter((c) => c.method === "POST").map((c) => (c.body as { account: string }).account))
      .toEqual(["bbbbbbbbbbbb"]);
  });

  it("asks for a task where none was kept, and keeps what the owner typed before the kept one came back", async () => {
    kept = null;
    render(<ContinueSession rec={rec()} onClose={() => {}} />);
    await settled();
    expect(screen.getByText(/This session's task was not kept/)).toBeTruthy();
    const press = screen.getByRole("button", { name: /Finish and continue/ }) as HTMLButtonElement;
    expect(press.disabled).toBe(true);                                      // nothing to start it with
    fireEvent.change(screen.getByLabelText("Task"), { target: { value: "  " } });
    expect(press.disabled).toBe(true);
    cleanup();
    kept = TASK;
    render(<ContinueSession rec={rec()} onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText("Task"), { target: { value: "Mine" } });
    await settled();
    expect((screen.getByLabelText("Task") as HTMLTextAreaElement).value).toBe("Mine");
  });

  it("says the app's refusal and stays open, starting nothing more", async () => {
    answer = () => json({ detail: "The task holds something shaped like an Anthropic API key." }, 400);
    const onClose = vi.fn();
    render(<ContinueSession rec={rec()} onClose={onClose} />);
    await vi.waitFor(() => expect((screen.getByLabelText("Task") as HTMLTextAreaElement).value).toBe(TASK));
    fireEvent.click(screen.getByRole("button", { name: /Finish and continue/ }));
    expect(await screen.findByText("The task holds something shaped like an Anthropic API key.")).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
  });
});
