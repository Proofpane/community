import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../store";
import { day, dayTime } from "../when";
import { AccountDialog } from "./AccountDialog";

// Where the account's work lives, in the account's
// dialog, shown as the server answers GET /api/ade/storage: each test opens the dialog at its own section, so where it
// is not built the first expectation is the one that fails.

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useStore.setState(useStore.getInitialState()); });

const REPOSITORY = "Your code and the ADE's signed answers, windows and stops live in your own repository on GitHub.";
const MACHINE = "Your code and files stay where your linked sessions run.";
const HOSTED = "Your code, your files and your programs' sign-ins live on a machine this deployment runs for you; its operator can read them.";
const NOT_FOR_MEMBERS = "On this deployment a workspace is for admins only, for now.";
const LEFT_MACHINE = "Your 2 linked sessions stay linked and go on sending what they send.";

type View = Record<string, unknown>;
const view = (over: View = {}): View => ({
  choice: "machine", chosen: false, refusal: "This account keeps its work on its own machine.",
  choices: [
    { choice: "repository", label: "In your repository", sentence: REPOSITORY, offered: true, why: null },
    { choice: "machine", label: "On your own machine", sentence: MACHINE, offered: true, why: null },
    { choice: "hosted", label: "In a workspace this deployment runs for you", sentence: HOSTED, offered: false, why: NOT_FOR_MEMBERS },
  ],
  left_behind: { repository: [LEFT_MACHINE], machine: [], hosted: [LEFT_MACHINE] },
  e2e: { on: false, not_now: false, ask: false },
  ...over,
});

type Call = { path: string; method: string; body: Record<string, unknown> | undefined };
const serve = (answer: (call: Call) => [number, unknown] | null) => {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const call = { path: String(url), method: init.method || "GET", body: init.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    const [status, data] = answer(call) ?? [404, { detail: `no route for ${call.method} ${call.path}` }];
    return new Response(JSON.stringify(data), { status });
  }));
  return calls;
};
const member = () => useStore.setState({ user: { id: "u", email: "m@example.org", can_sign_in_again: true } });
const puts = (calls: Call[]) => calls.filter((c) => c.method === "PUT" && c.path === "/api/ade/storage").map((c) => c.body);
const shown = (text: string) => vi.waitFor(() => expect(screen.queryByText(text)).toBeTruthy());

