import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { eventSlot } from "./e2e";
import { useStore } from "./store";
import { browser, shellServer } from "./shellTestkit";

// A link to #item=<seq>: the phone and desktop apps, which sign nothing, open an item of the
// linked sessions' inbox in a browser at the server's address, where Approvals opens at that item.

beforeAll(async () => {
  await Promise.all([import("./components/Approvals"), import("./components/Channel"), import("./components/CommandPalette")]);
}, 60000);
beforeEach(() => {
  vi.useFakeTimers();
  localStorage.setItem("pp-open-token", "test-token.sig");
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
  history.replaceState(null, "", "/");
  useStore.setState(useStore.getInitialState());
});

const pass = async (ms = 1000) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const CID = "0123456789abcdef0123456789abcdef";
const seal = (value: unknown, slot: string) => `e2e1.${Buffer.from(JSON.stringify({ slot, value }), "utf8").toString("base64url")}`;
const link = (id: string, name: string) => ({
  id, name, kind: "local", session_ref: null, interval_min: 5, created_at: 1, last_seen_at: null, last_heartbeat_at: null,
  silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0, checkpoint_at: null, events: 1, last_event: null,
  commands: [], stopped_at: null,
});
const prompt = (seq: number, linkId: string, from: string) => ({
  seq, time: "2026-11-02T10:00:00Z", kind: "prompt", from, to: "owner", ref: null, from_id: linkId, client_id: CID,
  link_id: linkId, deadline_at: 1_790_001_800, look: false,
  text: seal({ text: JSON.stringify({ tool: "Bash", input: { command: "ls" }, session_id: "cc-1", agent_id: null, deadline: 2 }), session: null, said_at: null },
             eventSlot("u", linkId, "prompt", undefined, null, CID)),
});

describe("a link to one item of the linked sessions' inbox", () => {
  it("opens Approvals at that item, brought into view, and leaves the address without it", async () => {
    browser();
    const scrolled: Element[] = [];
    Element.prototype.scrollIntoView = function (this: Element) { scrolled.push(this); };
    history.replaceState(null, "", "/#item=8");
    shellServer({ links: { links: [link("L1", "mac"), link("L2", "studio")], questions: [], public_url: "https://pp.example", asks: [],
                           prompts: [prompt(7, "L1", "mac"), prompt(8, "L2", "studio")], proposals: [] } as never });
    render(<App />);
    await pass(2000);
    await vi.waitFor(() => expect(screen.getByRole("complementary", { name: "Approvals" })).toBeTruthy(), { timeout: 10000 });
    const card = await vi.waitFor(() => screen.getByRole("group", { name: "Reported by the session studio" }), { timeout: 10000 });
    await pass(100);
    expect(scrolled).toContain(card);
    const other = screen.getByRole("group", { name: "Reported by the session mac" });
    expect(scrolled).not.toContain(other);
    expect(card.className).toContain("ring-2");                 // marked
    expect(other.className).not.toContain("ring-2");
    expect(screen.queryByText(/The item the link names is not waiting here/)).toBeNull();
    expect(location.hash).toBe("");
  }, 30000);
});

describe("a link to an item that is a proposal, or that is not waiting", () => {
  const proposal = (seq: number) => ({
    seq, time: "2026-11-02T10:01:00Z", kind: "proposal", from: "planner", to: "owner", ref: null, from_id: "L2", client_id: CID,
    link_id: "L2", for_id: "L1", for: "mac", deadline_at: 1_790_086_400,
    text: seal({ text: "run the tests", session: null, said_at: null }, eventSlot("u", "L2", "proposal", undefined, null, CID)),
  });

  it("opens Approvals at a proposal, brought into view and marked", async () => {
    browser();
    const scrolled: Element[] = [];
    Element.prototype.scrollIntoView = function (this: Element) { scrolled.push(this); };
    history.replaceState(null, "", "/#item=11");
    shellServer({ links: { links: [link("L1", "mac"), link("L2", "planner")], questions: [], public_url: "https://pp.example", asks: [],
                           prompts: [prompt(7, "L1", "mac")], proposals: [proposal(11)] } as never });
    render(<App />);
    await pass(2000);
    const card = await vi.waitFor(() => screen.getByRole("group", { name: "Proposed by the session planner, for mac" }), { timeout: 10000 });
    await pass(100);
    expect(scrolled).toContain(card);
    expect(card.className).toContain("ring-2");
    expect(screen.queryByText(/The item the link names is not waiting here/)).toBeNull();
  }, 30000);

  it("says, where the item the link names is not waiting, that it is not, and marks nothing", async () => {
    browser();
    history.replaceState(null, "", "/#item=99");
    shellServer({ links: { links: [link("L1", "mac")], questions: [], public_url: "https://pp.example", asks: [],
                           prompts: [prompt(7, "L1", "mac")], proposals: [] } as never });
    render(<App />);
    await pass(2000);
    await vi.waitFor(() => expect(screen.getByRole("complementary", { name: "Approvals" })).toBeTruthy(), { timeout: 10000 });
    await pass(100);
    expect(screen.getByText("The item the link names is not waiting here: it was answered or closed, or it is another account's.")).toBeTruthy();
    expect(screen.getByRole("group", { name: "Reported by the session mac" }).className).not.toContain("ring-2");
  }, 30000);
});
