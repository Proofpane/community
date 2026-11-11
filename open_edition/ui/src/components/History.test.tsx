import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { HistorySession, LinkEvent, LinkView } from "../api";
import { useStore } from "../store";
import { dayTime } from "../when";
import { byRepository, liveWord, LIVE_FRESH, MachineSessions, registeredNow, resumeCommand } from "./History";
import { Channel, machines, SessionConversation, sessionSaid } from "./Channel";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useStore.setState(useStore.getInitialState()); });

const serve = (routes: Record<string, unknown>) =>
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = String(url);
    if (u in routes) return new Response(JSON.stringify(routes[u]), { status: 200 });
    return new Response(JSON.stringify({ detail: "no route" }), { status: 404 });
  }));

const session = (over: Partial<HistorySession>): HistorySession => ({
  id: "s1", title: "a session", project: "~/work/app", branch: "main", started_at: 1_790_000_000, last_at: 1_790_003_600,
  link_id: "L1", link: "mac", kind: "local", listed_at: 1_790_003_700, ...over,
});

const linkView = (over: Partial<LinkView>): LinkView => ({
  id: "L1", name: "mac", kind: "local", session_ref: null, interval_min: 15, created_at: 1_790_000_000,
  last_seen_at: null, last_heartbeat_at: null, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0,
  checkpoint_at: null, events: 0, last_event: null, ...over,
});

const note = (seq: number, mirror: "owner" | "session" | "info", text: string, said_at?: number): LinkEvent => ({
  seq, time: "2026-10-05T01:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text, mirror,
  ...(said_at ? { session: "s1", said_at } : {}),
});