describe("where your work lives, in the account", () => {
  it("is a section of the account's dialog", async () => {
    serve((c) => (c.path === "/api/ade/storage" ? [200, view()] : null));
    member();
    render(<AccountDialog onClose={() => {}} />);
    expect(screen.queryByRole("button", { name: /Where your work lives/ })).toBeTruthy();
  });

  it("shows the three choices with their sentences, the account's own marked as its default until it chooses", async () => {
    serve((c) => (c.path === "/api/ade/storage" ? [200, view()] : null));
    member();
    render(<AccountDialog tab={"storage" as never} onClose={() => {}} />);
    await shown(REPOSITORY);
    expect(screen.queryByText(MACHINE)).toBeTruthy();
    expect(screen.queryByText(HOSTED)).toBeTruthy();
    const machine = screen.getByRole("radio", { name: /On your own machine/ }) as HTMLInputElement;
    expect(machine.checked).toBe(true);
    expect(screen.queryByText(/the default for an account that has not chosen/)).toBeTruthy();
  });

  it("shows a choice not offered here, unavailable, saying why", async () => {
    serve((c) => (c.path === "/api/ade/storage" ? [200, view()] : null));
    member();
    render(<AccountDialog tab={"storage" as never} onClose={() => {}} />);
    await shown(NOT_FOR_MEMBERS);
    expect((screen.getByRole("radio", { name: /In a workspace this deployment runs for you/ }) as HTMLInputElement).disabled).toBe(true);
  });

  it("names what a move leaves before the person confirms it, and keeps the choice only once confirmed", async () => {
    const calls = serve((c) => (c.path !== "/api/ade/storage" ? null
      : c.method === "PUT" ? [200, view({ choice: "repository", chosen: true })] : [200, view({ chosen: true })]));
    member();
    render(<AccountDialog tab={"storage" as never} onClose={() => {}} />);
    await shown(REPOSITORY);
    fireEvent.click(screen.getByRole("radio", { name: /In your repository/ }));
    await shown(LEFT_MACHINE);
    expect(puts(calls)).toEqual([]);                                  // nothing kept before it is confirmed
    fireEvent.click(screen.getByRole("button", { name: /Keep my work in my repository/ }));
    await vi.waitFor(() => expect(puts(calls)).toEqual([{ choice: "repository" }]));
    await vi.waitFor(() => expect((screen.getByRole("radio", { name: /In your repository/ }) as HTMLInputElement).checked).toBe(true));
  });

  it("asks a member who chose its own machine to turn end-to-end encryption on, and keeps its not now", async () => {
    let asked = true;
    const calls = serve((c) => {
      if (c.path !== "/api/ade/storage") return null;
      if (c.method === "PUT" && c.body?.e2e_not_now) asked = false;
      return [200, view({ choice: "machine", chosen: true, e2e: { on: false, not_now: !asked, ask: asked } })];
    });
    member();
    render(<AccountDialog tab={"storage" as never} onClose={() => {}} />);
    await shown("Turn on end-to-end encryption?");
    expect(screen.queryByText(/sealed words are lost for good/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    await vi.waitFor(() => expect(puts(calls)).toEqual([{ e2e_not_now: true }]));
    await vi.waitFor(() => expect(screen.queryByText("Turn on end-to-end encryption?")).toBeNull());
  });

  it("takes the person to the Encryption section from the question, where turning it on stays their own act", async () => {
    serve((c) => (c.path === "/api/ade/storage"
      ? [200, view({ choice: "machine", chosen: true, e2e: { on: false, not_now: false, ask: true } })] : null));
    member();
    render(<AccountDialog tab={"storage" as never} onClose={() => {}} />);
    await shown("Turn on end-to-end encryption?");
    fireEvent.click(screen.getByRole("button", { name: /Turn it on/ }));
    await vi.waitFor(() => expect(screen.getByRole("button", { name: /^Encryption$/ }).getAttribute("aria-current")).toBe("page"));
  });

  it("says what end-to-end encryption seals, each thing it leaves readable, and who serves the page that opens it", async () => {
    serve((c) => (c.path === "/api/ade/storage"
      ? [200, view({ choice: "machine", chosen: true, e2e: { on: false, not_now: false, ask: true } })] : null));
    member();
    render(<AccountDialog tab={"storage" as never} onClose={() => {}} />);
    await shown("Turn on end-to-end encryption?");
    const question = screen.getByText("Turn on end-to-end encryption?").closest("section")?.textContent ?? "";
    for (const words of [
      // sealed: an event's text to the owner, four of a History row's fields, a conversation's messages
      "their messages to you", "titles, projects, branches and repositories", "their conversations' messages",
      // left readable
      "another session or all of them", "kind, who it is to, what it answers and when",
      "a mirrored note's session and mirror word", "that sealing in place leaves", "\"working on it\"",
      "where it ran, whether it is live, its start and last times", "its machine's id and its pins",
      "who said each message of a conversation, and when", "your own instructions and answers", "checkpoints",
      "your links' names and whether they are present", "the commands held for your approval, with their folders and sessions",
      "a moved session's title", "its repository's address, branch, head and folder", "its failure reason",
      "its transcript and the git bundle of its work",
      // who serves the page that opens them, and what older snapshots keep
      "served by this deployment", "older snapshots",
    ]) expect(question, words).toContain(words);
    expect(question).not.toContain("does not seal the rest");          // a list of some is not said as all
  });

  it("marks a workspace kept from before any choice as where the work is, not as the default", async () => {
    const choices = (view().choices as { choice: string }[])
      .map((c) => (c.choice === "hosted" ? { ...c, offered: true, why: null } : c));
    serve((c) => (c.path === "/api/ade/storage" ? [200, view({ choice: "hosted", chosen: false, refusal: null, choices })] : null));
    member();
    render(<AccountDialog tab={"storage" as never} onClose={() => {}} />);
    await shown(HOSTED);
    expect((screen.getByRole("radio", { name: /In a workspace this deployment runs for you/ }) as HTMLInputElement).checked).toBe(true);
    expect(screen.queryByText(/not chosen: you have a workspace here/)).toBeTruthy();
    expect(screen.queryByText(/the default for an account that has not chosen/)).toBeNull();
  });

  it("asks nothing where the server says not to ask", async () => {
    serve((c) => (c.path === "/api/ade/storage" ? [200, view({ chosen: true })] : null));
    member();
    render(<AccountDialog tab={"storage" as never} onClose={() => {}} />);
    await shown(MACHINE);
    expect(screen.queryByText("Turn on end-to-end encryption?")).toBeNull();
  });
});

// A hosted workspace's share of the cap, and what leaving the hosted choice keeps and for how long, as
// GET /api/ade/storage gives them in its `workspace`: the page shows the
// account its month's spending against its share, the date a workspace left behind is kept until, and deletes it at
// once only with a confirmation made for that act. A server from before them sends no `workspace`.
const KEPT_UNTIL = 1795942800;                 // 2026-11-29 09:00 UTC, 30 days after the account left the choice
const REMOVED_AT = 1793350800;                 // 2026-10-30 09:00 UTC
const SWEPT = "It was kept 30 days after you left the hosted choice, then its machine and its volume were destroyed.";
const DELETED_NOW = "You deleted it.";
const confirmations = (calls: Call[]) => calls.filter((c) => c.path === "/api/account/confirm" && c.method === "POST").map((c) => c.body);
const deletes = (calls: Call[]) => calls.filter((c) => c.method === "DELETE" && c.path === "/api/ade/storage/workspace").map((c) => c.body);

describe("a hosted workspace's share, and what leaving the hosted choice keeps", () => {
  it("shows the account its month's spending against its share", async () => {
    serve((c) => (c.path === "/api/ade/storage" ? [200, view({ choice: "hosted", chosen: true, refusal: null,
      workspace: { share: { month: "2026-10", spent_usd: 2.1, share_usd: 5 }, kept_until: null, keep_days: 30, removed: null } })] : null));
    member();
    render(<AccountDialog tab={"storage" as never} onClose={() => {}} />);
    await shown(HOSTED);
    await vi.waitFor(() => expect(screen.queryByText(/\$2\.10 of your \$5\.00 share/)).toBeTruthy());
  });

  it("gives the date a workspace left behind is kept until, and what deleting it at once leaves", async () => {
    serve((c) => (c.path === "/api/ade/storage" ? [200, view({ chosen: true,
      workspace: { share: null, kept_until: KEPT_UNTIL, keep_days: 30, removed: null } })] : null));
    member();
    render(<AccountDialog tab={"storage" as never} onClose={() => {}} />);
    await shown(MACHINE);
    await vi.waitFor(() => expect(document.body.textContent).toContain(dayTime(KEPT_UNTIL)));      // the day and its time
    const words = document.body.textContent ?? "";
    expect(words).toContain("30 days");
    expect(words).toContain("older snapshots");                     // the app volume's, until Fly drops them
    expect(words).toContain("sign-ins");                            // the programs' sign-ins on the volume go with it
    expect(words).toContain("your agents, tasks, Manager and finishes stays");    // the role's name
    expect(words).not.toMatch(/controller/i);
  });

  it("does not say a change of where the work lives deletes nothing", async () => {
    serve((c) => (c.path === "/api/ade/storage" ? [200, view({ choice: "hosted", chosen: true, refusal: null,
      workspace: { share: { month: "2026-10", spent_usd: 2.1, share_usd: 5 }, kept_until: null, keep_days: 30, removed: null } })] : null));
    member();
    render(<AccountDialog tab={"storage" as never} onClose={() => {}} />);
    await shown(HOSTED);
    expect(document.body.textContent).not.toContain("deletes nothing");      // leaving a workspace ends in its deletion
  });

  it("deletes a workspace left behind at once only with a confirmation made for that act", async () => {
    const calls = serve((c) => {
      if (c.path === "/api/account/confirm") return c.method === "GET" ? [200, { needed: true, password: true, passkeys: 0 }] : [200, { confirmation: "conf-1" }];
      if (c.path === "/api/ade/storage/workspace" && c.method === "DELETE") return [200, view({ chosen: true,
        workspace: { share: null, kept_until: null, keep_days: 30, removed: { removed_at: REMOVED_AT, why: DELETED_NOW } } })];
      if (c.path === "/api/ade/storage") return [200, view({ chosen: true,
        workspace: { share: null, kept_until: KEPT_UNTIL, keep_days: 30, removed: null } })];
      return null;
    });
    member();
    render(<AccountDialog tab={"storage" as never} onClose={() => {}} />);
    await vi.waitFor(() => expect(screen.queryByRole("button", { name: /Delete the workspace now/ })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /Delete the workspace now/ }));
    fireEvent.change(await screen.findByLabelText("Your password"), { target: { value: "pw" } });
    expect(deletes(calls)).toEqual([]);                              // nothing deleted before it is confirmed
    fireEvent.click(screen.getByRole("button", { name: /^Confirm$/ }));
    await vi.waitFor(() => expect(deletes(calls)).toEqual([{ confirmation: "conf-1" }]));
    expect(confirmations(calls)).toEqual([{ password: "pw", change: "workspace-delete" }]);
    await shown(DELETED_NOW);
  });

  it("says when and why a workspace left behind was deleted", async () => {
    serve((c) => (c.path === "/api/ade/storage" ? [200, view({ chosen: true,
      workspace: { share: null, kept_until: null, keep_days: 30, removed: { removed_at: REMOVED_AT, why: SWEPT } } })] : null));
    member();
    render(<AccountDialog tab={"storage" as never} onClose={() => {}} />);
    await shown(SWEPT);
    expect(document.body.textContent).toContain(day(REMOVED_AT));
    expect(screen.queryByRole("button", { name: /Delete the workspace now/ })).toBeNull();
    expect(document.body.textContent).toContain("your agents, tasks, Manager and finishes stays");
    expect(document.body.textContent).not.toMatch(/controller/i);
  });
});
