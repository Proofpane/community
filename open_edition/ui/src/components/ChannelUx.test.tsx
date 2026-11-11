// Linked sessions as its owner uses it: the panel is a dialog that fits a phone, what
// is typed to a session is never lost, pairing is one line to run, signing says beforehand when a session will refuse,
// questions are answered where they are read, a hold counts down, and a move says early what keeps it from going.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { HistorySession, LinkAsk, LinkEvent, LinkMove, LinkView } from "../api";
import { useStore } from "../store";
import { clock, dayTime } from "../when";
import { pinLine as accountPinLine } from "./AccountDialog";
import { listLine, MachineSessions } from "./History";
import { focusables } from "./Modal";

// passkeys work here, as in a browser at the server's own address: the page offers to sign
const passkeys = vi.hoisted(() => {
  const get = vi.fn(async () => ({ id: "AQ", type: "public-key", response: {
    clientDataJSON: new Uint8Array([1]).buffer, authenticatorData: new Uint8Array([2]).buffer,
    signature: new Uint8Array([3]).buffer, userHandle: null } }));
  (globalThis as unknown as { PublicKeyCredential: unknown }).PublicKeyCredential = function PublicKeyCredential() {};
  Object.defineProperty(navigator, "credentials", { value: { get }, configurable: true });
  return { get };
});

import { approvedWords, AskCard, askLines, CANCEL_OFFLINE, Channel, CLIENT, Conversation, HeldMoveCard, LinkCard, linkingWords, mirrorSettings, MoveToWorkspace, moveWords, NARROW, NO_LINE, PairingCard, pairLine, pairWait, PANEL_OFFLINE, pinLine, PinNotice, pinsWarning, Question, readAtOf, repositoryMissing, SessionConversation, SIGN_OFFLINE, SignBox, skewOf, untilWords, type AccountKey, type TicketOut } from "./Channel";

const T = Math.floor(Date.now() / 1000);

type Call = { path: string; method: string; body: Record<string, unknown> | undefined };
type Answer = [number, unknown];
type Route = (body: Record<string, unknown> | undefined) => Answer | Promise<Answer>;
let calls: Call[] = [];
/** The server as the test has it answer: each "METHOD /path" its route; anything else 404. */
function serve(routes: Record<string, Route>) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path: String(url), method, body });
    const route = routes[`${method} ${url}`];
    const [status, data] = route ? await route(body) : [404, { detail: `no route for ${method} ${url}` }];
    return new Response(JSON.stringify(data), { status });
  }));
}
const sent = (path: string) => calls.filter((c) => c.method === "POST" && c.path === path);

const link = (over: Partial<LinkView> = {}): LinkView => ({
  id: "L1", name: "MacBook Pro", kind: "local", session_ref: null, interval_min: 15, created_at: T - 3600,
  last_seen_at: T - 60, last_heartbeat_at: T - 60, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0,
  checkpoint_at: null, events: 0, last_event: null, commands: [], stopped_at: null, ...over,
});
const sessions = (links: LinkView[], more: Record<string, unknown> = {}): Route =>
  () => [200, { links, questions: [], public_url: "https://pp.example", connect_hold: 180, move_hold: 180, ...more }];
const base = (more: Record<string, Route> = {}): Record<string, Route> => ({
  "GET /api/ade/link/sessions": sessions([link()]),
  "GET /api/ade/link/history": () => [200, { sessions: [] }],
  ...more,
});

/** A media query that matches as a screen `width` pixels wide would. */
function screenWide(width: number) {
  vi.stubGlobal("innerWidth", width);
  vi.stubGlobal("matchMedia", (q: string) => {
    const most = /max-width:\s*(\d+)px/.exec(q);
    const least = /min-width:\s*(\d+)px/.exec(q);
    const matches = (!most || width <= Number(most[1])) && (!least || width >= Number(least[1]));
    return { matches, media: q, addEventListener() {}, removeEventListener() {} };
  });
}

/** The page with a button that opens Linked sessions, as the title bar's does. */
function Page() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Linked sessions</button>
      {open && <Channel onClose={() => setOpen(false)} />}
    </>
  );
}

beforeEach(() => {
  useStore.setState({ user: { id: "u", email: "o@example.org", can_sign_in_again: true } });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  passkeys.get.mockClear();
  useStore.setState(useStore.getInitialState());
});