describe("a machine's sessions", () => {
  it("lists them by repository, each marked local or cloud with the machine's linked session", () => {
    render(<MachineSessions sessions={[
      session({ id: "a", title: "newest, in app", repositories: ["~/work/app", "~/work/lib"] }),
      session({ id: "b", title: "in lib", repositories: ["~/work/lib"], project: "~/work/lib" }),
      session({ id: "c", title: "older, in app", repositories: ["~/work/app"] })]} />);
    const groups = screen.getAllByTitle("The repository its sessions worked in first").map((g) => g.textContent?.trim());
    expect(groups).toEqual(["~/work/app", "~/work/lib"]);                 // a group as new as its newest session
    expect(screen.getByText("also in ~/work/lib")).toBeTruthy();           // a session that spans repositories
    expect(screen.getAllByText(/Local · mac/)).toHaveLength(3);
    expect(screen.getAllByText(dayTime(1_790_003_600))).toHaveLength(3);  // in the reader's own zone
  });

  it("groups a session no repository is named for under its folder", () => {
    expect(byRepository([session({ id: "a" }), session({ id: "b", project: null })]).map((g) => [g.repo, g.sessions.length]))
      .toEqual([["~/work/app", 1], ["", 1]]);
  });

  it("opens a row to the command that opens its session again on its machine, and copies it", () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<MachineSessions sessions={[session({ id: "abc-123", title: "fix the sign-in", project: "~/my work/it's" })]} />);
    fireEvent.click(screen.getByText("fix the sign-in"));
    const command = "cd ~/'my work/it'\\''s' && claude --resume 'abc-123'";
    expect(screen.getByText(command)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Copy the command" }));
    expect(writeText).toHaveBeenCalledWith(command);
  });

  it("marks where each ran, whether it is running and whether it is registered, while its machine's word is fresh", () => {
    const now = Date.now() / 1000;
    render(<MachineSessions sessions={[
      session({ id: "a", title: "in VS Code", client: "vscode", live: "working", registered: true, listed_at: now - 60 }),
      session({ id: "b", title: "in a terminal", client: "terminal", live: "open", listed_at: now - 60 }),
      session({ id: "c", title: "said long ago", client: "terminal", live: "working", registered: true, listed_at: now - LIVE_FRESH - 60 })]} />);
    expect(screen.getByText("VS Code")).toBeTruthy();
    expect(screen.getAllByText("Terminal")).toHaveLength(2);
    expect(screen.getByText("working")).toBeTruthy();                      // c's words are too old to say
    expect(screen.getByText("open")).toBeTruthy();
    expect(screen.getAllByText("registered")).toHaveLength(1);
    expect(liveWord({ live: "working", listed_at: now - LIVE_FRESH - 1 }, now)).toBeNull();
    expect(registeredNow({ registered: true, listed_at: now - LIVE_FRESH - 1 }, now)).toBe(false);
    expect(registeredNow({ registered: false, listed_at: now }, now)).toBe(false);
  });

  it("keeps a withheld session's title on its machine, and says why", () => {
    render(<MachineSessions sessions={[session({ id: "w", title: null, branch: null, withheld: true })]} onRead={vi.fn()} />);
    fireEvent.click(screen.getByText("Title kept on mac's machine"));
    expect(screen.getByText(/touched a project mac's history.json excepts/)).toBeTruthy();
    expect(screen.queryByText("Read the conversation")).toBeNull();
  });

  it("offers a session's conversation where its machine sent it or its turns were mirrored under it", () => {
    const onRead = vi.fn();
    const sent = session({ id: "t", title: "with words", talk_at: 1_790_003_650 });
    const mirrored = session({ id: "m", title: "mirrored", notes: 2 });
    render(<MachineSessions sessions={[sent, mirrored, session({ id: "n", title: "without" })]} onRead={onRead} />);
    for (const [title, s] of [["with words", sent], ["mirrored", mirrored]] as const) {
      fireEvent.click(screen.getByText(title));
      fireEvent.click(screen.getByText("Read the conversation"));
      expect(onRead).toHaveBeenLastCalledWith(s);
      fireEvent.click(screen.getByText(title));
    }
    fireEvent.click(screen.getByText("without"));
    expect(screen.getByText(/Its conversation is not sent here: to send it, add "conversations": true to ~\/\.proofpane-ade\/mac\/history\.json on mac's machine\./)).toBeTruthy();
  });

  it("says how a machine comes to list its sessions while it lists none", () => {
    render(<MachineSessions sessions={[]} />);
    expect(screen.getByText(/once its link's folder holds a history.json naming the projects to list/)).toBeTruthy();
  });

  it("quotes a folder and a session's id for the shell, whatever they hold", () => {
    expect(resumeCommand({ id: "x", project: "/srv/a b" })).toBe("cd '/srv/a b' && claude --resume 'x'");
    expect(resumeCommand({ id: "x", project: "~" })).toBe("cd ~ && claude --resume 'x'");
    expect(resumeCommand({ id: "x", project: null })).toBe("claude --resume 'x'");
  });
});

describe("the machines", () => {
  it("puts a machine's links together by its id, and shows a session two of them list as the one that listed it last", () => {
    const links = [linkView({ id: "L1", name: "mac", machine: "m1" }), linkView({ id: "L2", name: "cloud", kind: "cloud" }),
      linkView({ id: "L3", name: "mac-two", machine: "m1" })];
    // a link no longer run there keeps what it last listed: registered, with notes, but older
    const got = machines(links, [session({ id: "s", link_id: "L1", listed_at: 100, registered: true, notes: 3 }),
      session({ id: "s", link_id: "L3", listed_at: 200 }), session({ id: "t", link_id: "L1", listed_at: 300 }),
      session({ id: "t", link_id: "L3", listed_at: 200, registered: true }), session({ id: "x", link_id: "gone" })]);
    expect(got.map((g) => [g.key, g.links.map((l) => l.name), g.sessions.map((s) => `${s.id}@${s.link_id}`)])).toEqual([
      ["machine:m1", ["mac", "mac-two"], ["s@L3", "t@L1"]], ["link:L2", ["cloud"], []]]);
  });

  it("lists each machine with its links' cards and its sessions under them, in one list", async () => {
    serve({
      "/api/ade/link/sessions": { links: [linkView({ id: "L1", name: "mac", machine: "m1" })], questions: [], public_url: "https://ade.example" },
      "/api/ade/link/history": { sessions: [session({ id: "s1", title: "the one list", repositories: ["~/work/app"], notes: 2 })] },
    });
    useStore.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true } });   // a signed-in owner
    render(<Channel onClose={vi.fn()} />);
    const machine = await screen.findByRole("region", { name: "A machine: mac" });
    expect(await within(machine).findByText("the one list")).toBeTruthy();
    expect(within(machine).getByText(/its own too once it is registered, are under each session below/)).toBeTruthy();
    expect(screen.queryByRole("tab")).toBeNull();                              // no tabs: one list
  });
});

describe("a session's conversation", () => {
  it("is what its machine sent of it before its first mirrored note, then its notes, no turn twice", () => {
    const talk = { earlier: 0, updated_at: 1_790_003_650, messages: [
      { who: "owner" as const, text: "before", at: 1_790_003_000, ms: 1_790_003_000_100 },
      { who: "session" as const, text: "a reply before", at: 1_790_003_001, ms: 1_790_003_000_900 },
      { who: "owner" as const, text: "mirrored too", at: 1_790_003_060, ms: 1_790_003_060_000 }] };
    const got = sessionSaid(talk, [note(9, "owner", "mirrored too", 1_790_003_060_000), note(10, "session", "and after", 1_790_003_061_000)], "Terminal");
    expect(got.map((s) => [s.who, s.text, s.head])).toEqual([["you", "before", "You, in Terminal"], ["it", "a reply before", "Claude"],
      ["you", "mirrored too", "You, in Terminal"], ["it", "and after", "Claude"]]);
    expect(sessionSaid(talk, [], "").map((s) => s.text)).toEqual(["before", "a reply before", "mirrored too"]);
    // a message whose time is not known goes only where no note says when it was said
    const untimed = { ...talk, messages: [{ who: "owner" as const, text: "when?", at: null }] };
    expect(sessionSaid(untimed, [note(9, "owner", "x", 1)], "").map((s) => s.text)).toEqual(["x"]);
    expect(sessionSaid(untimed, [], "").map((s) => s.text)).toEqual(["when?"]);
  });

  it("shows what its machine sent and its mirrored turns, and the command that opens it there", async () => {
    const s = session({ id: "t", title: "with words", client: "terminal", talk_at: 1_790_003_650, notes: 1 });
    serve({
      "/api/ade/link/history/L1/t": { earlier: 3, updated_at: 1_790_003_650, messages: [
        { who: "owner", text: "how is the build?", at: 1_790_003_000 }, { who: "session", text: "**Green**: 12 passed.", at: 1_790_003_100 }] },
      "/api/ade/link/timeline/L1?limit=100&session=t": { events: [note(20, "session", "Mirrored since.", 1_790_004_000_000)] },
    });
    render(<SessionConversation s={s} onBack={vi.fn()} onClose={vi.fn()} />);
    expect(await screen.findByText("how is the build?")).toBeTruthy();
    expect(screen.getByText("Green")).toBeTruthy();                           // the session's words as markdown
    expect(await screen.findByText("Mirrored since.")).toBeTruthy();
    expect(screen.getByText(/You, in Terminal/)).toBeTruthy();
    expect(screen.getByText(/3 earlier messages stay on mac's machine/)).toBeTruthy();
    expect(screen.getByText("claude --resume 't'", { exact: false })).toBeTruthy();
  });

  it("opens a link in what its machine sent only to another site", async () => {
    const s = session({ id: "t", title: "with links", client: "terminal", talk_at: 1_790_003_650 });
    serve({ "/api/ade/link/history/L1/t": { earlier: 0, updated_at: 1_790_003_650, messages: [
      { who: "session", text: "[Sign in here](#device=ABCD-EFGH) or read [the docs](https://example.org/docs).", at: 1_790_003_100 }] },
      "/api/ade/link/timeline/L1?limit=100&session=t": { events: [] } });
    const { container } = render(<SessionConversation s={s} onBack={vi.fn()} onClose={vi.fn()} />);
    expect((await screen.findByRole("link", { name: "the docs" })).getAttribute("target")).toBe("_blank");
    expect(screen.queryByRole("link", { name: "Sign in here" })).toBeNull();
    expect(container.textContent).toContain("Sign in here [link not opened: #device=ABCD-EFGH]");
  });

  it("opens at once where the list of sessions chose it", async () => {
    const s = session({ id: "m", title: "chosen in the list", notes: 1 });
    serve({ "/api/ade/link/timeline/L1?limit=100&session=m": { events: [note(5, "owner", "its words", 1_790_004_000_000)] } });
    useStore.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true } });
    render(<Channel onClose={vi.fn()} reading={s} />);
    expect(await screen.findByText("its words")).toBeTruthy();
    expect(screen.getByText("chosen in the list")).toBeTruthy();
  });

  it("reads its notes alone where its machine sends no conversation", async () => {
    const s = session({ id: "m", title: "mirrored", notes: 1 });
    serve({ "/api/ade/link/timeline/L1?limit=100&session=m": { events: [note(5, "owner", "just this", 1_790_004_000_000)] } });
    render(<SessionConversation s={s} onBack={vi.fn()} onClose={vi.fn()} />);
    expect(await screen.findByText("just this")).toBeTruthy();
  });
});
