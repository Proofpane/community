// A slash menu where the owner writes, to a linked session or in
// the workspace's chat. A `/` at the start of the box, or the slash button beside it, opens a menu of entries, filtered
// as he types, each chosen by a click, a tap or Enter, each opening a modal over the page that Escape or a tap outside
// closes. Account, lanes and the agent map read what the page holds and change nothing.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LinkView, SessionRecord } from "../api";
import { reportedSlot, useE2EStore } from "../e2e";
import { useStore } from "../store";
import { dayTime } from "../when";
import { Channel, Conversation } from "./Channel";
import { ChatPane } from "./ChatPane";

// a device that can sign, as ChannelUx.test.tsx stands one in: read once, as the page's modules load
vi.hoisted(() => {
  (globalThis as unknown as { PublicKeyCredential: unknown }).PublicKeyCredential = function PublicKeyCredential() {};
  Object.defineProperty(navigator, "credentials", { value: { get: () => Promise.reject(new Error("not here")) },
    configurable: true });
});

const T = Math.floor(Date.now() / 1000);
type Call = { path: string; method: string };
let calls: Call[] = [];
function serve(routes: Record<string, unknown>) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    calls.push({ path: String(url), method });
    const got = routes[`${method} ${url}`];
    return new Response(JSON.stringify(got ?? { detail: "no route" }), { status: got === undefined ? 404 : 200 });
  }));
}
const link = (over: Partial<LinkView> = {}): LinkView => ({
  id: "L1", name: "mac", kind: "local", session_ref: null, interval_min: 15, created_at: T - 3600,
  last_seen_at: T - 60, last_heartbeat_at: T - 60, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0,
  checkpoint_at: null, events: 0, last_event: null, commands: [], stopped_at: null, ...over,
});
// every entry of the menu, as "/" lists them
const ALL = ["/account", "/lanes", "/map", "/usage", "/plans", "/background"];

// a sealed text opens here as Sealed.test.tsx stands its opening in: an "envelope" is the value and the slot it was
// sealed to, so it opens only with that slot, as a real one does; the tab's keys are the store's own state
vi.mock("../e2e", async (importOriginal) => {
  const real = await importOriginal<typeof import("../e2e")>();
  return {
    ...real,
    openSealed: async (envelope: string, slot: string) => {
      try {
        const inner = JSON.parse(Buffer.from(envelope.slice(5), "base64url").toString("utf8"));
        return inner.slot === slot ? { ok: true, value: inner.value }
          : { ok: false, why: "Its tag does not check: it was sealed for another place." };
      } catch {
        return { ok: false, why: "It is not an envelope." };
      }
    },
  };
});
const sealFor = (value: unknown, slot: string) =>
  `e2e1.${Buffer.from(JSON.stringify({ slot, value }), "utf8").toString("base64url")}`;
// what mac's client reported of its own session with its heartbeat; planner's reports nothing
const REPORTED = {
  usage: { context_used: 143500, context_window: 1000000, model: "claude-opus-5-5" },
  tasks: [{ text: "the README", state: "completed" }, { text: "the cases", state: "in_progress" }],
  background: [{ id: "b1", kind: "shell", text: "Watch the file", state: "running" }],
};
const LINKS = {
  links: [
    link({ last_heard_at: T - 600, checkpoint: { doing: "the README", next: "the cases", waiting_on: "your go" },
      reported: REPORTED as never, reported_at: T - 30,
      commands: [
        { seq: 4, time: "t", kind: "instruction", from: "owner", to: "mac", ref: null, text: "x", state: "queued" },
        { seq: 6, time: "t", kind: "instruction", from: "owner", to: "mac", ref: null, text: "y", state: "taken" },
        { seq: 7, time: "t", kind: "instruction", from: "owner", to: "mac", ref: null, text: "z", state: "answered" },
      ] }),
    link({ id: "L2", name: "planner", kind: "cloud", last_heard_at: T - 60, checkpoint: { doing: "the plan" } }),
  ],
  questions: [{ seq: 5, time: "t", kind: "question", from: "mac", to: "owner", ref: null, text: "Push?",
    link_id: "L1" }],
  prompts: [], proposals: [], public_url: "https://pp.example",
};
const FP1 = "a".repeat(32);
const FP2 = "b".repeat(32);
const ROUTES: Record<string, unknown> = {
  "GET /api/account/passkeys": { high_security: true, passkeys: [{ name: "this Mac", fingerprint: FP1, tier: "device" },
    { name: "the YubiKey", fingerprint: FP2, tier: "hardware" }] },
  "GET /api/ade/controller": { controller: { account: "default", state: "on" } },
  "GET /api/ade/link/timeline/L1?limit=100": { events: [] },
  "GET /api/ade/link/sessions": LINKS,
};
const ID = "0123456789ab";
const record = { id: ID, repo: "app", program: "chat", task: "a task", base: "abc", branch: null, created_at: 1,
  finished: null, running: true, exit_code: null, ended_at: null, chat: null } as SessionRecord;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useStore.setState(useStore.getInitialState());
  useE2EStore.setState({ view: null, ring: null });
});