describe("Linked sessions, as a dialog", () => {
  it("is a modal dialog: the keyboard starts on it, stays in it both ways round, and goes back to what opened it", async () => {
    serve(base());
    render(<Page />);
    const opener = screen.getByRole("button", { name: "Linked sessions" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Linked sessions" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    // its name is read first, then what is in it from the start: the keyboard starts on its title
    expect(document.activeElement).toBe(within(dialog).getByRole("heading", { name: "Linked sessions" }));
    await screen.findByText("MacBook Pro", { selector: "bdi" });
    const reach = focusables(dialog);
    reach[reach.length - 1].focus();
    fireEvent.keyDown(document.activeElement!, { key: "Tab" });
    expect(document.activeElement).toBe(reach[0]);            // round to the first, never out to the page behind
    fireEvent.keyDown(document.activeElement!, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(reach[reach.length - 1]);
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });
});

describe("what is typed to a session", () => {
  const card = async () => (await screen.findByText("MacBook Pro", { selector: "bdi" })).closest("div.rounded-lg") as HTMLElement;
  const instruct = async (words: string) => {
    fireEvent.click(within(await card()).getByRole("button", { name: /Instruct/ }));
    const box = screen.getByLabelText("What MacBook Pro should do") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: words } });
    box.focus();
    return box;
  };

  it("is never lost to Escape or a click outside: the panel stays and says why; Close keeps it for next time", async () => {
    serve(base());
    render(<Page />);
    fireEvent.click(screen.getByRole("button", { name: "Linked sessions" }));
    const box = await instruct("run the tests, then post the result");
    fireEvent.keyDown(box, { key: "Escape" });
    const dialog = screen.getByRole("dialog", { name: "Linked sessions" });
    expect(screen.getByText(/stays open while you have words in it/)).toBeTruthy();
    fireEvent.mouseDown(dialog.parentElement!);                // a click outside it
    expect(screen.getByRole("dialog", { name: "Linked sessions" })).toBe(dialog);
    fireEvent.keyDown(document.body, { key: "Escape" });        // from elsewhere, while the composer holds words
    expect(screen.getByRole("dialog", { name: "Linked sessions" })).toBe(dialog);
    fireEvent.click(screen.getByRole("button", { name: "Close Linked sessions" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Linked sessions" }));
    await card();
    expect((screen.getByLabelText("What MacBook Pro should do") as HTMLTextAreaElement).value).toBe("run the tests, then post the result");
  });

  it("closes on Escape and a click outside when no composer holds words", async () => {
    serve(base());
    render(<Page />);
    fireEvent.click(screen.getByRole("button", { name: "Linked sessions" }));
    await card();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Linked sessions" }));
    fireEvent.mouseDown(screen.getByRole("dialog").parentElement!);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("asks before Cancel discards it, and once discarded it does not come back", async () => {
    serve(base());
    render(<Page />);
    fireEvent.click(screen.getByRole("button", { name: "Linked sessions" }));
    await instruct("ship it");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByLabelText("What MacBook Pro should do")).toBeTruthy();     // still there
    fireEvent.click(screen.getByRole("button", { name: "Discard draft?" }));
    expect(screen.queryByLabelText("What MacBook Pro should do")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Close Linked sessions" }));
    fireEvent.click(screen.getByRole("button", { name: "Linked sessions" }));
    await card();
    expect(screen.queryByLabelText("What MacBook Pro should do")).toBeNull();
  });

  it("to a link revoked on another device is said not sent, and kept to copy", async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    let links = [link()];
    serve(base({ "GET /api/ade/link/sessions": () => sessions(links)(undefined) }));
    render(<Channel onClose={() => {}} />);
    await instruct("rebase onto main");
    links = [];                                                 // revoked elsewhere
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    const notice = await screen.findByRole("alert");
    expect(notice.textContent).toContain("MacBook Pro is no longer linked: it was revoked, here or on another device. "
      + "Your instruction to it was not sent; here it is, to copy.");
    expect(within(notice).getByText("rebase onto main")).toBeTruthy();
    fireEvent.click(within(notice).getByRole("button", { name: /Copy the words/ }));
    expect(writeText).toHaveBeenCalledWith("rebase onto main");
    fireEvent.click(within(notice).getByRole("button", { name: "Discard" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("a link's card", () => {
  const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
    ({ seq, time: new Date(Date.now() - 240_000).toISOString(), kind: "step", from: "MacBook Pro", to: "owner", ref: null, text: "", ...over });
  const command = (seq: number, text: string, state: LinkEvent["state"] = "taken"): LinkEvent =>
    ({ seq, time: new Date(T * 1000).toISOString(), kind: "instruction", from: "owner", to: "MacBook Pro", ref: null, text, state });
  const latest = () => screen.getByLabelText("Latest");

  it("says its newest event as a sentence: no digest, no session id, its words on one line without Markdown's marks", () => {
    serve(base());
    const cases: [LinkEvent, string][] = [
      [ev(10, { kind: "take", ref: 11, text: `sha256:${"1e63".repeat(16)}` }), "Took your instruction “Run the tests again.”"],
      [ev(5, { kind: "done", ref: 3, text: "done: Pushed `sign-in` and opened draft PR **#42**.\n\n- CI: 3 checks running\n- Next: wait for review\n" }),
        "Replied to your instruction “Yes, push the branch.”, done: Pushed sign-in and opened draft PR #42. CI: 3 checks running · Next: wait for review"],
      [ev(2, { kind: "question", text: "Should I push the [sign-in](https://example.org/b) branch?" }), "Asked you: Should I push the sign-in branch?"],
      [ev(7, { kind: "waiting", ref: 11, text: "working on it" }), "Working on your instruction “Run the tests again.”"],
      [ev(8, { kind: "note", client_id: `stopped:${T}`, text: "stopped at 2026-10-09T00:00:00Z" }), "Saw your stop"],
      [ev(9, { kind: "take", ref: 99, text: `sha256:${"ab".repeat(32)}` }), "Took one of your commands"],
    ];
    for (const [e, said] of cases) {
      render(<LinkCard l={link({ last_event: e, session_ref: "6f1c2d3e-0a1b-4c5d-8e9f-a0b1c2d3e4f5", events: 5,
        commands: [command(11, "Run the tests again."), command(3, "Yes, push the branch.")] })} questions={[]} onTicket={() => {}} />);
      const line = latest();
      expect(line.textContent?.replace(/(just now|\d+m ago)$/, ""), e.kind).toBe(said);
      expect(line.textContent).toMatch(/4m ago$/);
      expect(document.body.textContent).not.toMatch(/sha256:|\\n|\*\*|6f1c2d3e|\[\d+\]/);
      cleanup();
    }
  });

  it("titles its line, where its newest is a call the session made, with what a Bash step names", () => {
    // The card is where the owner follows a running turn, so its Called line
    // carries the step's words as the conversation's step rows do (a title, which a phone does not show)
    serve(base());
    const said = 'A Bash step names the program its command begins with, never what else it runs: "grep -l x *.yml | xargs rm -f" shows as "Bash: grep". Where it cannot tell the program, as for a command that begins by setting a variable, it shows Bash alone.';
    const cases: [LinkEvent, boolean][] = [
      [ev(12, { kind: "note", mirror: "step", text: "Bash: grep" }), true],
      [ev(13, { kind: "note", mirror: "live", text: JSON.stringify({ items: [["calls", ["Bash: grep"]]] }) }), true],
      [ev(14, { kind: "note", mirror: "session", text: "Found it." }), false],
    ];
    for (const [e, titled] of cases) {
      render(<LinkCard l={link({ last_event: e, events: 5 })} questions={[]} onTicket={() => {}} />);
      const titles = Array.from(latest().querySelectorAll("[title]")).map((el) => el.getAttribute("title"));
      expect(titles.includes(said), e.mirror).toBe(titled);
      cleanup();
    }
  });

  it("offers Open conversation first, and Link again, Stop and Revoke in a menu", async () => {
    serve(base({ "POST /api/ade/link/links/L1/stop": () => [200, { stopped_at: T }], "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }] }));
    const talk = vi.fn();
    const ticket = vi.fn();
    render(<LinkCard l={link({ events: 2 })} questions={[]} onTicket={ticket} onTalk={talk} />);
    const open = screen.getByRole("button", { name: /Open conversation/ });
    expect(open.className).toContain("btn-primary");
    expect(open.parentElement?.querySelector("button")).toBe(open);          // the first of the card's actions
    for (const name of [/^Link again$/, /^Revoke/, /^Stop$/]) expect(screen.queryByRole("button", { name })).toBeNull();
    fireEvent.click(open);
    expect(talk).toHaveBeenCalledTimes(1);
    const more = screen.getByRole("button", { name: "More for MacBook Pro" });
    fireEvent.click(more);
    const menu = screen.getByRole("menu", { name: "More for MacBook Pro" });
    expect(within(menu).getAllByRole("menuitem").map((b) => b.textContent?.trim())).toEqual(["Link again", "Stop it…", "Revoke…"]);
    fireEvent.click(within(menu).getByRole("menuitem", { name: /Link again/ }));
    expect(ticket).toHaveBeenCalledTimes(1);
    fireEvent.click(more);
    fireEvent.click(screen.getByRole("menuitem", { name: /Stop it/ }));
    const stopping = screen.getByRole("group", { name: "Stopping MacBook Pro" });
    expect(stopping.textContent).toContain("Its hooks hold every tool, and it takes nothing more, until you resume it");
    expect(sent("/api/ade/link/links/L1/stop")).toHaveLength(0);              // nothing until it is confirmed
    fireEvent.click(within(stopping).getByRole("button", { name: /Stop it/ }));
    await vi.waitFor(() => expect(sent("/api/ade/link/links/L1/stop")).toHaveLength(1));
    fireEvent.click(more);
    fireEvent.click(screen.getByRole("menuitem", { name: /Revoke/ }));
    const revoking = await screen.findByRole("group", { name: "Revoking MacBook Pro" });
    fireEvent.change(within(revoking).getByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(within(revoking).getByRole("button", { name: "Confirm" }));
    // the confirmation is made for revoking this link, and nothing else
    await vi.waitFor(() => expect(sent("/api/account/confirm").map((c) => c.body)).toEqual([{ password: "pw", change: "link-revoke:L1" }]));
  });

  it("closes its menu on Escape without closing Linked sessions", async () => {
    serve(base());
    render(<Channel onClose={() => {}} />);
    const more = await screen.findByRole("button", { name: "More for MacBook Pro" });
    fireEvent.click(more);
    const item = screen.getAllByRole("menuitem")[0];
    expect(document.activeElement).toBe(item);
    fireEvent.keyDown(item, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.getByRole("dialog", { name: "Linked sessions" })).toBeTruthy();
    expect(document.activeElement).toBe(more);
  });

  // Where a menu would start, measured: `crossing` says whether leftward, from the button's right edge, it starts left of
  // the edge of what clips it (the panel, at 16); rightward, from the button's left edge, it starts at 44.
  const placed = (crossing: () => boolean) => vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const left = this.getAttribute("role") === "menu" ? (this.className.includes("right-0") ? (crossing() ? -114 : 200) : 44)
      : (this as HTMLElement).dataset?.panel !== undefined ? 16 : 0;
    return { left, right: left + 240, top: 0, bottom: 0, width: 240, height: 0, x: left, y: 0, toJSON: () => ({}) } as DOMRect;
  });
  const inPanel = () => render(
    <div data-panel="" style={{ overflowX: "hidden" }}><LinkCard l={link({ events: 2 })} questions={[]} onTicket={() => {}} /></div>);

  it("opens its menu rightward where leftward it would cross the edge of what clips it, as in a narrow Linked sessions", () => {
    // More wrapped to the start of its row: leftward, the menu showed only its empty right end
    serve(base());
    const measured = placed(() => true);
    try {
      inPanel();
      fireEvent.click(screen.getByRole("button", { name: "More for MacBook Pro" }));
      const menu = screen.getByRole("menu", { name: "More for MacBook Pro" });
      expect(menu.className).toContain("left-0");
      expect(menu.className).not.toContain("right-0");
      expect(within(menu).getAllByRole("menuitem").map((b) => b.textContent?.trim())).toEqual(["Link again", "Stop it…", "Revoke…"]);
    } finally {
      measured.mockRestore();
    }
  });

  it("keeps its menu leftward where it fits, and measures again each time it opens", () => {
    serve(base());
    let crossing = false;
    const measured = placed(() => crossing);
    try {
      inPanel();
      const more = screen.getByRole("button", { name: "More for MacBook Pro" });
      const side = () => screen.getByRole("menu", { name: "More for MacBook Pro" }).className.match(/\b(left|right)-0\b/)?.[1];
      fireEvent.click(more);
      expect(side()).toBe("right");
      fireEvent.click(more);
      crossing = true;
      fireEvent.click(more);
      expect(side()).toBe("left");
      fireEvent.click(more);
      crossing = false;
      fireEvent.click(more);
      expect(side()).toBe("right");
    } finally {
      measured.mockRestore();
    }
  });

  it("names a question's Dismiss for what it does, and says what it tells the session", async () => {
    serve(base({ "POST /api/ade/link/questions/2/close": () => [200, { seq: 9 }] }));
    const q = ev(2, { kind: "question", time: new Date(T * 1000).toISOString(), text: "Should I push?" });
    render(<Question q={q} linkId="L1" />);
    expect(screen.getByText(/asks you/).textContent).toBe("MacBook Pro asks you · just now");
    expect(screen.queryByRole("button", { name: "Close" })).toBeNull();
    const dismiss = screen.getByRole("button", { name: "Dismiss question" });
    expect(document.getElementById(dismiss.getAttribute("aria-describedby") ?? "")?.textContent)
      .toBe("Dismissing tells MacBook Pro you closed the question without an answer.");
    fireEvent.click(dismiss);
    await vi.waitFor(() => expect(sent("/api/ade/link/questions/2/close")).toHaveLength(1));
  });
});

describe("linking a session", () => {
  const FP = "e3d8bd098e5d4d42a9c1bd5e44fc1b41";
  /** The ticket's routes; with `ended`, the ticket has its id (tk1) and the server names how its pairing ended. */
  const ticketRoutes = (expires: () => number, links: () => LinkView[] = () => [link()],
                        ended?: () => { ticket_id: string; state: "linked" | "cancelled" }[]) => base({
    "GET /api/ade/link/sessions": () => sessions(links(), ended ? { ended_pairings: ended() } : {})(undefined),
    "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
    "POST /api/account/confirm": (b) => (b?.password === "right one" ? [200, { confirmation: "c7" }] : [403, { detail: "no" }]),
    "POST /api/ade/link/tickets": (b) => (b?.confirmation === "c7"
      ? [200, { ticket: "PP-ABCDEFGH", expires_at: expires(), url: "https://pp.example", keys: [FP], e2e: null,
                ...(ended ? { ticket_id: "tk1" } : {}) }] : [403, { detail: "no" }]),
  });
  const named = (name: string, where: "This Mac" | "claude.ai" = "This Mac") => {
    fireEvent.change(screen.getByLabelText("What to call it"), { target: { value: name } });
    fireEvent.click(screen.getByRole("radio", { name: new RegExp(where) }));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  };
  const confirmed = async (name: string) => {
    const word = await screen.findByRole("group", { name: `Linking “${name}”` });
    fireEvent.change(within(word).getByLabelText("Your password"), { target: { value: "right one" } });
    fireEvent.click(within(word).getByRole("button", { name: "Confirm" }));
    return screen.findByLabelText("The line to run");
  };

  it("asks the session's name and where it runs before any ticket, holding the name to the server's rules", async () => {
    serve(ticketRoutes(() => T + 600));
    render(<Channel onClose={() => {}} />);
    await screen.findByText("MacBook Pro", { selector: "bdi" });
    fireEvent.click(screen.getByRole("button", { name: /Link a session/ }));
    expect(document.activeElement).toBe(screen.getByLabelText("What to call it"));
    expect((screen.getByRole("radio", { name: /This Mac/ }) as HTMLInputElement).checked).toBe(true);
    for (const [name, said] of [["", "Give the session a name, such as MacBook Pro."],
      ["my  mac", "A name has no two spaces together and no space at its end."],
      ["macbook pro", "A linked session is named MacBook Pro already: choose another name, or link that one again from its card."],
      ["-x", "A name is letters, digits, spaces, dots, dashes and underscores, at most 60, starting with a letter or a digit."]]) {
      named(name);
      expect(screen.getByRole("alert").textContent, name).toBe(said);
    }
    expect(calls.filter((c) => c.path.startsWith("/api/account/confirm") || c.path === "/api/ade/link/tickets")).toEqual([]);
    named("Studio");
    expect(await screen.findByRole("group", { name: "Linking “Studio”" })).toBeTruthy();
    expect(sent("/api/ade/link/tickets")).toHaveLength(0);           // the account's word first
  });

  it("gives one complete line for this Mac, with the client where SETUP_A.md installs it, and when its ticket ends", async () => {
    let ends = 0;
    serve(ticketRoutes(() => (ends = Math.floor(Date.now() / 1000) + 600)));
    render(<Channel onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Link a session/ }));
    named("Studio");
    const line = await confirmed("Studio");
    expect(line.textContent).toBe(`python3 -I "$HOME/.proofpane-ade/ade_link.py" setup --url https://pp.example --ticket PP-ABCDEFGH --keys ${FP} --e2e 0:none --name 'Studio' --kind local --mirror`);
    expect(line.textContent).not.toMatch(/NAME|REF|open_edition\/|\n|#/);
    const card = screen.getByRole("region", { name: "Linking Studio" });
    expect(card.textContent).toMatch(/To link “Studio”, run this line on this Mac/);
    expect(card.textContent).toContain(`Waiting for the session to pair: the ticket works once, until ${clock(ends)}: `);
    expect(card.textContent).toMatch(/: (9:[0-5]\d|10:00) left\./);
    expect(card.textContent).toContain("It pins your passkey as it links, so it acts only on what that key signs.");
  });

  it("names a claude.ai session's page from that session's own shell", async () => {
    serve(ticketRoutes(() => T + 600));
    render(<Channel onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Link a session/ }));
    named("Planner", "claude.ai");
    const line = await confirmed("Planner");
    expect(line.textContent).toMatch(/ --name 'Planner' --kind cloud --ref "https:\/\/claude\.ai\/code\/session_\$\{CLAUDE_CODE_REMOTE_SESSION_ID#cse_\}"$/);
    expect(screen.getByRole("region", { name: "Linking Planner" }).textContent).toContain("run this line in the claude.ai session");
  });

  it("clears the ticket's card once the link is there, and says it is linked", async () => {
    let links = [link()];
    let ended: { ticket_id: string; state: "linked" | "cancelled" }[] = [];
    serve(ticketRoutes(() => T + 600, () => links, () => ended));
    render(<Channel onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Link a session/ }));
    named("Studio");
    await confirmed("Studio");
    // linked as it paired, held for no time: never listed, and told by the server's word
    links = [link(), link({ id: "L2", name: "Studio", created_at: T })];
    ended = [{ ticket_id: "tk1", state: "linked" }];
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    expect((await screen.findByText("“Studio” is linked.")).closest("[role=status]")).toBeTruthy();
    expect(screen.queryByLabelText("The line to run")).toBeNull();
  });

  it("says when its ticket has expired, and makes a new one with the account's word", async () => {
    serve(ticketRoutes(() => T - 5));
    render(<Channel onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Link a session/ }));
    named("Studio");
    await confirmed("Studio");
    expect(screen.getByRole("region", { name: "Linking Studio" }).textContent).toMatch(/The ticket expired at \d\d:\d\d\./);
    fireEvent.click(screen.getByRole("button", { name: "Make a new ticket" }));
    expect(await screen.findByRole("group", { name: "Linking “Studio”" })).toBeTruthy();
  });

  it("names the client the manual installs from $HOME, where the four templates that run it write the home folder out", ({ skip }) => {
    const folder = join(__dirname, "..", "..", "..");
    const file = (name: string) => readFileSync(join(folder, name), "utf8");
    // the manual is not part of every tree: where it is absent, what it states cannot be read back, and the test is
    // skipped, saying why
    skip(!existsSync(join(folder, "MANUAL.md")),
      "MANUAL.md is not in this tree, so what the manual states cannot be read back here");
    const readme = file("MANUAL.md").replace(/\s+/g, " ");
    expect(readme).toContain("the client copied to `~/.proofpane-ade/ade_link.py`");
    // and how a template's home folder is filled in: a path written out, where a ~ inside the line's quotes is not one
    expect(readme).toContain("`/absolute/path/to/home` with what `echo $HOME` prints in the owner's own terminal");
    expect(CLIENT.startsWith("python3 -I ")).toBe(true);
    expect(CLIENT.slice("python3".length)).toBe(` -I "$HOME/.proofpane-ade/ade_link.py"`);
    // The four templates that run the client name it, the link's directory and any other path in ~/.proofpane-ade with
    // the home folder written out: a SessionStart line's output goes into the
    // session as its context, and each line runs the client from a folder a HOME names and acts on the link's directory
    // there, while a settings file a session loads can set HOME. Here each file names the client with a --dir at least
    // once, every path in it into .proofpane-ade starts at the home folder, and none names HOME in any form, or ~; the
    // Python tests hold their lines one by one (test_ade_link_client.py the link, mirror and everywhere blocks, and
    // test_ade_link_remote_guard.py the file that gives two blocks in one as exactly those two). Each starts its
    // interpreter in UTF-8 mode too, -X utf8, so that a locale a settings file names cannot end it before the client
    // runs; the page's lines to type keep CLIENT's -I alone, as above.
    for (const name of ["ade_link.settings.json", "ade_link_and_hook.settings.json", "ade_link.mirror.settings.json",
      "ade_link.everywhere.settings.json"]) {
      const text = file(name);
      expect(text, name).toContain(` -I -X utf8 \\"/absolute/path/to/home/.proofpane-ade/ade_link.py\\" `
        + `--dir \\"/absolute/path/to/home/.proofpane-ade/NAME\\" `);
      expect(text.split(".proofpane-ade/").length, name).toBe(text.split("/absolute/path/to/home/.proofpane-ade/").length);
      expect(text, name).not.toMatch(/HOME|~/);
    }
    // The page's own lines and its mirror block name the home folder as $HOME: the page cannot know the owner's home
    // folder (nor the venv's path), and gives each line and the block whole, nothing left to fill in. That the typed
    // lines run in the owner's own shell is not the reason: the ask command and the pin line run in a session's own.
    // So the block is the mirror template with the link's name filled in, the client run by python3 on the
    // PATH and the home folder as $HOME, and in nothing else.
    const block = file("ade_link.mirror.settings.json").split("/absolute/path/to/venv/bin/python").join("python3")
      .split("/absolute/path/to/home").join("$HOME").split("NAME").join("mac");
    expect(JSON.parse(mirrorSettings("mac") ?? "")).toEqual(JSON.parse(block));
  });

  it("starts every line it gives to run with that client, never a file in the folder it is run in", () => {
    const fp = "a".repeat(32);
    const lines = [
      pairLine({ ticket: "PP-ABCDEFGH", expires_at: 1_790_000_000, url: "https://pp.example" }, { name: "mac", kind: "local" }),
      askLines("https://pp.example", `ask-${"c".repeat(20)}`)?.[0], pinLine("mac", [fp]), accountPinLine(fp),
      listLine("mac")?.split(" && ")[1]];
    for (const line of lines) {
      expect(line?.startsWith(`${CLIENT} `), String(line)).toBe(true);
      expect(line, String(line)).not.toContain("open_edition/");
    }
  });
});

describe("a pairing that waits", () => {
  const FP = "ab".repeat(16);
  const now = () => Math.floor(Date.now() / 1000);
  const pairing = (over: Partial<LinkAsk> = {}): LinkAsk => ({
    id: "A9", name: "Studio", kind: "local", fingerprint: FP, asked_from: "203.0.113.7", created_at: T - 5,
    expires_at: T - 5, session_ref: null, state: "held", hold_id: "H9", takes_effect_at: T + 161, told: 1,
    failed: null, via: "ticket", ticket_id: "tk1", replaces: null, ...over,
  });
  const approval = (over: Partial<LinkAsk> = {}): LinkAsk => ({
    id: "A1", name: "Cloud planner", kind: "cloud", fingerprint: "0123456789abcdef0123456789abcdef",
    asked_from: "203.0.113.7", created_at: T - 60, expires_at: T + 1740, session_ref: null, state: "held",
    hold_id: "H1", takes_effect_at: T + 42, told: 0, failed: null, ...over,
  });
  /** The ticket's routes, with a ticket the pairing is listed under (tk1) and how long a pairing waits. */
  type Ended = { ticket_id: string; state: "linked" | "cancelled" };
  const routes = (state: { asks: LinkAsk[]; links: LinkView[]; serverAhead?: number; ended?: Ended[]; hold?: number }, more: Record<string, Route> = {}) => base({
    "GET /api/ade/link/sessions": () => sessions(state.links, { asks: state.asks, server_time: now() + (state.serverAhead ?? 0),
                                                                ended_pairings: state.ended ?? [], connect_hold: state.hold ?? 180 })(undefined),
    "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
    "POST /api/account/confirm": () => [200, { confirmation: "c7" }],
    "POST /api/ade/link/tickets": () => [200, { ticket: "PP-ABCDEFGH", expires_at: now() + 600, ticket_id: "tk1",
      url: "https://pp.example", keys: [], e2e: null, pair_hold: 180 }],
    ...more,
  });
  const linking = async (name: string) => {
    fireEvent.click(await screen.findByRole("button", { name: /Link a session/ }));
    fireEvent.change(screen.getByLabelText("What to call it"), { target: { value: name } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    const word = await screen.findByRole("group", { name: `Linking “${name}”` });
    fireEvent.change(within(word).getByLabelText("Your password"), { target: { value: "right one" } });
    fireEvent.click(within(word).getByRole("button", { name: "Confirm" }));
    await screen.findByLabelText("The line to run");
  };
  const reads = () => calls.filter((c) => c.method === "GET" && c.path === "/api/ade/link/sessions").length;

  it("counts down by the server's clock, and says Linking… once its time has come", () => {
    expect(skewOf(T + 100, T * 1000)).toBe(100_000);
    expect(skewOf(undefined, T * 1000)).toBe(0);
    // a read made 2 minutes ago by the page's monotonic clock, whatever this browser's clock did since
    expect(readAtOf(5_000, T * 1000, 125_000)).toBe(T * 1000 - 120_000);
    expect(readAtOf(null, T * 1000, 125_000)).toBe(T * 1000);
    expect(linkingWords(T + 161, T * 1000)).toBe(`linked in 2:41, at ${clock(T + 161)}, unless you cancel it.`);
    // this browser's clock 100 seconds behind the server's: 61 seconds left by the server's
    expect(linkingWords(T + 161, T * 1000, 100_000)).toBe(`linked in 1:01, at ${clock(T + 161)}, unless you cancel it.`);
    expect(linkingWords(T + 161, T * 1000, 161_000)).toBe("Linking…");
    expect(linkingWords(T + 30, T * 1000, 0, "moved")).toBe(`moved in 0:30, at ${clock(T + 30)}, unless you cancel it.`);
    expect(linkingWords(null, T * 1000)).toBe("");
    expect(approvedWords(T + 42, T * 1000)).toBe(untilWords(T + 42, T * 1000));
    expect(approvedWords(T + 42, T * 1000, 10_000)).toBe(untilWords(T + 42, (T + 10) * 1000));
    expect(approvedWords(T + 42, T * 1000, 42_000)).toBe("Linking…");
  });

  it("says in the ticket's box how long a pairing then waits", () => {
    expect(pairWait(180, false)).toBe(" Once the session pairs, it is linked 3 minutes later, unless you cancel it here.");
    expect(pairWait(900, true)).toBe(" Once the session pairs, it moves to the new key 15 minutes later, unless you cancel it here.");
    expect(pairWait(0, false)).toBe(" Once the session pairs, it is linked at once (this deployment holds no pairing).");
    expect(pairWait(undefined, false)).toBe("");
  });

  it("shows a session paired by a ticket with its key, where it came from, how many were told, and Cancel", () => {
    serve({});
    render(<PairingCard a={pairing({ takes_effect_at: now() + 161 })} />);
    const card = screen.getByRole("group", { name: "Pairing: Studio" });
    expect(card.textContent).toMatch(/^“Studio” \(local\) pairs by your ticket: linked in 2:(4\d|3\d), at \d\d:\d\d, unless you cancel it\./);
    expect(card.textContent).toContain(`key ${FP}`);
    expect(card.textContent).toContain("Compare it with the fingerprint the session printed: a session the ticket was "
      + "refused to prints its own, and one that differs means this pairing is not that session’s. From 203.0.113.7, ");
    expect(card.textContent).toContain("Its notice was sent toward 1 device with notifications on.");
    expect(within(card).getByRole("button", { name: "Cancel" })).toBeTruthy();
  });

  it("names a re-pair's old and new keys, and says why a pairing whose hold ended was not linked", () => {
    serve({});
    const old = { id: "L1", name: "Studio", created_at: T - 999, last_seen_at: T - 9, fingerprint: "cd".repeat(16), revoked_at: null };
    render(<PairingCard a={pairing({ replaces: old })} />);
    const card = screen.getByRole("group", { name: "Pairing: Studio" });
    expect(card.textContent).toMatch(/^“Studio” \(local\) moves to a new key by your ticket: (moved in |Linking…)/);
    expect(card.textContent).toContain(`old key ${"cd".repeat(16)}`);
    expect(card.textContent).toContain(`new key ${FP}`);
    cleanup();
    render(<PairingCard a={pairing({ state: "failed", failed: "The account already has a live link named Studio." })} />);
    expect(screen.getByRole("group", { name: "Not linked: Studio" }).textContent).toBe(
      "“Studio” (local) paired by your ticket, and the link could not be made: The account already has a live link named Studio.");
  });

  it("reads Linked sessions every 2 seconds while its ticket is out, and becomes the pairing's card as it pairs", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const state = { asks: [] as LinkAsk[], links: [link()], ended: [] as Ended[] };
      serve(routes(state));
      render(<Channel onClose={() => {}} />);
      await linking("Studio");
      const box = screen.getByRole("region", { name: "Linking Studio" });
      expect(box.textContent).toContain("Waiting for the session to pair: the ticket works once, until ");
      expect(box.textContent).toContain("Once the session pairs, it is linked 3 minutes later, unless you cancel it here.");
      const before = reads();
      // the effect that sets the reads going runs once React gets to it (a task of its own, on a busy machine later
      // than the line shows): let it run before the clock moves, or the reads start only after it has
      await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
      await act(async () => { vi.advanceTimersByTime(2000); });
      await vi.waitFor(() => expect(reads()).toBe(before + 1));
      state.asks = [pairing({ takes_effect_at: now() + 180 })];
      await act(async () => { vi.advanceTimersByTime(2000); });
      const card = await screen.findByRole("group", { name: "Pairing: Studio" });
      expect(card.textContent).toMatch(/pairs by your ticket: linked in (3:00|2:5\d), at /);
      expect(screen.queryByRole("region", { name: "Linking Studio" })).toBeNull();
      expect(screen.queryByRole("region", { name: "Asking to be linked" })).toBeNull();   // shown once, as the box
      // paired: no more reads of its own
      const paired = reads();
      await act(async () => { vi.advanceTimersByTime(6000); });
      expect(reads()).toBe(paired);
      // linked, as the server says it: the card goes and the panel says so
      state.asks = [];
      state.links = [link(), link({ id: "L2", name: "Studio", fingerprint: FP })];
      state.ended = [{ ticket_id: "tk1", state: "linked" }];
      fireEvent.click(screen.getByRole("button", { name: "Read again" }));
      expect((await screen.findByText("“Studio” is linked.")).closest("[role=status]")).toBeTruthy();
      expect(screen.queryByRole("group", { name: "Pairing: Studio" })).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("says a pairing cancelled before its time, from another device, is not linked", async () => {
    const state = { asks: [] as LinkAsk[], links: [link()], ended: [] as Ended[] };
    serve(routes(state));
    render(<Channel onClose={() => {}} />);
    await linking("Studio");
    state.asks = [pairing({ takes_effect_at: now() + 180 })];
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    await screen.findByRole("group", { name: "Pairing: Studio" });
    state.asks = [];
    state.ended = [{ ticket_id: "tk1", state: "cancelled" }];
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    expect(await screen.findByText("The pairing of “Studio” was cancelled: it is not linked.")).toBeTruthy();
    expect(screen.queryByRole("group", { name: "Pairing: Studio" })).toBeNull();
  });

  it("takes a pairing no longer listed as linked or cancelled only on the server's word, never on the links' look", async () => {
    // an earlier ticket's pairing, cancelled: not this ticket's
    const state = { asks: [] as LinkAsk[], links: [link()], ended: [{ ticket_id: "tk0", state: "cancelled" }] as Ended[] };
    serve(routes(state));
    render(<Channel onClose={() => {}} />);
    await linking("Studio");
    state.asks = [pairing({ takes_effect_at: now() - 1 })];
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    const card = await screen.findByRole("group", { name: "Pairing: Studio" });
    expect(card.textContent).toContain("pairs by your ticket: Linking…");
    // gone from the list, a link of its key there, and the server naming no end for it yet: nothing is said
    state.asks = [];
    state.links = [link(), link({ id: "L2", name: "Studio", fingerprint: FP })];
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    await vi.waitFor(() => expect(reads()).toBeGreaterThan(2));
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    expect(screen.getByRole("group", { name: "Pairing: Studio" }).textContent).toContain("Linking…");
    expect(screen.queryByText(/is linked|was cancelled/)).toBeNull();
    state.ended = [{ ticket_id: "tk0", state: "cancelled" }, { ticket_id: "tk1", state: "linked" }];
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    expect(await screen.findByText("“Studio” is linked.")).toBeTruthy();
  });

  it("says what became of a pairing it never saw listed, held for no time or ended between two reads", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const state = { asks: [] as LinkAsk[], links: [link()], ended: [] as Ended[] };
      serve(routes(state));
      render(<Channel onClose={() => {}} />);
      fireEvent.click(await screen.findByRole("button", { name: "More for MacBook Pro" }));
      fireEvent.click(screen.getByRole("menuitem", { name: /Link again/ }));
      const word = await screen.findByRole("group", { name: "Linking MacBook Pro again" });
      fireEvent.change(within(word).getByLabelText("Your password"), { target: { value: "right one" } });
      fireEvent.click(within(word).getByRole("button", { name: "Confirm" }));
      await screen.findByRole("region", { name: "Linking MacBook Pro" });
      state.ended = [{ ticket_id: "tk1", state: "linked" }];        // linked again with its own key, never listed
      // as in the test above: let the effect that sets the reads going run before the clock moves
      await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
      await act(async () => { vi.advanceTimersByTime(2000); });
      expect(await screen.findByText("“MacBook Pro” is linked again, with the same key.")).toBeTruthy();
      expect(screen.queryByRole("region", { name: "Linking MacBook Pro" })).toBeNull();
      const done = reads();
      await act(async () => { vi.advanceTimersByTime(6000); });
      expect(reads()).toBe(done);                                    // no more reads of its own
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps a pairing that failed on its card, saying why, until it is put away: never called cancelled", async () => {
    const state = { asks: [] as LinkAsk[], links: [link()], ended: [] as Ended[] };
    serve(routes(state));
    render(<Channel onClose={() => {}} />);
    await linking("Studio");
    state.asks = [pairing({ state: "failed", failed: "The account already has a live link named Studio." })];
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    const failed = await screen.findByRole("group", { name: "Not linked: Studio" });
    state.asks = [];                                                // its half hour on the list is over
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    await vi.waitFor(() => expect(reads()).toBeGreaterThan(2));
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    expect(screen.getByRole("group", { name: "Not linked: Studio" })).toBe(failed);
    expect(screen.queryByText(/was cancelled/)).toBeNull();
    fireEvent.click(within(failed).getByRole("button", { name: "Put away" }));
    expect(screen.queryByRole("group", { name: "Not linked: Studio" })).toBeNull();
  });

  it("puts a waiting pairing's card away to the requests, where it is listed with its Cancel", async () => {
    const state = { asks: [] as LinkAsk[], links: [link()], ended: [] as Ended[] };
    serve(routes(state));
    render(<Channel onClose={() => {}} />);
    await linking("Studio");
    state.asks = [pairing({ takes_effect_at: now() + 180 })];
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    const card = await screen.findByRole("group", { name: "Pairing: Studio" });
    expect(screen.queryByRole("region", { name: "Asking to be linked" })).toBeNull();
    fireEvent.click(within(card).getByRole("button", { name: "Put away" }));
    const listed = await screen.findByRole("region", { name: "Asking to be linked" });
    expect(within(within(listed).getByRole("group", { name: "Pairing: Studio" })).getByRole("button", { name: "Cancel" })).toBeTruthy();
  });

  it("cancels a pairing from its card with this sign-in alone", async () => {
    const state = { asks: [] as LinkAsk[], links: [link()] };
    serve(routes(state, { "POST /api/ade/holds/H9/cancel": () => { state.asks = []; return [200, { cancelled_at: T }]; } }));
    render(<Channel onClose={() => {}} />);
    await linking("Studio");
    state.asks = [pairing({ takes_effect_at: now() + 180 })];
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    const card = await screen.findByRole("group", { name: "Pairing: Studio" });
    fireEvent.click(within(card).getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("Cancelled — “Studio” won't be linked.")).toBeTruthy();
    expect(sent("/api/ade/holds/H9/cancel")).toHaveLength(1);
    expect(sent("/api/ade/holds/H9/cancel")[0].body).toBeUndefined();          // no confirmation
    await vi.waitFor(() => expect(screen.queryByRole("group", { name: "Pairing: Studio" })).toBeNull());
    expect(screen.queryByText(/was cancelled: it is not linked/)).toBeNull();   // said once
  });

  it("tells a link moved to its new key by the key, never by the name it had all along", async () => {
    const OLD = "cd".repeat(16);
    const state = { asks: [] as LinkAsk[], links: [link({ fingerprint: OLD })], ended: [] as Ended[] };
    serve(routes(state));
    render(<Channel onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "More for MacBook Pro" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Link again/ }));
    const word = await screen.findByRole("group", { name: "Linking MacBook Pro again" });
    fireEvent.change(within(word).getByLabelText("Your password"), { target: { value: "right one" } });
    fireEvent.click(within(word).getByRole("button", { name: "Confirm" }));
    const box = await screen.findByRole("region", { name: "Linking MacBook Pro" });
    expect(box.textContent).toContain("Once the session pairs, it moves to the new key 3 minutes later, unless you cancel it here.");
    // the confirmation is made for a ticket to re-pair this link, and nothing else
    expect(sent("/api/account/confirm").map((c) => c.body)).toEqual([{ password: "right one", change: "link-ticket:L1" }]);
    const old = { id: "L1", name: "MacBook Pro", created_at: T - 3600, last_seen_at: T - 60, fingerprint: OLD, revoked_at: null };
    state.asks = [pairing({ name: "MacBook Pro", replaces: old, takes_effect_at: now() + 180 })];
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    const card = await screen.findByRole("group", { name: "Pairing: MacBook Pro" });
    expect(card.textContent).toContain("moves to a new key by your ticket: moved in ");
    // the link of that name is there all along, with its old key: not yet linked again
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    expect(screen.getByRole("group", { name: "Pairing: MacBook Pro" })).toBeTruthy();
    expect(screen.queryByText(/is linked/)).toBeNull();
    state.asks = [];
    state.links = [link({ fingerprint: FP })];
    state.ended = [{ ticket_id: "tk1", state: "linked" }];
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    expect(await screen.findByText("“MacBook Pro” is linked again: its new key works now.")).toBeTruthy();
  });

  describe("Link again on the same machine, with the link's own key", () => {
    const old = { id: "L1", name: "MacBook Pro", created_at: T - 3600, last_seen_at: T - 60, fingerprint: FP, revoked_at: null };
    const repairs = async (state: { asks: LinkAsk[]; links: LinkView[]; ended: Ended[] }, more: Record<string, Route> = {}) => {
      serve(routes(state, more));
      render(<Channel onClose={() => {}} />);
      fireEvent.click(await screen.findByRole("button", { name: "More for MacBook Pro" }));
      fireEvent.click(screen.getByRole("menuitem", { name: /Link again/ }));
      const word = await screen.findByRole("group", { name: "Linking MacBook Pro again" });
      fireEvent.change(within(word).getByLabelText("Your password"), { target: { value: "right one" } });
      fireEvent.click(within(word).getByRole("button", { name: "Confirm" }));
      await screen.findByRole("region", { name: "Linking MacBook Pro" });
      state.asks = [pairing({ name: "MacBook Pro", replaces: old, takes_effect_at: now() + 180 })];
      fireEvent.click(screen.getByRole("button", { name: "Read again" }));
      return screen.findByRole("group", { name: "Pairing: MacBook Pro" });
    };

    it("is not said linked again while its pairing waits, though the link has that key all along", async () => {
      const state = { asks: [] as LinkAsk[], links: [link({ fingerprint: FP })], ended: [] as Ended[] };
      const card = await repairs(state);
      expect(card.textContent).toContain(`old key ${FP}`);
      fireEvent.click(screen.getByRole("button", { name: "Read again" }));
      await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
      expect(screen.getByRole("group", { name: "Pairing: MacBook Pro" })).toBe(card);
      expect(screen.queryByText(/is linked again/)).toBeNull();
      state.asks = [];
      state.ended = [{ ticket_id: "tk1", state: "linked" }];
      fireEvent.click(screen.getByRole("button", { name: "Read again" }));
      expect(await screen.findByText("“MacBook Pro” is linked again, with the same key.")).toBeTruthy();
    });

    it("cancelled from another device, it is said to stay on its old key", async () => {
      const state = { asks: [] as LinkAsk[], links: [link({ fingerprint: FP })], ended: [] as Ended[] };
      await repairs(state);
      state.asks = [];
      state.ended = [{ ticket_id: "tk1", state: "cancelled" }];
      fireEvent.click(screen.getByRole("button", { name: "Read again" }));
      expect(await screen.findByText("The pairing of “MacBook Pro” was cancelled: it stays on its old key.")).toBeTruthy();
      expect(screen.queryByText(/not linked/)).toBeNull();
    });

    it("cancelled from its card, it is said to stay on its old key", async () => {
      const state = { asks: [] as LinkAsk[], links: [link({ fingerprint: FP })], ended: [] as Ended[] };
      const card = await repairs(state, { "POST /api/ade/holds/H9/cancel": () => {
        state.asks = []; state.ended = [{ ticket_id: "tk1", state: "cancelled" }]; return [200, { cancelled_at: T }]; } });
      fireEvent.click(within(card).getByRole("button", { name: "Cancel" }));
      expect(await screen.findByText("Cancelled — “MacBook Pro” stays on its old key.")).toBeTruthy();
      await vi.waitFor(() => expect(screen.queryByRole("group", { name: "Pairing: MacBook Pro" })).toBeNull());
      expect(screen.queryByText(/won't be linked|was cancelled/)).toBeNull();   // said once, in its own words
    });
  });

  it("says how long a pairing waits by the latest read, which high security turned on since lengthens", async () => {
    const state = { asks: [] as LinkAsk[], links: [link()], ended: [] as Ended[], hold: 900 };
    serve(routes(state));
    render(<Channel onClose={() => {}} />);
    await linking("Studio");
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    const box = await screen.findByRole("region", { name: "Linking Studio" });
    await vi.waitFor(() => expect(box.textContent).toContain("Once the session pairs, it is linked 15 minutes later, unless you cancel it here."));
  });

  it("counts by the server's clock among the requests: past its time by the server's, Linking…", async () => {
    const state = { asks: [pairing({ takes_effect_at: now() + 100 }), approval({ takes_effect_at: now() + 100 })], links: [link()], serverAhead: 200 };
    serve(routes(state));
    render(<Channel onClose={() => {}} />);
    const listed = await screen.findByRole("region", { name: "Asking to be linked" });
    expect(within(listed).getByRole("group", { name: "Pairing: Studio" }).textContent).toContain("pairs by your ticket: Linking…");
    expect(within(listed).getByRole("group", { name: "Approved: Cloud planner" }).textContent).toContain("Linking…");
  });

  it("counts from the read that carried the server's time, not from when the panel shows it", async () => {
    // the store read Linked sessions two minutes before the panel opened, the server's clock and this browser's
    // agreeing: a pairing and an approval had 3 minutes left at the read, so 1 minute as the panel opens
    const W = 1_790_000_000_000;
    let wall = W, mono = 5_000;
    vi.spyOn(Date, "now").mockImplementation(() => wall);
    vi.spyOn(performance, "now").mockImplementation(() => mono);
    const at = W / 1000 + 180;
    serve(routes({ asks: [pairing({ takes_effect_at: at }), approval({ takes_effect_at: at })], links: [link()] }));
    await useStore.getState().refreshLinks();
    wall += 120_000;
    mono += 120_000;
    render(<Channel onClose={() => {}} />);
    const listed = await screen.findByRole("region", { name: "Asking to be linked" });
    expect(within(listed).getByRole("group", { name: "Pairing: Studio" }).textContent)
      .toContain(`pairs by your ticket: linked in 1:00, at ${clock(at)}, unless you cancel it.`);
    expect(within(listed).getByRole("group", { name: "Approved: Cloud planner" }).textContent)
      .toContain(`Takes effect in 1:00 (${clock(at)})`);
    expect(reads()).toBe(1);        // the panel read nothing itself: the store had the links
  });
});

describe("signing, before the touch", () => {
  const TOUCH = "1".repeat(32), PHONE = "2".repeat(32), KEY = "3".repeat(32);
  const key = (id: string, name: string, fingerprint: string, created_at: number, tier = "device"): AccountKey =>
    ({ id, name, fingerprint, created_at, tier });
  const mine = [key("p1", "Touch ID", TOUCH, T - 7200), key("p2", "iPhone", PHONE, T - 60), key("p3", "YubiKey", KEY, T - 9000, "hardware")];

  it("finds the passkeys a session refuses: from its pins where it reports them, else those added since it was linked", () => {
    expect(pinsWarning(mine, false, [TOUCH, KEY], T - 3600)).toEqual(
      { refused: [{ name: "iPhone", fingerprint: PHONE }], all: false, known: true, pinned: ["Touch ID", "YubiKey"] });
    expect(pinsWarning(mine, false, ["9".repeat(32)], T - 3600)?.all).toBe(true);
    expect(pinsWarning(mine, false, [], T - 3600)).toEqual(
      { refused: [{ name: "iPhone", fingerprint: PHONE }], all: false, known: false, pinned: [] });
    expect(pinsWarning(mine, false, null, null)).toBeNull();                       // nothing to tell it by
    expect(pinsWarning(mine, false, [TOUCH, PHONE, KEY], T - 3600)).toBeNull();     // it takes them all
    expect(pinsWarning(mine, true, [KEY], T - 3600)).toBeNull();                    // high security: the hardware key alone signs
    expect(pinsWarning(mine, true, [TOUCH], T - 3600)).toEqual(
      { refused: [{ name: "YubiKey", fingerprint: KEY }], all: true, known: true, pinned: [] });
  });

  const signRoutes = (pins: string[]) => base({
    "POST /api/ade/link/nonce": () => [200, { nonce: "n1", rp_id: "pp.example", deployment: "pp.example", account: "u", server_time: T }],
    "GET /api/account/passkeys": () => [200, { passkeys: mine.slice(0, 2), high_security: false }],
    "GET /api/ade/e2e": () => [200, { links: [{ link_id: "L1", name: "MacBook Pro", key_id: null, last_required_at: null, pins }] }],
  });
  const reviewed = async (words = "run the tests") => {
    render(<SignBox purpose="instruction" to="L1" toName="MacBook Pro" placeholder="What MacBook Pro should do" submit="Review" onDone={async () => {}} />);
    fireEvent.change(screen.getByLabelText("What MacBook Pro should do"), { target: { value: words } });
    fireEvent.click(screen.getByRole("button", { name: /Review/ }));
    await screen.findByRole("button", { name: /Sign with your passkey/ });
  };

  it("says at Review which passkey the session refuses, with the pin line for its own window, before any prompt", async () => {
    serve(signRoutes([TOUCH]));
    await reviewed();
    const note = screen.getByRole("note", { name: "Before you sign" });
    expect(note.textContent).toContain("MacBook Pro takes this only if Touch ID signs it: it has not pinned iPhone, and refuses what that key signs.");
    expect(within(note).getByText(/pin --add/).textContent)
      .toBe(`python3 -I "$HOME/.proofpane-ade/ade_link.py" --dir "$HOME/.proofpane-ade/"'MacBook Pro' pin --add ${PHONE}`);
    expect(passkeys.get).not.toHaveBeenCalled();
  });

  it("says plainly when the session will refuse it whatever passkey signs it", async () => {
    serve(signRoutes(["9".repeat(32)]));
    await reviewed();
    expect(screen.getByRole("note", { name: "Before you sign" }).textContent)
      .toContain("MacBook Pro will refuse this: it has not pinned Touch ID and iPhone, the passkeys that can sign it.");
    expect(screen.getByText(/pin --add/).textContent).toContain(`pin --add ${TOUCH} --add ${PHONE}`);
  });

  it("where the session reports no pins, names the passkeys added since it was linked", async () => {
    useStore.setState({ links: { links: [link()], questions: [], public_url: "https://pp.example" } });
    serve(signRoutes([]));
    await reviewed();
    expect(screen.getByRole("note", { name: "Before you sign" }).textContent)
      .toContain("iPhone was added after MacBook Pro was linked: unless you gave MacBook Pro its pin line, it refuses what that key signs.");
  });

  it("says nothing where the session takes every passkey that can sign it", async () => {
    serve(signRoutes([TOUCH, PHONE]));
    await reviewed();
    expect(screen.queryByRole("note", { name: "Before you sign" })).toBeNull();
  });
});

describe("answering a question where it is read", () => {
  const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
    ({ seq, time: new Date().toISOString(), kind: "note", from: "MacBook Pro", to: "owner", ref: null, text: "", ...over });
  const question = ev(2, { kind: "question", text: "Should I push the sign-in branch?" });
  const routes = (open: boolean) => base({
    "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [question, ev(3, { mirror: "session", text: "Waiting for you." })] }],
    "GET /api/ade/link/sessions": sessions([link()], { questions: open ? [{ ...question, link_id: "L1" }] : [] }),
    "POST /api/ade/link/nonce": () => [200, { nonce: "n1", rp_id: "pp.example", deployment: "pp.example", account: "u", server_time: T }],
    "POST /api/ade/link/commands": () => [200, { seq: 9, expires_at: T + 3600 }],
  });

  it("offers Answer on an open question's row in the conversation, and signs an answer to that question", async () => {
    serve(routes(true));
    useStore.setState({ links: { links: [link()], questions: [{ ...question, link_id: "L1" }], public_url: "https://pp.example" } });
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    const row = (await screen.findByText("Should I push the sign-in branch?")).parentElement as HTMLElement;
    expect(row.textContent).toContain("MacBook Pro asks you");
    expect(screen.getByText(/To answer its question, use Answer on the question/)).toBeTruthy();
    fireEvent.click(within(row).getByRole("button", { name: /Answer/ }));
    fireEvent.change(within(row).getByLabelText("Your answer"), { target: { value: "Yes, push it." } });
    fireEvent.click(within(row).getByRole("button", { name: /Review the answer/ }));
    fireEvent.click(await within(row).findByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(sent("/api/ade/link/commands")).toHaveLength(1));
    const payload = sent("/api/ade/link/commands")[0].body?.payload as Record<string, unknown>;
    expect(payload).toMatchObject({ purpose: "answer", to: "L1", ref: 2, text: "Yes, push it." });
    expect(screen.getByLabelText("Message MacBook Pro")).toBeTruthy();     // the box under it stays an instruction
  });

  it("offers no Answer on a question no longer open", async () => {
    serve(routes(false));
    useStore.setState({ links: { links: [link()], questions: [], public_url: "https://pp.example" } });
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    await screen.findByText("Should I push the sign-in branch?");
    expect(screen.queryByRole("button", { name: /Answer/ })).toBeNull();
    expect(screen.queryByText(/To answer its question/)).toBeNull();
  });

  it("says in a machine's session where to reply to it, and opens its link's conversation to write from here", async () => {
    const s: HistorySession = { id: "s1", title: "Make sign-in work", project: "~/code/demo", branch: "sign-in", started_at: T - 7200,
      last_at: T - 3600, link_id: "L1", link: "MacBook Pro", kind: "local", listed_at: T - 60, notes: 1 };
    serve(base({ "GET /api/ade/link/timeline/L1?limit=100&session=s1": () => [200, { events: [ev(5, { mirror: "session", session: "s1", text: "Shall I push?" })] }] }));
    useStore.setState({ links: { links: [link()], questions: [], public_url: "https://pp.example" } });
    const talk = vi.fn();
    render(<SessionConversation s={s} onBack={() => {}} onClose={() => {}} onTalk={talk} />);
    await screen.findByText("Shall I push?");
    expect(screen.getByText(/To reply to it, open it again on MacBook Pro's machine, in its folder, and answer it there:/)).toBeTruthy();
    expect(screen.getByText("cd ~/'code/demo' && claude --resume 's1'")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Message MacBook Pro/ }));
    expect(talk).toHaveBeenCalledWith("L1");
  });

  it("goes from a machine's session to its link's conversation in Linked sessions", async () => {
    const s: HistorySession = { id: "s1", title: "Make sign-in work", project: "~/code/demo", branch: "sign-in", started_at: T - 7200,
      last_at: T - 3600, link_id: "L1", link: "MacBook Pro", kind: "local", listed_at: T - 60, notes: 1 };
    serve(base({
      "GET /api/ade/link/timeline/L1?limit=100&session=s1": () => [200, { events: [] }],
      "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [] }],
    }));
    render(<Channel onClose={() => {}} reading={s} />);
    fireEvent.click(await screen.findByRole("button", { name: /Message MacBook Pro/ }));
    expect(await screen.findByLabelText("Message MacBook Pro")).toBeTruthy();
  });
});

describe("a hold, in Linked sessions", () => {
  const ask = (over: Partial<LinkAsk> = {}): LinkAsk => ({
    id: "A1", name: "Cloud planner", kind: "cloud", fingerprint: "0123456789abcdef0123456789abcdef", asked_from: "203.0.113.7",
    created_at: T - 60, expires_at: T + 1740, session_ref: null, state: "held", hold_id: "H1", takes_effect_at: T + 42,
    told: 0, failed: null, ...over,
  });
  const heldMove = (over: Partial<LinkMove> = {}): LinkMove => ({
    seq: null, hold_id: "H2", link_id: "L1", session: "s1", state: "held", created_at: T, expires_at: T + 3600,
    takes_effect_at: T + 42, told: 1, reason: null, title: "Fix the sign-in", link: "MacBook Pro", ...over,
  });

  it("says when it takes effect as a countdown and the time it ends", () => {
    expect(untilWords(T + 42, T * 1000)).toBe(`Takes effect in 0:42 (${clock(T + 42)})`);
    expect(untilWords(T + 3723, T * 1000)).toBe(`Takes effect in 1:02:03 (${clock(T + 3723)})`);
    expect(untilWords(T + 42, (T + 42) * 1000)).toBe(`Taking effect now (${clock(T + 42)})`);
  });

  it("counts down on its card, second by second", async () => {
    const at = Math.floor(Date.now() / 1000) + 42;
    serve(base({ "GET /api/ade/link/sessions": sessions([link()], { asks: [ask({ takes_effect_at: at })] }) }));
    render(<Channel onClose={() => {}} />);
    const count = await screen.findByText(/^Takes effect in /);
    const first = count.textContent;
    expect(first).toMatch(new RegExp(`^Takes effect in 0:(4[0-2]|3\\d) \\(${clock(at)}\\)$`));
    await act(async () => { await new Promise((r) => setTimeout(r, 1100)); });
    expect(screen.getByText(/^Takes effect in /).textContent).not.toBe(first);
  });

  it("calls a move the owner signed signed, never approved", async () => {
    serve(base({ "GET /api/ade/link/sessions": sessions([link()], { held_moves: [heldMove()] }) }));
    render(<Channel onClose={() => {}} />);
    const card = (await screen.findByText(/^You signed moving/)).closest("div.rounded-lg") as HTMLElement;
    expect(card.textContent).toContain("You signed moving “Fix the sign-in” from MacBook Pro into the workspace.");
    expect(card.textContent).not.toMatch(/Approved/);
    expect(moveWords(heldMove({ seq: 31 }), "MacBook Pro")).toMatch(/^Signed: /);
  });

  it("confirms a cancelled approval, saying the session won't be linked", async () => {
    let asks = [ask()];
    serve(base({
      "GET /api/ade/link/sessions": () => sessions([link()], { asks })(undefined),
      "POST /api/ade/holds/H1/cancel": () => { asks = []; return [200, { cancelled_at: T }]; },
    }));
    render(<Channel onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    const said = await screen.findByText("Cancelled — “Cloud planner” won't be linked.");
    expect(said.closest("[role=status]")).toBeTruthy();
    await vi.waitFor(() => expect(screen.queryByText(/is linked unless/)).toBeNull());
    expect(screen.getByText("Cancelled — “Cloud planner” won't be linked.")).toBeTruthy();       // it stays said
  });

  it("confirms a cancelled move, saying the session won't move", async () => {
    let moves = [heldMove()];
    serve(base({
      "GET /api/ade/link/sessions": () => sessions([link()], { held_moves: moves })(undefined),
      "POST /api/ade/holds/H2/cancel": () => { moves = []; return [200, { cancelled_at: T }]; },
    }));
    render(<Channel onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("Cancelled — “Fix the sign-in” won't move into the workspace.")).toBeTruthy();
  });
});

describe("moving a session into the workspace", () => {
  const session = (over: Partial<HistorySession> = {}): HistorySession => ({
    id: "s1", title: "Make sign-in work", project: "~/code/demo", branch: "sign-in", started_at: T - 7200, last_at: T - 3600,
    link_id: "L1", link: "MacBook Pro", kind: "local", listed_at: T - 60, ...over,
  });
  const move = (over: Partial<LinkMove> = {}): LinkMove => ({
    seq: 31, link_id: "L1", session: "s1", state: "started", created_at: T - 600, expires_at: T + 3000, taken_at: T - 500,
    uploaded_at: T - 400, updated_at: T - 300, workspace_session: "0123456789ab", reason: null, ...over,
  });
  const running = (repos: { name: string; missing?: boolean }[]) =>
    useStore.setState({ ws: { enabled: true, allowed: true, running: true } as never, repos: repos as never });
  const moveButton = () => screen.queryByRole("button", { name: /Move to the workspace|Move it again/ });

  it("offers no second move once it started in the workspace, unless it ran on its machine since", async () => {
    serve(base({ "GET /api/ade/link/moves?session=s1": () => [200, { moves: [move()] }] }));
    running([{ name: "demo" }]);
    const { unmount } = render(<MoveToWorkspace s={session()} onOpen={() => {}} />);
    expect(await screen.findByText("Started in the workspace: it continues there.")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Open it in the workspace/ })).toBeTruthy();
    expect(moveButton()).toBeNull();
    unmount();
    render(<MoveToWorkspace s={session({ last_at: T - 60 })} onOpen={() => {}} />);
    expect(await screen.findByRole("button", { name: "Move it again" })).toBeTruthy();
    expect(screen.getByText("It ran on MacBook Pro's machine after it was moved.")).toBeTruthy();
  });

  it("marks a moved session's row in Linked sessions, and one whose move is under way", async () => {
    serve(base({
      "GET /api/ade/link/history": () => [200, { sessions: [session(), session({ id: "s2", title: "Speed up the tests" }), session({ id: "s3", title: "Landing page" })] }],
      "GET /api/ade/link/moves": () => [200, { moves: [move({ session: "s2", seq: 40, state: "asked" }), move(), move({ session: "s3", seq: 20, state: "failed" })] }],
    }));
    render(<Channel onClose={() => {}} />);
    const row = (title: string) => (screen.getByText(title).closest("li") as HTMLElement);
    await screen.findByText("Make sign-in work");
    await vi.waitFor(() => expect(within(row("Make sign-in work")).queryByText("In the workspace")).toBeTruthy());
    expect(within(row("Speed up the tests")).getByText("Moving")).toBeTruthy();
    expect(within(row("Landing page")).queryByText(/In the workspace|Moving/)).toBeNull();
  });

  it("says before anything is signed when its repository is not in the workspace, naming it", async () => {
    serve(base({ "GET /api/ade/link/moves?session=s1": () => [200, { moves: [] }] }));
    running([{ name: "website" }, { name: "demo", missing: true }]);
    const { unmount } = render(<MoveToWorkspace s={session()} onOpen={() => {}} />);
    const button = await screen.findByRole("button", { name: /Move to the workspace/ }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByText("This session's repository, demo (~/code/demo), is not in your workspace: add it there first "
      + "(Add a repository), then move the session.")).toBeTruthy();
    unmount();
    for (const repos of [[{ name: "Demo" }], []]) {          // there; or its list not read yet: the server holds it then
      running(repos);
      const { unmount: off } = render(<MoveToWorkspace s={session()} onOpen={() => {}} />);
      expect(((await screen.findByRole("button", { name: /Move to the workspace/ })) as HTMLButtonElement).disabled).toBe(false);
      off();
    }
    expect(repositoryMissing(session({ repositories: ["~/code/lib"] }), [{ name: "demo" }])).toMatch(/^This session's repository, lib \(~\/code\/lib\)/);
  });
});

describe("a two-step action, from the keyboard", () => {
  /** Whether a polite live region on the page says `text`. */
  const sayIt = async (text: string) => vi.waitFor(() =>
    expect(screen.getAllByRole("status").filter((el) => el.getAttribute("aria-live") === "polite").map((el) => el.textContent).join("\n")).toContain(text));
  const word = {
    "GET /api/account/confirm": (): Answer => [200, { needed: true, password: true, passkeys: 0 }],
    "POST /api/account/confirm": (b: Record<string, unknown> | undefined): Answer => (b?.password === "right one" ? [200, { confirmation: "c7" }] : [403, { detail: "no" }]),
  };

  it("making a ticket: the account's word, then Copy, each taking the keyboard and said", async () => {
    const ends = Math.floor(Date.now() / 1000) + 600;
    serve(base({ ...word, "POST /api/ade/link/tickets": () => [200, { ticket: "PP-ABCDEFGH", expires_at: ends, url: "https://pp.example", keys: [] }] }));
    render(<Channel onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Link a session/ }));
    fireEvent.change(screen.getByLabelText("What to call it"), { target: { value: "Studio" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    // the step comes after a read, so the keyboard reaches it as its effects run, which may be after it is found
    const password = await screen.findByLabelText("Your password");
    await vi.waitFor(() => expect(document.activeElement).toBe(password));
    await sayIt("Linking “Studio”: confirm it, and the ticket is made.");
    fireEvent.change(password, { target: { value: "right one" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    const copy = await screen.findByRole("button", { name: /Copy the line/ });
    await vi.waitFor(() => expect(document.activeElement).toBe(copy));
    await sayIt(`The ticket is made. Copy the line and run it on this Mac, in a terminal in the folder the session runs in (in the Claude Code session itself, in the background: it waits out the hold); the ticket works once, until ${clock(ends)}.`);
  });

  it("signing: Review takes the keyboard to Sign and says what is signed; once sent, back to the words", async () => {
    serve(base({
      "POST /api/ade/link/nonce": () => [200, { nonce: "n1", rp_id: "pp.example", deployment: "pp.example", account: "u", server_time: T }],
      "POST /api/ade/link/commands": () => [200, { seq: 9, expires_at: T + 3600 }],
    }));
    render(<SignBox purpose="instruction" to="L1" toName="MacBook Pro" placeholder="What MacBook Pro should do" submit="Review" onDone={async () => {}} />);
    const box = screen.getByLabelText("What MacBook Pro should do");
    fireEvent.change(box, { target: { value: "run the tests" } });
    fireEvent.click(screen.getByRole("button", { name: /Review/ }));
    const sign = await screen.findByRole("button", { name: /Sign with your passkey/ });
    await vi.waitFor(() => expect(document.activeElement).toBe(sign));
    await sayIt(`You are signing an instruction to MacBook Pro (link L1), good until ${dayTime(T + 3600)}. Sign with your passkey to send it.`);
    fireEvent.click(sign);
    await sayIt("Signed: your instruction went to MacBook Pro.");
    await vi.waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("What MacBook Pro should do")));
  });

  it("approving a request: the account's word, then its hold, each taking the keyboard and said", async () => {
    const at = Math.floor(Date.now() / 1000) + 180;
    const ask: LinkAsk = { id: "A1", name: "cloud", kind: "cloud", fingerprint: "0123456789abcdef0123456789abcdef", asked_from: null,
      created_at: T - 60, expires_at: T + 1740, session_ref: null, state: "pending", hold_id: null, takes_effect_at: null, told: null, failed: null };
    let asks = [ask];
    serve(base({
      ...word,
      "GET /api/ade/link/sessions": () => sessions([], { asks })(undefined),
      "POST /api/ade/link/asks/A1/approve": () => { asks = [{ ...ask, state: "held", hold_id: "H1", takes_effect_at: at, told: 0 }]; return [200, { hold: { id: "H1", takes_effect_at: at }, told: 0 }]; },
    }));
    render(<Channel onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    const password = await screen.findByLabelText("Your password");
    await vi.waitFor(() => expect(document.activeElement).toBe(password));
    await sayIt("Approving “cloud”: confirm it, and its hold begins.");
    fireEvent.change(password, { target: { value: "right one" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    const held = await screen.findByRole("group", { name: "Approved: cloud" });
    await vi.waitFor(() => expect(document.activeElement).toBe(held));
    await sayIt(`Approved: “cloud” is linked unless you cancel it first. Takes effect in `);
    await sayIt(`(${clock(at)}).`);
  });

  it("signing a move: Review the move, then Sign, then where it stands, each taking the keyboard and said", async () => {
    const at = Math.floor(Date.now() / 1000) + 180;
    let listed: LinkMove[] = [];
    serve(base({
      "GET /api/ade/link/moves?session=s1": () => [200, { moves: listed }],
      "POST /api/ade/link/nonce": () => [200, { nonce: "n1", rp_id: "pp.example", deployment: "pp.example", account: "u", server_time: T }],
      "POST /api/ade/link/commands": () => {
        listed = [{ seq: null, hold_id: "H1", link_id: "L1", session: "s1", state: "held", created_at: T, expires_at: T + 3600, takes_effect_at: at, told: 0, reason: null }];
        return [200, { held: { id: "H1", takes_effect_at: at, told: 0 }, expires_at: T + 3600 }];
      },
    }));
    useStore.setState({ ws: { enabled: true, allowed: true, running: true } as never, repos: [{ name: "demo" }] as never,
      links: { links: [link()], questions: [], public_url: "https://pp.example", move_hold: 180 } });
    const s: HistorySession = { id: "s1", title: "Make sign-in work", project: "~/code/demo", branch: "sign-in", started_at: T - 7200,
      last_at: T - 3600, link_id: "L1", link: "MacBook Pro", kind: "local", listed_at: T - 60 };
    render(<MoveToWorkspace s={s} onOpen={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Move to the workspace/ }));
    expect(document.activeElement).toBe(screen.getByRole("button", { name: /Review the move/ }));
    await sayIt("Review the move, then sign it.");
    fireEvent.click(screen.getByRole("button", { name: /Review the move/ }));
    const sign = await screen.findByRole("button", { name: /Sign with your passkey/ });
    await vi.waitFor(() => expect(document.activeElement).toBe(sign));
    await sayIt("You are signing a move of session s1 into the workspace, for MacBook Pro (link L1) to take until");
    await sayIt("Sign with your passkey to send it.");
    fireEvent.click(sign);
    await sayIt("Signed: MacBook Pro's machine may take it in 3 minutes, unless you cancel it first.");
    await vi.waitFor(() => expect(document.activeElement).toBe(screen.getByRole("group", { name: "This session's move" })));
  });
});

describe("the machine's sessions, and what to do where there are none", () => {
  const listed = (over: Partial<HistorySession> = {}): HistorySession => ({
    id: "s1", title: "Make sign-in work", project: "~/code/demo", branch: "main", started_at: T - 7200, last_at: T - 3600,
    link_id: "L1", link: "MacBook Pro", kind: "local", listed_at: T - 60, ...over,
  });

  const read = () => calls.filter((c) => c.path === "/api/ade/link/history").length;

  it("reads the machine's session list again with Read again", async () => {
    let listing: HistorySession[] = [];
    serve(base({ "GET /api/ade/link/history": () => [200, { sessions: listing }] }));
    render(<Channel onClose={() => {}} />);
    await screen.findByText("MacBook Pro", { selector: "bdi" });
    await vi.waitFor(() => expect(read()).toBeGreaterThan(0));
    listing = [listed()];
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    expect(await screen.findByText("Make sign-in work")).toBeTruthy();
  });

  it("shows a new link's sessions as the page's store reads them again, reading nothing itself", async () => {
    serve(base({ "GET /api/ade/link/moves": () => [200, { moves: [] }] }));
    useStore.setState({ links: { links: [link()], questions: [], public_url: "https://pp.example" }, history: [] });
    render(<Channel onClose={() => {}} />);
    await screen.findByText("MacBook Pro", { selector: "bdi" });
    // the summary saw both lists' stamps move: the store reads them again, and the panel shows what it read
    act(() => {
      useStore.setState({ links: { links: [link(), link({ id: "L2", name: "Studio", machine: "m2" })], questions: [], public_url: "https://pp.example" },
        history: [listed({ link_id: "L2", link: "Studio", id: "s9", title: "On the studio" })] });
    });
    expect(await screen.findByText("On the studio")).toBeTruthy();
    expect(read()).toBe(0);
    expect(calls.filter((c) => c.path === "/api/ade/link/sessions")).toHaveLength(0);
  });

  it("says what to do in place, never \"see the README\"", async () => {
    serve(base({
      "GET /api/ade/link/sessions": sessions([]),
      "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [] }],
      "GET /api/ade/link/timeline/L1?limit=100&session=s1": () => [200, { events: [] }],
    }));
    const { unmount } = render(<Channel onClose={() => {}} />);
    expect(await screen.findByRole("button", { name: /Link your first session/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Link your first session/ }));
    expect(screen.getByLabelText("What to call it")).toBeTruthy();
    unmount();
    render(<MachineSessions sessions={[]} link="MacBook Pro" />);
    expect(screen.getByText(/printf/).textContent).toBe(`printf '%s\\n' '{"projects": "all"}' > "$HOME/.proofpane-ade/MacBook Pro"/history.json `
      + `&& python3 -I "$HOME/.proofpane-ade/ade_link.py" --dir "$HOME/.proofpane-ade/MacBook Pro" history`);
    cleanup();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    const settings = JSON.parse((await screen.findByLabelText("The settings to add")).textContent ?? "");
    expect(settings.hooks.Stop[0].hooks[0].command)
      .toBe(`python3 -I -X utf8 "$HOME/.proofpane-ade/ade_link.py" --dir "$HOME/.proofpane-ade/MacBook Pro" mirror reply || true`);
    cleanup();
    render(<SessionConversation s={listed()} onBack={() => {}} onClose={() => {}} />);
    expect(await screen.findByText(/To send its conversation here, add "conversations": true to ~\/\.proofpane-ade\/MacBook Pro\/history\.json on MacBook Pro's machine/)).toBeTruthy();
    cleanup();
    render(<MachineSessions sessions={[listed()]} />);
    fireEvent.click(screen.getByText("Make sign-in work"));
    expect(screen.getByText(/to send it, add "conversations": true to ~\/\.proofpane-ade\/MacBook Pro\/history\.json/)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/README/);
  });

  it("says to merge the mirror's block into a settings file, never a second \"hooks\" beside the first", async () => {
    serve(base({ "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [] }] }));
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    const shown = await screen.findByLabelText("The settings to add");
    expect(shown.previousElementSibling?.textContent).toBe("To see its conversation here too, add this to "
      + ".claude/settings.local.json in its folder: each turn is posted here as it runs and as it ends. If the file "
      + "already has settings, put this \"hooks\" inside its outer braces. If it has a \"hooks\" already, put this "
      + "\"Stop\" and this \"PreToolUse\" inside that one instead; where it has either already, add the one entry from "
      + "this one's list to that list. A settings file takes one \"hooks\": a second beside the first is still valid "
      + "JSON, but a reader such as JavaScript's own keeps only the last, so the first one's hooks would be dropped with "
      + "nothing said.");
    const mirror = JSON.parse(shown.textContent ?? "");
    expect(Object.keys(mirror)).toEqual(["hooks"]);
    expect(Object.keys(mirror.hooks)).toEqual(["Stop", "PreToolUse"]);      // the live view's hook too
    expect(mirror.hooks.Stop).toHaveLength(1);                     // "the one entry from this one's list"
    expect(mirror.hooks.PreToolUse).toHaveLength(1);
    // what the words say of a second "hooks", with JavaScript's own reader, which they name: the link block's hooks
    // and this block's pasted as two "hooks" in one file parse, and keep only the last, the first's hook on every tool
    // ("*") among what is dropped
    const held = JSON.parse(readFileSync(join(__dirname, "..", "..", "..", "ade_link.settings.json"), "utf8")).hooks;
    expect(held.PreToolUse.map((b: { matcher: string }) => b.matcher)).toContain("*");
    const pasted = JSON.parse(`{"hooks": ${JSON.stringify(held)}, "hooks": ${JSON.stringify(mirror.hooks)}}`);
    expect(pasted.hooks).toEqual(mirror.hooks);
  });

  it("speaks of a session in the cloud as one in the cloud", async () => {
    render(<MachineSessions sessions={[]} link="Planner" cloud />);
    expect(document.body.textContent).toBe("Your other claude.ai sessions are not listed here: claude.ai lists them. This one's conversation is under Open conversation, above.");
    expect(document.body.textContent).not.toMatch(/VS Code|terminal|history\.json/);
    cleanup();
    serve(base({ "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [] }] }));
    render(<Conversation l={link({ kind: "cloud", name: "Planner" })} onBack={() => {}} onClose={() => {}} />);
    expect(await screen.findByText(/A session in the cloud shows here what it replies to you and what it asks you/)).toBeTruthy();
    expect(screen.queryByLabelText("The settings to add")).toBeNull();
  });
});

describe("Linked sessions on a phone", () => {
  it("fills a 390-pixel screen, its Close first and touch-sized in a header held at the top, its actions a row below", async () => {
    screenWide(390);
    expect(matchMedia(NARROW).matches).toBe(true);
    serve(base());
    const closed = vi.fn();
    render(<Channel onClose={closed} />);
    const dialog = screen.getByRole("dialog", { name: "Linked sessions" });
    expect(dialog.className).toMatch(/\binset-0\b/);
    expect(dialog.className).toMatch(/\bw-full\b/);
    expect(dialog.className).not.toMatch(/\bright-2\b|\btop-12\b|w-\[min\(/);
    const close = screen.getByRole("button", { name: "Close Linked sessions" });
    expect(focusables(dialog)[0]).toBe(close);               // the first thing reached, and the first seen
    expect(close.className).toContain("!h-11");                // 44 pixels, for a thumb
    const header = close.closest(".sticky") as HTMLElement;
    expect(header).toBeTruthy();
    const firstRow = close.parentElement as HTMLElement;
    const link = screen.getByRole("button", { name: /Link a session/ });
    const ask = screen.getByRole("button", { name: /Ask address/ });
    expect(firstRow.contains(link) || firstRow.contains(ask)).toBe(false);
    expect(link.parentElement).toBe(ask.parentElement);
    expect(header.contains(link)).toBe(true);
    fireEvent.click(close);
    expect(closed).toHaveBeenCalledTimes(1);
  });

  it("is the panel at the top right on a wide screen, as before", () => {
    screenWide(1440);
    serve(base());
    render(<Channel onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: "Linked sessions" });
    expect(dialog.className).toMatch(/\bright-2\b/);
    expect(dialog.className).not.toMatch(/\binset-0\b/);
  });
});

describe("opened on linking a session, as the shell's Link your Mac opens it", () => {
  it("asks the session's name first, on this Mac unless said otherwise, the keyboard in its field; Cancel leaves the list", async () => {
    serve(base());
    render(<Channel onClose={() => {}} linking />);
    const form = screen.getByRole("form", { name: "Link a session" });
    expect(document.activeElement).toBe(within(form).getByLabelText("What to call it"));
    expect((within(form).getByRole("radio", { name: /This Mac/ }) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(within(form).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("form", { name: "Link a session" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "Linked sessions" })).toBeTruthy();
    expect(await screen.findByText("MacBook Pro", { selector: "bdi" })).toBeTruthy();
  });

  it("opens on its list otherwise, and on a machine's session when one is given", async () => {
    serve(base({ "GET /api/ade/link/history/L1/s1": () => [200, { talk: null }] }));
    const { unmount } = render(<Channel onClose={() => {}} />);
    expect(screen.queryByRole("form", { name: "Link a session" })).toBeNull();
    unmount();
    const s: HistorySession = { id: "s1", title: "Make sign-in work", project: "~/code/demo", branch: "main", started_at: T - 7200,
      last_at: T - 3600, link_id: "L1", link: "MacBook Pro", kind: "local", listed_at: T - 60 };
    render(<Channel onClose={() => {}} reading={s} linking />);
    expect(screen.queryByRole("form", { name: "Link a session" })).toBeNull();
    expect(screen.getByRole("button", { name: "Back to the linked sessions" })).toBeTruthy();
  });
});

describe("Linked sessions, read through the page's store", () => {
  const machineSession = (over: Partial<HistorySession> = {}): HistorySession => ({
    id: "s1", title: "Make sign-in work", project: "~/code/demo", branch: "main", started_at: T - 7200, last_at: T - 3600,
    link_id: "L1", link: "MacBook Pro", kind: "local", listed_at: T - 60, ...over,
  });
  const reads = (path: string) => calls.filter((c) => c.method === "GET" && c.path === path).length;
  /** The lists as the shell's summary had the store read them. */
  const kept = (history: HistorySession[] = [machineSession()]) =>
    useStore.setState({ links: { links: [link()], questions: [], public_url: "https://pp.example", move_hold: 180 }, history });

  it("shows the lists the store keeps, and reads neither on a timer of its own", async () => {
    vi.useFakeTimers();
    try {
      serve(base({ "GET /api/ade/link/moves": () => [200, { moves: [] }] }));
      kept();
      render(<Channel onClose={() => {}} />);
      await act(async () => { await vi.advanceTimersByTimeAsync(100); });
      expect(screen.getByText("Make sign-in work")).toBeTruthy();
      expect(reads("/api/ade/link/moves")).toBe(1);                 // where its moves left it, read as it opens
      await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
      expect([reads("/api/ade/link/sessions"), reads("/api/ade/link/history"), reads("/api/ade/link/moves")]).toEqual([0, 0, 1]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("reads a list the store has not read yet once, as it opens, and not again", async () => {
    vi.useFakeTimers();
    try {
      serve(base());
      render(<Channel onClose={() => {}} />);
      await act(async () => { await vi.advanceTimersByTimeAsync(100); });
      expect(screen.getByText("MacBook Pro", { selector: "bdi" })).toBeTruthy();
      expect([reads("/api/ade/link/sessions"), reads("/api/ade/link/history")]).toEqual([1, 1]);
      await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
      expect([reads("/api/ade/link/sessions"), reads("/api/ade/link/history")]).toEqual([1, 1]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("has the store read both lists at once on Read again, whatever their stamps, and the moves", async () => {
    serve(base({
      "GET /api/ade/link/history": () => [200, { sessions: [machineSession(), machineSession({ id: "s2", title: "Speed up the tests" })] }],
      "GET /api/ade/link/moves": () => [200, { moves: [] }],
    }));
    kept();
    render(<Channel onClose={() => {}} />);
    await screen.findByText("Make sign-in work");
    await vi.waitFor(() => expect(reads("/api/ade/link/moves")).toBe(1));
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    expect(await screen.findByText("Speed up the tests")).toBeTruthy();
    expect(useStore.getState().history?.map((s) => s.id)).toEqual(["s1", "s2"]);
    expect([reads("/api/ade/link/sessions"), reads("/api/ade/link/history")]).toEqual([1, 1]);
    expect(reads("/api/ade/link/moves")).toBeGreaterThan(1);
  });

  it("reads where moves stand again as the store reads either list again, and marks the rows by it", async () => {
    let moves: LinkMove[] = [];
    serve(base({ "GET /api/ade/link/moves": () => [200, { moves }] }));
    kept();
    render(<Channel onClose={() => {}} />);
    const row = () => screen.getByText("Make sign-in work").closest("li") as HTMLElement;
    await vi.waitFor(() => expect(reads("/api/ade/link/moves")).toBe(1));
    expect(within(row()).queryByText(/In the workspace|Moving/)).toBeNull();
    // the session moved meanwhile, and its machine's list changed with it: the summary had the store read it again
    moves = [{ seq: 40, link_id: "L1", session: "s1", state: "started", created_at: T - 600, expires_at: T + 3000,
      workspace_session: "0123456789ab", reason: null } as LinkMove];
    act(() => { useStore.setState({ history: [machineSession({ listed_at: T })] }); });
    await vi.waitFor(() => expect(within(row()).getByText("In the workspace")).toBeTruthy(), { timeout: 5000 });
    expect(reads("/api/ade/link/moves")).toBe(2);
    // and so for the linked sessions' list: a move's hold that ended is in it
    moves = [];
    act(() => { useStore.setState({ links: { links: [link()], questions: [], public_url: "https://pp.example", move_hold: 180 } }); });
    await vi.waitFor(() => expect(within(row()).queryByText("In the workspace")).toBeNull(), { timeout: 5000 });
    expect(reads("/api/ade/link/moves")).toBe(3);
  });

  it("reads no moves while no machine's session is listed: none of them could have moved", async () => {
    serve(base({ "GET /api/ade/link/moves": () => [200, { moves: [] }] }));
    useStore.setState({ links: { links: [link()], questions: [], public_url: "https://pp.example" }, history: [] });
    render(<Channel onClose={() => {}} />);
    await screen.findByText("MacBook Pro", { selector: "bdi" });
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));               // both lists come again
    await vi.waitFor(() => expect([reads("/api/ade/link/sessions"), reads("/api/ade/link/history")]).toEqual([1, 1]));
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    expect(reads("/api/ade/link/moves")).toBe(0);
  });

  it("reads where moves stand again back from a session's view, where it may have been moved", async () => {
    let moves: LinkMove[] = [];
    serve(base({ "GET /api/ade/link/moves": () => [200, { moves }] }));
    kept();
    render(<Channel onClose={() => {}} reading={machineSession()} />);
    await vi.waitFor(() => expect(reads("/api/ade/link/moves")).toBe(1));
    moves = [{ seq: 40, link_id: "L1", session: "s1", state: "started", created_at: T - 600, expires_at: T + 3000,
      workspace_session: "0123456789ab", reason: null } as LinkMove];
    fireEvent.click(screen.getByRole("button", { name: "Back to the linked sessions" }));
    const row = () => screen.getByText("Make sign-in work").closest("li") as HTMLElement;
    await vi.waitFor(() => expect(within(row()).getByText("In the workspace")).toBeTruthy(), { timeout: 5000 });
    expect(reads("/api/ade/link/moves")).toBe(2);
  });

  it("reads where moves stand again as the workspace's sessions change while one is under way, and only then", async () => {
    let moves: LinkMove[] = [{ seq: 40, link_id: "L1", session: "s1", state: "uploaded", created_at: T - 600, expires_at: T + 3000,
      reason: null } as LinkMove];
    serve(base({ "GET /api/ade/link/moves": () => [200, { moves }] }));
    kept();
    render(<Channel onClose={() => {}} />);
    const row = () => screen.getByText("Make sign-in work").closest("li") as HTMLElement;
    await vi.waitFor(() => expect(within(row()).getByText("Moving")).toBeTruthy(), { timeout: 5000 });
    const before = reads("/api/ade/link/moves");
    moves = [{ ...moves[0], state: "started", workspace_session: "0123456789ab" }];
    act(() => { useStore.setState({ records: [{ id: "0123456789ab" } as never] }); });   // the moved session starts there
    await vi.waitFor(() => expect(within(row()).getByText("In the workspace")).toBeTruthy(), { timeout: 5000 });
    expect(reads("/api/ade/link/moves")).toBe(before + 1);
    act(() => { useStore.setState({ records: [] }); });                                    // none under way now: not read
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    expect(reads("/api/ade/link/moves")).toBe(before + 1);
  });

  it("misses no change of the workspace's sessions that comes as a move is first shown under way", async () => {
    let moves: LinkMove[] = [{ seq: 40, link_id: "L1", session: "s1", state: "uploaded", created_at: T - 600, expires_at: T + 3000,
      reason: null } as LinkMove];
    serve(base({ "GET /api/ade/link/moves": () => [200, { moves }] }));
    kept();
    // the moment the page first says Moving (as its render is put on the page, before anything runs after it), the
    // moved session starts in the workspace: its move is started, and the workspace's sessions change
    let changed = false;
    const watch = new MutationObserver(() => {
      if (changed || !document.body.textContent?.includes("Moving")) return;
      changed = true;
      moves = [{ ...moves[0], state: "started", workspace_session: "0123456789ab" }];
      useStore.setState({ records: [{ id: "0123456789ab" } as never] });
    });
    watch.observe(document.body, { childList: true, subtree: true, characterData: true });
    try {
      render(<Channel onClose={() => {}} />);
      await vi.waitFor(() => expect(changed).toBe(true), { timeout: 5000 });
      await vi.waitFor(() => expect(screen.getByText("In the workspace")).toBeTruthy(), { timeout: 5000 });
    } finally {
      watch.disconnect();
    }
  }, 15000);
});

describe("while the page cannot reach its server", () => {
  const offline = (on: boolean) => act(() => { useStore.setState({ offline: on }); });
  /** What a control's description says (aria-describedby). */
  const described = (el: HTMLElement) => (el.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent ?? "").join(" ");

  it("keeps Review and Sign where the keyboard reaches them, unavailable and saying why, and signs nothing", async () => {
    serve(base({
      "POST /api/ade/link/nonce": () => [200, { nonce: "n1", rp_id: "pp.example", deployment: "pp.example", account: "u", server_time: T }],
      "POST /api/ade/link/commands": () => [200, { seq: 9, expires_at: T + 3600 }],
    }));
    useStore.setState({ offline: true });
    render(<SignBox purpose="instruction" to="L1" toName="MacBook Pro" placeholder="What MacBook Pro should do" submit="Review" onDone={async () => {}} />);
    fireEvent.change(screen.getByLabelText("What MacBook Pro should do"), { target: { value: "run the tests" } });
    const review = screen.getByRole("button", { name: /Review/ });
    expect(review.getAttribute("aria-disabled")).toBe("true");
    expect(described(review)).toBe(SIGN_OFFLINE);
    fireEvent.click(review);
    expect(sent("/api/ade/link/nonce")).toHaveLength(0);
    offline(false);
    expect(review.getAttribute("aria-disabled")).toBeNull();
    fireEvent.click(review);
    const sign = await screen.findByRole("button", { name: /Sign with your passkey/ });
    await vi.waitFor(() => expect(document.activeElement).toBe(sign));
    offline(true);                                     // lost after the review: Sign keeps the keyboard, and says why
    expect(sign.getAttribute("aria-disabled")).toBe("true");
    expect((sign as HTMLButtonElement).disabled).toBe(false);
    expect(document.activeElement).toBe(sign);
    expect(described(sign)).toBe(SIGN_OFFLINE);
    fireEvent.click(sign);
    expect(passkeys.get).not.toHaveBeenCalled();
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
    offline(false);
    fireEvent.click(sign);
    await vi.waitFor(() => expect(sent("/api/ade/link/commands")).toHaveLength(1));
  });

  it("keeps a hold's Cancel unavailable, saying the hold runs on, and cancels nothing: a request's, a move's, the session's own", async () => {
    serve(base({
      "POST /api/ade/holds/H1/cancel": () => [200, { cancelled_at: T }],
      "GET /api/ade/link/moves?session=s1": () => [200, { moves: [{ seq: null, hold_id: "H1", link_id: "L1", session: "s1", state: "held",
        created_at: T, expires_at: T + 3600, takes_effect_at: T + 120, told: 0, reason: null }] }],
    }));
    useStore.setState({ offline: true, ws: { enabled: true, allowed: true, running: true } as never, repos: [{ name: "demo" }] as never });
    const ask: LinkAsk = { id: "A1", name: "cloud", kind: "cloud", fingerprint: "0123456789abcdef0123456789abcdef", asked_from: null,
      created_at: T - 60, expires_at: T + 1740, session_ref: null, state: "held", hold_id: "H1", takes_effect_at: T + 120, told: 0, failed: null };
    const held = { seq: null, hold_id: "H1", link_id: "L1", link: "MacBook Pro", session: "s1", title: "Make sign-in work", state: "held",
      created_at: T, expires_at: T + 3600, takes_effect_at: T + 120, told: 0, reason: null } as unknown as LinkMove;
    const s: HistorySession = { id: "s1", title: "Make sign-in work", project: "~/code/demo", branch: "main", started_at: T - 7200,
      last_at: T - 3600, link_id: "L1", link: "MacBook Pro", kind: "local", listed_at: T - 60 };
    for (const card of [<AskCard a={ask} links={[]} twins={1} />, <HeldMoveCard m={held} />, <MoveToWorkspace s={s} onOpen={() => {}} />]) {
      const { unmount } = render(card);
      const cancel = await screen.findByRole("button", { name: "Cancel" });
      expect(cancel.getAttribute("aria-disabled")).toBe("true");
      expect(described(cancel)).toBe(CANCEL_OFFLINE);
      fireEvent.click(cancel);
      expect(sent("/api/ade/holds/H1/cancel")).toHaveLength(0);
      unmount();
    }
    useStore.setState({ offline: false });
    render(<HeldMoveCard m={held} />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await vi.waitFor(() => expect(sent("/api/ade/holds/H1/cancel")).toHaveLength(1));
  });

  it("is said in Linked sessions as it happens, whose dialog hides the page's own line, and unsaid once it is back", () => {
    serve(base());
    render(<Channel onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: "Linked sessions" });
    const before = within(dialog).getAllByRole("status");
    expect(dialog.textContent).not.toContain(PANEL_OFFLINE);
    offline(true);
    const saying = within(dialog).getAllByRole("status").find((el) => el.textContent === PANEL_OFFLINE);
    expect(saying?.getAttribute("aria-live")).toBe("polite");
    expect(before).toContain(saying);                  // there from the start, so its words are said as they come
    offline(false);
    expect(saying?.textContent).toBe("");
  });
});

describe("the lines it gives to run, made only from values in the form the server writes them", () => {
  // the review's probe: what a server that is not the ADE could send, run as a command once its line is pasted
  const OWNED = "$(touch /tmp/pp-owned)";
  const URL = "https://pp.example", TICKET = "PP-ABCDEFGH", ASK = `ask-${"c".repeat(20)}`, FP = "a".repeat(32);
  const mac = { name: "mac", kind: "local" as const };
  const out = (over: Partial<TicketOut> = {}): TicketOut => ({ ticket: TICKET, expires_at: T + 600, url: URL, ...over });

  it("gives a pair line only for an address and a ticket in the server's forms", () => {
    expect(pairLine(out(), mac)).toBe(`${CLIENT} setup --url ${URL} --ticket ${TICKET} --name 'mac' --kind local --mirror`);
    for (const url of [`${URL} ${OWNED}`, `${OWNED}${URL}`, `${URL};id`, `${URL}\`id\``, `${URL}/`, `${URL}/ade`, "http://pp.example",
      "https://[::1]:8000", "http://[::1]:8000", `${URL}:123456`, `${URL}\n`, "ftp://pp.example", "https://", ""]) {
      expect(pairLine(out({ url }), mac), JSON.stringify(url)).toBeNull();
    }
    for (const url of ["https://proofpane-oss.fly.dev", "https://pp.example:8443", "https://PP.Example", "http://127.0.0.1:8000",
      "http://localhost:8000", "http://localhost"]) {
      expect(pairLine(out({ url }), mac), url).toContain(` --url ${url} --ticket `);
    }
    for (const ticket of [`${TICKET};${OWNED}`, `${OWNED}${TICKET}`, `${TICKET}\n`, "pp-abcdefgh", "PP-ABCDEFG", "PP-ABCDEFGHI", "PP-ABCDEFG1", "tk_abc", ""]) {
      expect(pairLine(out({ ticket }), mac), JSON.stringify(ticket)).toBeNull();
    }
  });

  it("carries only fingerprints of 32 hex characters, as the server writes them and the client takes them", () => {
    expect(pairLine(out({ keys: ["0".repeat(16), "1".repeat(64), "A".repeat(32), "2".repeat(31), `${FP};${OWNED}`, FP] }), mac))
      .toContain(` --keys ${FP} --name `);
  });

  it("gives the ask command only for an address and an ask address in the server's forms", () => {
    expect(askLines(URL, ASK)?.[0]).toBe(`${CLIENT} pair --url ${URL} --ask ${ASK} --name NAME --kind cloud --ref REF`);
    expect(askLines(`${URL} ${OWNED}`, ASK)).toBeNull();
    for (const address of [`${ASK};${OWNED}`, `${OWNED}${ASK}`, `ask-${"c".repeat(19)}`, `ask-${"c".repeat(21)}`, `ask-${"C".repeat(20)}`,
      `ask-${"1".repeat(20)}`, `${ASK}\n`, ""]) {
      expect(askLines(URL, address), JSON.stringify(address)).toBeNull();
    }
  });

  it("gives a pin line only for fingerprints in the server's form, for a session's own window and on Account", () => {
    expect(pinLine("mac", [FP])).toBe(`${CLIENT} --dir "$HOME/.proofpane-ade/"'mac' pin --add ${FP}`);
    expect(pinLine("mac", [FP, `${FP} ${OWNED}`])).toBeNull();
    expect(pinLine("mac", ["0".repeat(16)])).toBeNull();
    expect(accountPinLine(FP)).toBe(`${CLIENT} --dir "$HOME/.proofpane-ade/NAME" pin --add ${FP}`);
    for (const f of [`${FP} ${OWNED}`, `${OWNED}${FP}`, "0".repeat(16), "A".repeat(32), `${FP}\n`, null, undefined, ""]) {
      expect(accountPinLine(f), JSON.stringify(f)).toBeNull();
    }
  });

  it("puts a link's name into a command only in the form the server holds names to", () => {
    expect(listLine("MacBook Pro")).toContain(`"$HOME/.proofpane-ade/MacBook Pro"/history.json`);
    expect(mirrorSettings("MacBook Pro")).toContain(`--dir \\"$HOME/.proofpane-ade/MacBook Pro\\" mirror reply`);
    for (const name of [`Mac${OWNED}`, `${OWNED}Mac`, "Mac\"", "Mac`id`", "Mac\nid", "-mac", "m".repeat(61), ""]) {
      expect(listLine(name), JSON.stringify(name)).toBeNull();
      expect(mirrorSettings(name), JSON.stringify(name)).toBeNull();
    }
  });

  it("gives no line to run for a ticket's answer not in the server's forms, and says why, with nothing to copy", async () => {
    serve(base({
      "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
      "POST /api/account/confirm": () => [200, { confirmation: "c7" }],
      "POST /api/ade/link/tickets": () => [200, { ticket: TICKET, expires_at: T + 600, url: `${URL} ${OWNED}`, keys: [FP], e2e: null }],
    }));
    render(<Channel onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Link a session/ }));
    fireEvent.change(screen.getByLabelText("What to call it"), { target: { value: "Studio" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    const word = await screen.findByRole("group", { name: "Linking “Studio”" });
    fireEvent.change(within(word).getByLabelText("Your password"), { target: { value: "right one" } });
    fireEvent.click(within(word).getByRole("button", { name: "Confirm" }));
    const card = await screen.findByRole("region", { name: "Linking Studio" });
    expect(within(card).getByRole("alert").textContent).toBe(NO_LINE);
    expect(screen.queryByLabelText("The line to run")).toBeNull();
    expect(within(card).queryByRole("button", { name: /Copy/ })).toBeNull();
    expect(document.body.textContent).not.toContain("pp-owned");
    fireEvent.click(within(card).getByRole("button", { name: "Put away" }));
    expect(screen.queryByRole("region", { name: "Linking Studio" })).toBeNull();
  });

  it("gives no ask command for an ask address not in the server's form, and says why", async () => {
    serve(base({ "GET /api/ade/link/address": () => [200, { address: `${ASK};${OWNED}`, created_at: T, url: URL }] }));
    render(<Channel onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Ask address/ }));
    expect((await screen.findByText(NO_LINE)).getAttribute("role")).toBe("alert");
    expect(screen.queryByTitle("Copy the command")).toBeNull();
    expect(document.body.textContent).not.toContain("pp-owned");
  });

  it("says, before a touch, that it gives no pin line for a fingerprint not in the server's form", () => {
    render(<PinNotice w={{ refused: [{ name: "iPhone", fingerprint: `${FP} ${OWNED}` }], all: false, known: true, pinned: ["Touch ID"] }} name="mac" />);
    const note = screen.getByRole("note", { name: "Before you sign" });
    expect(note.textContent).toContain("mac takes this only if Touch ID signs it: it has not pinned iPhone");
    expect(note.textContent).toContain(NO_LINE);
    expect(note.textContent).not.toContain("pp-owned");
    expect(within(note).queryByRole("button")).toBeNull();
  });

  it("gives no settings or list line for a link whose name is not in the server's form", async () => {
    const bad = `Mac${OWNED}`;
    serve(base({ "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [] }] }));
    render(<Conversation l={link({ name: bad })} onBack={() => {}} onClose={() => {}} />);
    expect(await screen.findByText(NO_LINE)).toBeTruthy();
    expect(screen.queryByLabelText("The settings to add")).toBeNull();
    expect(screen.queryByRole("button", { name: /Copy the settings/ })).toBeNull();
    cleanup();
    render(<MachineSessions sessions={[]} link={bad} />);
    expect(document.body.textContent).not.toMatch(/printf|history\.json &&|run this/);
    expect(screen.queryByRole("button", { name: /Copy/ })).toBeNull();
  });
});
