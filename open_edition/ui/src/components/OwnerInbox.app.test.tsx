import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LinkSessions, LinkView } from "../api";
import { eventSlot } from "../e2e";
import { useStore } from "../store";
import { Approvals } from "./Approvals";

// The owner's inbox in the desktop and phone apps (APP): they hold no keys and sign nothing, since their passkeys are not
// this site's. A linked session's prompt is counted and listed there, its words left sealed, and the app opens the
// browser tab at the server's address, where it is read and answered.
const fake = vi.hoisted(() => ({ opened: [] as string[] }));

vi.mock("../api", async (importOriginal) => ({ ...(await importOriginal<typeof import("../api")>()), APP: true }));
vi.mock("../e2e", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../e2e")>()),
  useE2E: () => ({ state: "locked", on: true, generation: 1, unlock: async () => {}, unlockWithCode: async () => {}, lock: () => {} }),
  openSealed: async (envelope: string) => { fake.opened.push(envelope); return { ok: false, why: "not here" }; },
}));

const CID = "0123456789abcdef0123456789abcdef";
const seal = (value: unknown, slot: string) => `e2e1.${Buffer.from(JSON.stringify({ slot, value }), "utf8").toString("base64url")}`;
const link = (over: Partial<LinkView> = {}): LinkView => ({
  id: "L1", name: "mac", kind: "local", session_ref: null, interval_min: 5, created_at: 1, last_seen_at: null,
  last_heartbeat_at: null, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0, checkpoint_at: null,
  events: 1, last_event: null, commands: [], stopped_at: null, ...over,
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); fake.opened.length = 0; useStore.setState(useStore.getInitialState()); });

describe("a linked session's prompt in the desktop and phone apps", () => {
  it("is listed as the session's report, its words left sealed, with the browser tab at the server's address to answer it", () => {
    // no request leaves the test: the inbox asks the server named below for its hidden and closed prompts
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "no route" }), { status: 404 })));
    localStorage.setItem("pp-open-server", "https://pp.example");
    const words = JSON.stringify({ tool: "Bash", input: { command: "rm -rf build" }, session_id: "cc-1", agent_id: null, deadline: 2 });
    const prompt = { seq: 7, time: "2026-11-02T10:00:00Z", kind: "prompt", from: "mac", to: "owner", ref: null, from_id: "L1",
      client_id: CID, link_id: "L1", deadline_at: 1_790_001_800, look: false,
      text: seal({ text: words, session: null, said_at: null }, eventSlot("acct-1", "L1", "prompt", undefined, null, CID)) };
    useStore.setState({ user: { id: "acct-1", email: "o@example.org", can_sign_in_again: true },
      links: { links: [link()], questions: [], public_url: "https://pp.example", prompts: [prompt], proposals: [] } as unknown as LinkSessions });
    render(<Approvals records={[]} requests={[]} onClose={() => {}} onOpen={() => {}} />);
    expect(screen.queryByText(/Reported by the session mac/)).not.toBeNull();
    expect(screen.getByText("Encrypted: read it in a browser at pp.example")).toBeTruthy();
    const open = screen.getByRole("link", { name: /Open Approvals in your browser/ });
    expect(open.getAttribute("href")).toBe("https://pp.example/app/#approvals");
    expect(open.getAttribute("target")).toBe("_blank");
    // nothing is signed or opened here
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Deny" })).toBeNull();
    expect(document.body.textContent).not.toContain("rm -rf build");
    expect(new XMLSerializer().serializeToString(document.body)).not.toContain("e2e1.");
    expect(fake.opened).toEqual([]);
  });
});

describe("one tap from an item in the apps to the same item in the browser", () => {
  it("offers each prompt, proposal and question it cannot answer one tap that opens that item in a browser at the server's address", () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "no route" }), { status: 404 })));
    localStorage.setItem("pp-open-server", "https://pp.example");
    const words = JSON.stringify({ tool: "Bash", input: { command: "rm -rf build" }, session_id: "cc-1", agent_id: null, deadline: 2 });
    const prompt = { seq: 7, time: "2026-11-02T10:00:00Z", kind: "prompt", from: "mac", to: "owner", ref: null, from_id: "L1",
      client_id: CID, link_id: "L1", deadline_at: 1_790_001_800, look: false,
      text: seal({ text: words, session: null, said_at: null }, eventSlot("acct-1", "L1", "prompt", undefined, null, CID)) };
    const proposal = { seq: 11, time: "2026-11-02T10:01:00Z", kind: "proposal", from: "planner", to: "owner", ref: null, from_id: "L2",
      client_id: CID, link_id: "L2", for_id: "L1", for: "mac", deadline_at: 1_790_086_400,
      text: seal({ text: "run the tests", session: null, said_at: null }, eventSlot("acct-1", "L2", "proposal", undefined, null, CID)) };
    const question = { seq: 13, time: "2026-11-02T10:02:00Z", kind: "question", from: "mac", to: "owner", ref: null, from_id: "L1",
      client_id: CID, link_id: "L1",
      text: seal({ text: "Which branch?", session: null, said_at: null }, eventSlot("acct-1", "L1", "question", undefined, null, CID)) };
    useStore.setState({ user: { id: "acct-1", email: "o@example.org", can_sign_in_again: true },
      links: { links: [link(), link({ id: "L2", name: "planner" })], questions: [question], public_url: "https://pp.example", prompts: [prompt],
               proposals: [proposal] } as unknown as LinkSessions });
    render(<Approvals records={[]} requests={[]} onClose={() => {}} onOpen={() => {}} />);
    const forPrompt = screen.getByRole("link", { name: "Open this prompt in your browser" });
    expect(forPrompt.getAttribute("href")).toBe("https://pp.example/app/#item=7");
    expect(forPrompt.getAttribute("target")).toBe("_blank");
    const forProposal = screen.getByRole("link", { name: "Open this proposal in your browser" });
    expect(forProposal.getAttribute("href")).toBe("https://pp.example/app/#item=11");
    expect(forProposal.getAttribute("target")).toBe("_blank");
    const forQuestion = screen.getByRole("link", { name: "Open this question in your browser" });
    expect(forQuestion.getAttribute("href")).toBe("https://pp.example/app/#item=13");
    expect(fake.opened).toEqual([]);
  });
});