function inChat(routes: Record<string, unknown> = ROUTES) {
  serve(routes);
  useStore.setState({ loadChat: async () => {}, chat: { [ID]: [] }, links: LINKS as never,
    user: { id: "u1", email: "owner@pp.example" } });
  render(<ChatPane record={record} />);
  return screen.getByRole("textbox", { name: "Message to Claude" });
}
async function inConversation() {
  serve(ROUTES);
  useStore.setState({ links: LINKS as never, user: { id: "u1", email: "owner@pp.example" } });
  render(<Conversation l={LINKS.links[0]} onBack={() => {}} onClose={() => {}} />);
  return screen.findByRole("textbox", { name: "Message mac" });
}
// no menu at all: no list of entries, and no word that none is called so
function noMenu() {
  expect(screen.queryByRole("listbox", { name: "The slash menu" })).toBeNull();
  expect(screen.queryByText("Nothing is called that.")).toBeNull();
}
// each entry by its own name, the first of its two parts (its hint beside it)
const options = () => within(screen.getByRole("listbox", { name: "The slash menu" })).getAllByRole("option")
  .map((o) => o.querySelector("span")?.textContent);

describe("the slash menu, typed", () => {
  it("opens on a slash at the start of the workspace chat's box, filters as typed, and Enter opens its modal",
    async () => {
    const box = inChat();
    fireEvent.change(box, { target: { value: "/" } });
    expect(options()).toEqual(ALL);
    fireEvent.change(box, { target: { value: "/Agent" } });
    expect(options()).toEqual(["/map"]);
    fireEvent.change(box, { target: { value: "/la" } });
    expect(options()).toEqual(["/lanes"]);
    fireEvent.keyDown(box, { key: "Enter" });
    expect(screen.getByRole("dialog", { name: "Lanes" })).toBeTruthy();
    expect((box as HTMLTextAreaElement).value).toBe("");
    noMenu();
  });

  it("opens in a linked session's box too, and is no menu once the words hold a space or start otherwise",
    async () => {
    const box = await inConversation();
    fireEvent.change(box, { target: { value: "/m" } });
    expect(options()).toEqual(["/map"]);
    fireEvent.change(box, { target: { value: "/m please" } });
    noMenu();
    fireEvent.change(box, { target: { value: "and/or" } });
    noMenu();
  });

  it("the arrows move through its entries, round again past the last, and Enter opens the one chosen", () => {
    const box = inChat();
    fireEvent.change(box, { target: { value: "/" } });
    for (const key of ["ArrowDown", "ArrowDown", "ArrowDown", "ArrowUp"]) fireEvent.keyDown(box, { key });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(screen.getByRole("dialog", { name: "Agent map" })).toBeTruthy();
  });

  it("only a plain Enter chooses: not Tab, Shift+Enter, nor an Enter that ends an input method's composition", () => {
    const box = inChat();
    fireEvent.change(box, { target: { value: "/a" } });
    fireEvent.keyDown(box, { key: "Tab" });
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    fireEvent.keyDown(box, { key: "Enter", isComposing: true });
    fireEvent.keyDown(box, { key: "Enter", keyCode: 229 });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(options()).toEqual(["/account", "/map"]);
    expect(calls.filter((c) => c.method !== "GET")).toEqual([]);         // nor did the box send its words
  });

  it("the box names the entry chosen as the arrows move, and words that call none are said, not listed", () => {
    const box = inChat();
    const active = () => document.getElementById(box.getAttribute("aria-activedescendant") ?? "")?.textContent ?? "";
    fireEvent.change(box, { target: { value: "/" } });
    expect(box.getAttribute("aria-controls")).toBe(screen.getByRole("listbox", { name: "The slash menu" }).id);
    expect(active()).toMatch(/^\/account/);
    fireEvent.keyDown(box, { key: "ArrowDown" });
    expect(active()).toMatch(/^\/lanes/);
    fireEvent.change(box, { target: { value: "/zz" } });
    expect(screen.queryByRole("listbox", { name: "The slash menu" })).toBeNull();
    expect(screen.getByRole("status").textContent).toBe("Nothing is called that.");
    fireEvent.change(box, { target: { value: "" } });
    expect(box.getAttribute("aria-activedescendant")).toBeNull();
  });

  it("closes on Escape and opens no modal", async () => {
    const box = inChat();
    fireEvent.change(box, { target: { value: "/" } });
    expect(options()).toEqual(ALL);
    fireEvent.keyDown(box, { key: "Escape" });
    noMenu();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("the slash menu, clicked or tapped", () => {
  it("opens from the slash button beside each box, and an entry is a click; a tap outside closes its modal",
    async () => {
    for (const open of [inChat, inConversation]) {
      await open();
      fireEvent.click(screen.getByRole("button", { name: "Open the slash menu" }));
      expect(options()).toEqual(ALL);
      fireEvent.click(screen.getByRole("option", { name: /^\/account/ }));
      const dialog = await screen.findByRole("dialog", { name: "Account" });
      expect(dialog.textContent).toContain("owner@pp.example");
      fireEvent.mouseDown(dialog.parentElement as HTMLElement);
      expect(screen.queryByRole("dialog")).toBeNull();
      cleanup();
    }
  });

  it("a menu its button opened closes once words are typed or on a tap elsewhere, and Enter then sends them",
    async () => {
    const box = inChat();
    const button = screen.getByRole("button", { name: "Open the slash menu" });
    fireEvent.click(button);
    fireEvent.change(box, { target: { value: "run the tests" } });
    noMenu();
    fireEvent.click(button);
    expect(options()).toEqual(ALL);
    fireEvent.mouseDown(document.body);
    noMenu();
    fireEvent.keyDown(box, { key: "Enter" });
    expect(screen.queryByRole("dialog")).toBeNull();
    await vi.waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
  });

  it("an entry chosen from the menu its button opened leaves the box's words as they are", () => {
    const box = inChat();
    fireEvent.change(box, { target: { value: "/compact" } });
    fireEvent.click(screen.getByRole("button", { name: "Open the slash menu" }));
    fireEvent.click(screen.getByRole("option", { name: /^\/account/ }));
    expect(screen.getByRole("dialog", { name: "Account" })).toBeTruthy();
    expect((box as HTMLTextAreaElement).value).toBe("/compact");
  });
});

describe("inside the Linked sessions panel", () => {
  it("Escape closes the menu its button opened, or an entry's modal, and leaves the panel open", async () => {
    const one = { ...LINKS, links: [LINKS.links[0]] };
    serve({ ...ROUTES, "GET /api/ade/link/sessions": one });
    useStore.setState({ links: one as never, user: { id: "u1", email: "owner@pp.example" } });
    const onClose = vi.fn();
    render(<Channel onClose={onClose} />);
    fireEvent.click(await screen.findByRole("button", { name: /Open conversation/ }));
    await screen.findByRole("textbox", { name: "Message mac" });
    const button = screen.getByRole("button", { name: "Open the slash menu" });
    fireEvent.click(button);
    fireEvent.keyDown(button, { key: "Escape" });
    noMenu();
    fireEvent.click(button);
    fireEvent.click(screen.getByRole("option", { name: /^\/account/ }));
    fireEvent.keyDown(await screen.findByRole("dialog", { name: "Account" }), { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Account" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "Linked sessions" })).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("what each modal reads, changing nothing", () => {
  const open = async (entry: string, name: string, routes: Record<string, unknown> = ROUTES,
    links: unknown = LINKS) => {
    const box = inChat(routes);
    useStore.setState({ links: links as never });
    fireEvent.change(box, { target: { value: `/${entry}` } });
    fireEvent.keyDown(box, { key: "Enter" });
    return screen.findByRole("dialog", { name });
  };
  const said = (d: HTMLElement, term: string) =>
    within(d).getByText(term, { selector: "dt" }).nextElementSibling?.textContent;

  it("account: the workspace as it stands, a failed start said with why", async () => {
    useStore.setState({ ws: { enabled: true, choice: "hosted", running: false, start_error: "no room left" } });
    const d = await open("account", "Account");
    expect(said(d, "Workspace")).toBe("its last start failed: no room left");
  });

  it("lanes: a checkpoint's fields as its card shows them, every character showing itself, and what is open apart",
    async () => {
    const checkpoint = { doing: { say: "a\u202eb" }, next: ["x", "y"], waiting_on: "c\u200bd", open: ["the README"] };
    const d = await open("lanes", "Lanes", ROUTES, { ...LINKS, links: [link({ checkpoint, last_heard_at: T })] });
    const mac = within(d).getByRole("listitem", { name: "mac" });
    expect(mac.textContent).toContain('Doing: {"say":"a[U+202E]b"}');
    expect(mac.textContent).toContain("Next: x \u00b7 y");
    expect(mac.textContent).toContain("Blocked on: c[U+200B]d");
    expect(mac.textContent).toContain("Open: the README");
    expect(mac.textContent).not.toContain("Blocked on: c[U+200B]d; the README");
  });

  it("the agent map: no one holds the Manager's role where no chat does, and a failed read is said as one",
    async () => {
    let d = await open("map", "Agent map", { ...ROUTES, "GET /api/ade/controller": { controller: null } });
    await vi.waitFor(() => expect(d.textContent).toContain("The Manager: no one holds the role"));
    cleanup();
    const { ["GET /api/ade/controller"]: _, ...failing } = ROUTES;
    d = await open("map", "Agent map", failing);
    await vi.waitFor(() => expect(d.textContent).toContain("The Manager: could not be read just now"));
  });

  it("the agent map: a session named you is a node of its own, its edges its own", async () => {
    const links = { ...LINKS, questions: [], links: [LINKS.links[0], link({ id: "L3", name: "you", commands: [] })] };
    const d = await open("map", "Agent map", ROUTES, links);
    const nodes = within(d).getByRole("list", { name: "The sessions" });
    expect(within(nodes).getByRole("listitem", { name: "you, the owner" }).textContent).toBe("you1");
    expect(within(nodes).getByRole("listitem", { name: "the session you" }).textContent).toBe("you0");
  });

  it("account: who is signed in, the workspace, the passkeys, how many each session pinned, the linked sessions",
    async () => {
    // encryption's view as the page holds it: mac pinned this Mac's key alone, planner reports no pins
    useE2EStore.setState({ view: { links: [
      { link_id: "L1", name: "mac", key_id: null, last_required_at: null, pins: [FP1] },
      { link_id: "L2", name: "planner", key_id: null, last_required_at: null, pins: null },
    ] } as never });
    const d = await open("account", "Account");
    await vi.waitFor(() => expect(d.textContent).toContain("2: this Mac, the YubiKey (high security on)"));
    expect(d.textContent).toContain("2: mac, planner");
    // high security on: only the hardware key signs a command, and mac has not pinned it
    expect(d.textContent).toContain("mac: 0 of 1; planner: not reported");
  });

  it("lanes: each linked session newest first, with its checkpoint's doing, next and what blocks it", async () => {
    const d = await open("lanes", "Lanes");
    expect(within(d).getAllByRole("listitem").map((li) => li.getAttribute("aria-label"))).toEqual(["planner", "mac"]);
    const mac = within(d).getByRole("listitem", { name: "mac" });
    expect(mac.textContent).toContain("Doing: the README");
    expect(mac.textContent).toContain("Next: the cases");
    expect(mac.textContent).toContain("Blocked on: your go");
  });

  it("the agent map: who holds the Manager's role, the commands open and what waits on the owner", async () => {
    const box = inChat();
    // planner waits on the owner with a prompt and a proposal for mac, as Approvals counts them
    const at = { time: "t", from: "planner", to: "owner", ref: null, text: "p", link_id: "L2", deadline_at: T + 600 };
    useStore.setState({ links: { ...LINKS, prompts: [{ ...at, seq: 8, kind: "prompt", look: false }],
      proposals: [{ ...at, seq: 9, kind: "proposal", for_id: "L1", for: "mac" }] } as never });
    fireEvent.change(box, { target: { value: "/map" } });
    fireEvent.keyDown(box, { key: "Enter" });
    const d = await screen.findByRole("dialog", { name: "Agent map" });
    await vi.waitFor(() => expect(d.textContent).toContain("the workspace's chat, on default (on)"));
    const edges = within(d).getByRole("list", { name: "What goes between them" });
    expect(within(edges).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "you \u2192 mac: 2 commands open", "planner \u2192 mac: a proposal waiting for your signature",
      "mac \u2192 you: 1 waiting on you", "planner \u2192 you: 2 waiting on you"]);
  });

  it("usage: each session's context used of its window, its model and its rate limit as reported, or not reported",
    async () => {
    const d = await open("usage", "Usage");
    const mac = within(d).getByRole("listitem", { name: "mac" });
    expect(within(mac).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Context: 143,500 of 1,000,000 tokens (14%)", "Model: claude-opus-5-5", "Rate limit: not reported"]);
    expect(within(d).getByRole("listitem", { name: "planner" }).textContent).toContain("not reported");
    // each session newest heard first, with when its report came, or that none has
    expect([...d.querySelectorAll("li[aria-label]")].map((li) => li.getAttribute("aria-label")))
      .toEqual(["planner", "mac"]);
    expect(mac.textContent).toContain(`reported ${dayTime(T - 30)}`);
    expect(within(d).getByRole("listitem", { name: "planner" }).textContent).toContain("no report yet");
  });

  it("the plan list and the background tasks: each session's as it reported them, or not reported", async () => {
    let d = await open("plans", "Plan list");
    const items = (name: string) => within(within(d).getByRole("listitem", { name })).getAllByRole("listitem")
      .map((li) => li.textContent);
    expect(items("mac")).toEqual(["done: the README", "doing: the cases"]);
    expect(items("planner")).toEqual(["not reported"]);
    cleanup();
    d = await open("background", "Background tasks");
    expect(items("mac")).toEqual(["shell b1, running: Watch the file"]);
    expect(items("planner")).toEqual(["not reported"]);
  });

  it("a report's lists are held whole to the server's rules for them, and every character shows itself", async () => {
    // as a sealed report's lists may come, which the server cannot read
    const one = (tasks: unknown, background: unknown = []) => ({ ...LINKS, links: [link({ last_heard_at: T,
      reported: { usage: { context_used: 10, context_window: 100, model: "m\u202ex" }, tasks, background } as never,
      reported_at: T })] });
    const items = (d: HTMLElement) => within(within(d).getByRole("listitem", { name: "mac" }))
      .getAllByRole("listitem").map((li) => li.textContent);
    let d = await open("plans", "Plan list", ROUTES, one([{ text: "a\u202eb", state: "in_progress" }]));
    expect(items(d)).toEqual(["doing: a[U+202E]b"]);
    for (const [tasks, said] of [
      [[{ text: 7, state: "in_progress" }, { text: "the cases", state: "pending" }], "This text could not be read"],
      [[{ text: "c", state: "constructor" }], "This text could not be read"],
      [[{ text: "push ghp_abc", state: "pending" }], "This text is not shown: it holds something shaped like"],
      [[{ text: "x".repeat(161), state: "pending" }], "This text is not shown: it is longer than 160 characters"],
    ] as [unknown, string][]) {
      cleanup();
      d = await open("plans", "Plan list", ROUTES, one(tasks));
      expect(items(d).length).toBe(1);
      expect(items(d)[0]).toContain(said);
    }
    cleanup();
    d = await open("background", "Background tasks", ROUTES,
      one([], [{ id: "b 1", kind: "shell", text: "w", state: "running" }]));
    expect(items(d)).toEqual(["This text could not be read"]);
    cleanup();
    d = await open("background", "Background tasks", ROUTES,
      one([], [{ id: "b2", kind: "agent", text: "d\u200be", state: "completed" }]));
    expect(items(d)).toEqual(["agent b2, completed: d[U+200B]e"]);
    cleanup();
    d = await open("usage", "Usage", ROUTES, one([]));
    expect(items(d)).toEqual(["Context: 10 of 100 tokens (10%)", "Model: m[U+202E]x", "Rate limit: not reported"]);
  });

  it("usage: a rate limit as it is reported, and a count past its window said alone", async () => {
    const one = (usage: unknown) => ({ ...LINKS, links: [link({ last_heard_at: T, reported: { usage } as never,
      reported_at: T })] });
    const items = (d: HTMLElement) => within(within(d).getByRole("listitem", { name: "mac" }))
      .getAllByRole("listitem").map((li) => li.textContent);
    let d = await open("usage", "Usage", ROUTES, one({ context_used: 500000, context_window: 200000,
      rate_limit: { state: "ok" } }));
    expect(items(d)).toEqual(["Context: 500,000 tokens", "Model: not reported", "Rate limit: not limited"]);
    cleanup();
    d = await open("usage", "Usage", ROUTES,
      one({ context_used: 1, rate_limit: { state: "limited", resets_at: T + 3600 } }));
    expect(items(d)[2]).toBe(`Rate limit: limited, until ${dayTime(T + 3600)}`);
  });

  it("a report's lists that came sealed are said as encrypted where this tab holds no key; its numbers show",
    async () => {
    // the account's keys held and the tab locked: on, a master key, no passkey's credential needed for "locked"
    useE2EStore.setState({ view: { on: true, links: [], mk_id: "m1", credentials: [] } as never, ring: null });
    const sealedOne = { ...LINKS, links: [link({ last_heard_at: T,
      reported: { usage: REPORTED.usage, sealed: "e2e1." + "A".repeat(120) } as never, reported_at: T })] };
    expect(reportedSlot("acct", "L1")).toBe("proofpane-e2e/1|acct|L1|reported");
    let d = await open("plans", "Plan list", ROUTES, sealedOne);
    expect(within(within(d).getByRole("listitem", { name: "mac" })).getAllByRole("listitem")
      .map((li) => li.textContent)).toEqual(["Encrypted"]);
    expect(d.textContent).not.toContain("not reported");
    cleanup();
    d = await open("usage", "Usage", ROUTES, sealedOne);
    expect(d.textContent).toContain("Context: 143,500 of 1,000,000 tokens (14%)");
  });

  it("a sealed report opened in a tab that holds the keys: its lists drawn, held to the rules as a clear one's",
    async () => {
    useE2EStore.setState({ view: { on: true, links: [], mk_id: "m1", credentials: [] } as never, ring: {} as never });
    const one = (lists: unknown, slot = reportedSlot("u1", "L1")) => ({ ...LINKS, links: [link({ last_heard_at: T,
      reported: { usage: REPORTED.usage, sealed: sealFor(lists, slot) } as never, reported_at: T })] });
    const items = (d: HTMLElement) => within(within(d).getByRole("listitem", { name: "mac" }))
      .getAllByRole("listitem").map((li) => li.textContent);
    let d = await open("plans", "Plan list", ROUTES, one({ tasks: REPORTED.tasks }));
    await vi.waitFor(() => expect(items(d)).toEqual(["done: the README", "doing: the cases"]));
    cleanup();
    d = await open("background", "Background tasks", ROUTES, one({ background: REPORTED.background }));
    await vi.waitFor(() => expect(items(d)).toEqual(["shell b1, running: Watch the file"]));
    for (const lists of [{ tasks: [{ text: "c", state: "constructor" }] }, { tasks: REPORTED.tasks, notes: "x" }]) {
      cleanup();
      d = await open("plans", "Plan list", ROUTES, one(lists));
      await vi.waitFor(() => expect(items(d)).toEqual(["This text could not be read"]));
    }
    cleanup();                                  // sealed for another link's place: not opened as this one's
    d = await open("plans", "Plan list", ROUTES, one({ tasks: REPORTED.tasks }, reportedSlot("u1", "L2")));
    await vi.waitFor(() => expect(items(d)).toEqual(["This text could not be read"]));
  });

  it("Escape closes its modal, and nothing but reads is sent from either box, whichever the entry", async () => {
    for (const box of [inChat, inConversation]) {
      await box();
      for (const [entry, name] of [["account", "Account"], ["lanes", "Lanes"], ["map", "Agent map"], ["usage", "Usage"],
        ["plans", "Plan list"], ["background", "Background tasks"]]) {
        fireEvent.click(screen.getByRole("button", { name: "Open the slash menu" }));
        fireEvent.click(screen.getByRole("option", { name: new RegExp(`^/${entry}`) }));
        fireEvent.keyDown(await screen.findByRole("dialog", { name }), { key: "Escape" });
        expect(screen.queryByRole("dialog", { name })).toBeNull();
      }
      expect(calls.filter((c) => c.method !== "GET")).toEqual([]);
      cleanup();
    }
  });
});
