// CLOUD-LINK (queue 4.317): the block Linked sessions gives to link a session on claude.ai. It fetches the link client
// from the ADE, checks it against the sha256 this page's build carries (that of open_edition/ade_link.py), puts it
// where SETUP_A.md installs it and runs setup; above it the page says the one setting a claude.ai environment needs.
// The block and its sentences are kept in tests/vectors/cloud_link_page.json, which the client's cases run in a shell
// (tests/test_cloud_link.py): CLOUD_LINK_WRITE_VECTORS=1 writes the file from what the page gives.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LinkView } from "../api";
import { useStore } from "../store";
import { BLOCK_WAITS, CLIENT_CHANGED, hostRefused, NO_CRYPTOGRAPHY, ticketEnded } from "../cloudLink";
import { Channel, NO_LINE, pairLine, type TicketOut } from "./Channel";

const FILE = join(__dirname, "../../../tests/vectors/cloud_link_page.json");
const SHA256 = createHash("sha256").update(readFileSync(join(__dirname, "../../../ade_link.py"))).digest("hex");
const WRITTEN = "@CLIENT_SHA256@";            // where the file writes the client's sha256, which changes with it

interface Vectors {
  about: string; answer: Omit<TicketOut, "url">; name: string; blocks: Record<string, string>; mac_line: string;
  host_refused: string; client_changed: string; no_cryptography: string; block_waits: string; ticket_ended: string;
}
const vectors = (): Vectors => JSON.parse(readFileSync(FILE, "utf8"));
const answer = (v: Vectors, url: string): TicketOut => ({ ...v.answer, url });

describe("the block for a session on claude.ai", () => {
  it("is the vectors file's, the client's sha256 written in its place, and this Mac's line is as it was", () => {
    const v = vectors();
    const blocks = Object.fromEntries(Object.keys(v.blocks).map((url) =>
      [url, pairLine(answer(v, url), { name: v.name, kind: "cloud" })?.split(SHA256).join(WRITTEN)]));
    const mac = pairLine(answer(v, "https://ade.example.test"), { name: v.name, kind: "local" });
    const said = { host_refused: hostRefused("{host}"), client_changed: CLIENT_CHANGED, no_cryptography: NO_CRYPTOGRAPHY,
                   block_waits: BLOCK_WAITS, ticket_ended: ticketEnded("{host}") };
    if (process.env.CLOUD_LINK_WRITE_VECTORS) {
      writeFileSync(FILE, JSON.stringify({ ...v, blocks, mac_line: mac, ...said }, null, 2) + "\n");
    }
    expect(blocks).toEqual(v.blocks);
    expect(mac).toBe(v.mac_line);
    expect(said).toEqual({ host_refused: v.host_refused, client_changed: v.client_changed, no_cryptography: v.no_cryptography,
                           block_waits: v.block_waits, ticket_ended: v.ticket_ended });
  });

  it("carries the sha256 of open_edition/ade_link.py, as the page's build computed it, and no other", () => {
    const v = vectors();
    for (const url of Object.keys(v.blocks)) {
      const block = pairLine(answer(v, url), { name: v.name, kind: "cloud" }) ?? "";
      expect(block.match(/[0-9a-f]{64}/g), url).toEqual([SHA256]);
    }
  });
});

const T = Math.floor(Date.now() / 1000);
const FP = "e3d8bd098e5d4d42a9c1bd5e44fc1b41";
type Answer = [number, unknown];
type Route = (body: Record<string, unknown> | undefined) => Answer;
/** The server as the test has it answer: each "METHOD /path" its route; anything else 404. */
function serve(routes: Record<string, Route>) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    const route = routes[`${method} ${url}`];
    const [status, data] = route ? route(init.body ? JSON.parse(String(init.body)) : undefined)
      : [404, { detail: `no route for ${method} ${url}` }];
    return new Response(JSON.stringify(data), { status });
  }));
}
const link: LinkView = {
  id: "L1", name: "MacBook Pro", kind: "local", session_ref: null, interval_min: 15, created_at: T - 3600,
  last_seen_at: T - 60, last_heartbeat_at: T - 60, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0,
  checkpoint_at: null, events: 0, last_event: null, commands: [], stopped_at: null,
};
/** The server's routes, its ticket's answer naming `url` as the address the session links to. */
const routes = (url: string): Record<string, Route> => ({
  "GET /api/ade/link/sessions": () => [200, { links: [link], questions: [], public_url: "https://pp.example",
                                              connect_hold: 180, move_hold: 180 }],
  "GET /api/ade/link/history": () => [200, { sessions: [] }],
  "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
  "POST /api/account/confirm": (b) => (b?.password === "right one" ? [200, { confirmation: "c7" }] : [403, { detail: "no" }]),
  "POST /api/ade/link/tickets": (b) => (b?.confirmation === "c7"
    ? [200, { ticket: "PP-ABCDEFGH", expires_at: T + 600, url, keys: [FP], e2e: null }]
    : [403, { detail: "no" }]),
});

/** The ticket's card for a session named `name`, on this Mac or on claude.ai, as the owner gets to it. */
async function linking(name: string, where: "This Mac" | "claude.ai", url = "https://pp.example") {
  serve(routes(url));
  render(<Channel onClose={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /Link a session/ }));
  fireEvent.change(screen.getByLabelText("What to call it"), { target: { value: name } });
  fireEvent.click(screen.getByRole("radio", { name: new RegExp(where) }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  const word = await screen.findByRole("group", { name: `Linking \u201c${name}\u201d` });
  fireEvent.change(within(word).getByLabelText("Your password"), { target: { value: "right one" } });
  fireEvent.click(within(word).getByRole("button", { name: "Confirm" }));
  await screen.findByText(/PP-ABCDEFGH/, { selector: "pre" });
  return screen.getByRole("region", { name: `Linking ${name}` });
}

beforeEach(() => {
  useStore.setState({ user: { id: "u", email: "o@example.org", can_sign_in_again: true } });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  useStore.setState(useStore.getInitialState());
});

describe("linking a session on claude.ai", () => {
  it("says the one setting a claude.ai environment needs above the block, its host filled in, and not for this Mac", async () => {
    const said = vectors().host_refused.split("{host}").join("pp.example");
    const card = await linking("Planner", "claude.ai");
    const setting = within(card).getByText(said);
    const block = card.querySelector("pre");
    expect(block?.textContent).toContain("--kind cloud");
    expect(setting.compareDocumentPosition(block as Node) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    cleanup();
    const mac = await linking("Studio", "This Mac");
    expect(mac.querySelector("pre")?.textContent).toContain("--kind local");
    expect(mac.textContent).not.toContain("Allowed domains");
  }, 20000);                                  // two links made in turn, each as the owner makes it

  it("says no setting above the block where the ADE is on this machine's loopback, which no environment's list reaches", async () => {
    const card = await linking("Planner", "claude.ai", "http://127.0.0.1:8765");
    expect(card.querySelector("pre")?.textContent).toContain("--kind cloud");
    // the block's own program holds the sentence, for the refusal it says it at: no element says it alone
    expect(within(card).queryByText(vectors().host_refused.split("{host}").join("127.0.0.1"))).toBeNull();
  });
});

// CLOUD-LINK-2 (queue 4.319): on release 64 the owner reached the block before the setting, met the environment's
// refusal of the ADE's host, and asked for it simpler. The claude.ai card reads as two numbered steps: the
// environment's one setting first (once per environment: hostRefused's words, the host in a Copy button of its own,
// Anthropic's page on cloud environments linked), the block second. Where no environment's list reaches the ADE (this
// machine's loopback) and for a session on this Mac there is no setting step.
const DOCS = "https://code.claude.com/docs/en/cloud-environments#allow-specific-domains";

/** The card's numbered steps, in order: the items of its ordered list named "Two steps". */
const steps = (card: HTMLElement) => within(within(card).getByRole("list", { name: "Two steps" })).getAllByRole("listitem");

describe("linking a session on claude.ai, in two numbered steps", () => {
  it("gives the environment's setting as step 1, in hostRefused's words, and the block as step 2", async () => {
    const card = await linking("Planner", "claude.ai");
    const list = within(card).getByRole("list", { name: "Two steps" });
    expect(list.tagName).toBe("OL");
    const [one, two, ...more] = steps(card);
    expect(more).toEqual([]);
    // an environment whose network is Full reaches the host already: step 1 is for one that does not
    expect(one.textContent).toContain("Once per environment, unless it reaches pp.example already:");
    expect(within(one).getByText(hostRefused("pp.example"))).toBeTruthy();
    expect(within(one).queryByLabelText("The block to paste")).toBeNull();
    expect(within(two).getByLabelText("The block to paste").textContent).toContain("--kind cloud");
    expect(two.textContent).toContain("paste this block into the claude.ai session:");
    expect(within(two).getByRole("button", { name: /Copy the block/ })).toBeTruthy();
    expect(two.textContent).toMatch(/Waiting for the session to pair: the ticket works once, until \d{2}:\d{2}: \d+:\d{2} left\./);
    // the header names the two steps and asks for no paste before step 1
    const header = card.firstElementChild as HTMLElement;
    expect(header.textContent).toContain("To link \u201cPlanner\u201d from claude.ai, two steps:");
    expect(header.textContent).not.toContain("paste this block");
  });

  it("puts the host in a Copy button of its own, which copies the host alone, and links Anthropic's page", async () => {
    const written: string[] = [];
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: async (t: string) => { written.push(t); } }, configurable: true,
    });
    const card = await linking("Planner", "claude.ai");
    const [one, two] = steps(card);
    const hostCopy = within(one).getByRole("button", { name: "Copy pp.example" });
    // a long host is cut short in the button, whole in its title; the button is described by the setting's words
    expect(hostCopy.getAttribute("title")).toBe("pp.example");
    expect(document.getElementById(hostCopy.getAttribute("aria-describedby") ?? "")?.textContent).toBe(hostRefused("pp.example"));
    fireEvent.click(hostCopy);
    expect(written).toEqual(["pp.example"]);
    // its own Copied, which keeps the host in its name; the block's Copy is untouched
    expect(within(one).getByRole("button", { name: "Copied pp.example" })).toBeTruthy();
    expect(within(two).getByRole("button", { name: /Copy the block/ })).toBeTruthy();
    const page = within(one).getByRole("link", { name: /cloud environments/ });
    expect(page.getAttribute("href")).toBe(DOCS);
    expect(page.getAttribute("target")).toBe("_blank");
    expect(page.getAttribute("rel")?.split(/\s+/)).toEqual(expect.arrayContaining(["noopener", "noreferrer"]));
    for (const word of ["Network access", "Limited", "Allowed domains", "Allow package managers"]) {
      expect(one.textContent, word).toContain(word);
    }
  });

  it("copies the host as Allowed domains takes it, in lower case and without its port", async () => {
    const written: string[] = [];
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: async (t: string) => { written.push(t); } }, configurable: true,
    });
    const card = await linking("Planner", "claude.ai", "https://PP.Example:8443");
    const [one] = steps(card);
    expect(within(one).getByText(hostRefused("pp.example"))).toBeTruthy();
    fireEvent.click(within(one).getByRole("button", { name: "Copy pp.example" }));
    expect(written).toEqual(["pp.example"]);
  });

  it("gives a claude.ai link linked again the same two steps, under a header that says again", async () => {
    const cloud: LinkView = { ...link, id: "L2", name: "Planner", kind: "cloud", session_ref: "https://claude.ai/code/session_x" };
    const r = routes("https://pp.example");
    r["GET /api/ade/link/sessions"] = () => [200, { links: [cloud], questions: [], public_url: "https://pp.example",
                                                     connect_hold: 180, move_hold: 180 }];
    serve(r);
    render(<Channel onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "More for Planner" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Link again/ }));
    const word = await screen.findByRole("group", { name: "Linking Planner again" });
    fireEvent.change(within(word).getByLabelText("Your password"), { target: { value: "right one" } });
    fireEvent.click(within(word).getByRole("button", { name: "Confirm" }));
    await screen.findByText(/PP-ABCDEFGH/, { selector: "pre" });
    const card = screen.getByRole("region", { name: "Linking Planner" });
    expect(steps(card)).toHaveLength(2);
    const header = card.firstElementChild as HTMLElement;
    expect(header.textContent).toContain("To link Planner again from claude.ai, two steps:");
    expect(header.textContent).not.toContain("paste this block");
  }, 20000);

  it("says why there is no block, as before, where a claude.ai ticket's answer carries no address", async () => {
    const r = routes("https://pp.example");
    r["POST /api/ade/link/tickets"] = () => [200, { ticket: "PP-ABCDEFGH", expires_at: T + 600, url: null, keys: [FP], e2e: null }];
    serve(r);
    render(<Channel onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Link a session/ }));
    fireEvent.change(screen.getByLabelText("What to call it"), { target: { value: "Planner" } });
    fireEvent.click(screen.getByRole("radio", { name: /claude\.ai/ }));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    const word = await screen.findByRole("group", { name: "Linking \u201cPlanner\u201d" });
    fireEvent.change(within(word).getByLabelText("Your password"), { target: { value: "right one" } });
    fireEvent.click(within(word).getByRole("button", { name: "Confirm" }));
    const card = await screen.findByRole("region", { name: "Linking Planner" });
    expect(within(card).getByRole("alert").textContent).toBe(NO_LINE);
    expect(within(card).queryByRole("list", { name: "Two steps" })).toBeNull();
    expect(within(card).queryByRole("button", { name: /Copy/ })).toBeNull();
  });

  it("says both steps to a screen reader as the card comes, the setting first, and the block alone for the loopback", async () => {
    const card = await linking("Planner", "claude.ai");
    expect((await within(card).findByText(/^The ticket is made\./)).textContent).toContain("The ticket is made. First, "
      + "once per environment, let the environment reach pp.example if it does not already; then copy the block and "
      + "paste it into the claude.ai session; pasted first, the block waits for that while the ticket lives, so either "
      + "order works; the ticket works once, until ");
    cleanup();
    const loop = await linking("Planner", "claude.ai", "http://127.0.0.1:8765");
    expect((await within(loop).findByText(/^The ticket is made\./)).textContent).toContain("The ticket is made. Copy the "
      + "block and paste it into the claude.ai session; the ticket works once, until ");
  }, 20000);                                  // two links made in turn

  it("takes the keyboard to step 1's Copy as the ticket is made, and to the block's Copy where there is no setting step", async () => {
    const card = await linking("Planner", "claude.ai");
    const [one] = steps(card);
    await vi.waitFor(() => expect(document.activeElement).toBe(within(one).getByRole("button", { name: "Copy pp.example" })));
    cleanup();
    const loop = await linking("Planner", "claude.ai", "http://127.0.0.1:8765");
    await vi.waitFor(() => expect(document.activeElement).toBe(within(loop).getByRole("button", { name: /Copy the block/ })));
  }, 20000);                                  // two links made in turn

  // CLOUD-LINK-3 (queue 4.320): the block waits for step 1 while the ticket lives, so the steps can come in either order
  it("says in step 2 that the block, pasted first, waits for step 1 while the ticket lives and then goes on", async () => {
    const card = await linking("Planner", "claude.ai");
    const [, two] = steps(card);
    expect(two.textContent).toContain("Pasted before step 1 is done, the block waits for it, trying again every 10 "
      + "seconds while the ticket lives, and then goes on by itself: the two steps can come in either order.");
    cleanup();
    const loop = await linking("Planner", "claude.ai", "http://127.0.0.1:8765");
    expect(loop.textContent).not.toContain("Pasted before step 1");   // no setting step there, so no such sentence
    cleanup();
    const mac = await linking("Studio", "This Mac");
    expect(mac.textContent).not.toContain("Pasted before step 1");    // nor for a session on this Mac
  }, 30000);                                  // three links made in turn

  // CLOUD-LINK-3, item 4 (queue 4.320, 22:19 UTC): about two minutes on, the ticket alive and no pairing come from it,
  // the card puts the setting back above the block, with the host's Copy, saying the session has not reached the ADE.
  // It is in the ticket's card, which becomes the pairing's as it pairs (ChannelUx.test.tsx), and goes as the ticket ends.
  it("reminds of the setting above the block two minutes on while no pairing has come, until the ticket ends", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: Date.now() });     // the card's clock alone, moved by the case
    const start = Date.now();                                     // the tickets the server gives live 600 s from here
    const words = "The session has not reached the ADE yet. In a claude.ai session the usual cause is its environment's "
      + "network setting:";
    try {
      const card = await linking("Planner", "claude.ai");
      const [, two] = steps(card);
      const reminder = () => within(card).queryByRole("note", { name: "The session has not reached the ADE" });
      // the card's live regions, on the page from the start: one of them says the reminder, so it is announced
      const live = within(card).getAllByRole("status");
      const announced = () => live.some((s) => s.textContent === `${words} ${hostRefused("pp.example")}`);
      const tick = () => new Promise((r) => setTimeout(r, 1200));   // the card reads its clock every second
      expect(reminder()).toBeNull();
      expect(announced()).toBe(false);
      vi.setSystemTime(start + 110_000);                          // not two minutes yet
      await tick();
      expect(reminder()).toBeNull();
      vi.setSystemTime(start + 125_000);                          // two minutes on
      await vi.waitFor(() => expect(reminder()).not.toBeNull(), { timeout: 3000 });
      const said = reminder() as HTMLElement;
      expect(two.contains(said)).toBe(true);
      expect(said.textContent).toContain(words);
      expect(within(said).getByText(hostRefused("pp.example"))).toBeTruthy();
      expect(within(said).getByRole("button", { name: "Copy pp.example" })).toBeTruthy();
      const block = within(two).getByLabelText("The block to paste");
      expect(said.compareDocumentPosition(block) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      await vi.waitFor(() => expect(announced()).toBe(true), { timeout: 3000 });
      vi.setSystemTime(start + 601_000);                          // the ticket ended
      await vi.waitFor(() => expect(reminder()).toBeNull(), { timeout: 3000 });
      expect(card.textContent).toContain("The ticket expired at");
      cleanup();
      vi.setSystemTime(start);                                    // a new card, its ticket alive
      const loop = await linking("Planner", "claude.ai", "http://127.0.0.1:8765");    // no setting there to remind of
      vi.setSystemTime(start + 125_000);
      await tick();
      expect(loop.textContent).toContain("Waiting for the session to pair");          // the ticket still lives
      expect(within(loop).queryByRole("note", { name: "The session has not reached the ADE" })).toBeNull();
      expect(loop.textContent).not.toContain(words);
    } finally {
      vi.useRealTimers();
    }
  }, 30000);

  it("gives no setting step where no environment's list reaches the ADE, nor for a session on this Mac", async () => {
    const card = await linking("Planner", "claude.ai");     // a public host: the two steps (the contrast)
    expect(steps(card)).toHaveLength(2);
    cleanup();
    const loop = await linking("Planner", "claude.ai", "http://127.0.0.1:8765");
    expect(within(loop).queryByRole("list", { name: "Two steps" })).toBeNull();
    expect(within(loop).queryByRole("button", { name: /^Copy 127/ })).toBeNull();
    expect(within(loop).queryByRole("link", { name: /cloud environments/ })).toBeNull();
    expect(loop.querySelector("pre")?.textContent).toContain("--kind cloud");
    expect(loop.textContent).toContain("paste this block into the claude.ai session:");
    cleanup();
    const mac = await linking("Studio", "This Mac");
    expect(within(mac).queryByRole("list", { name: "Two steps" })).toBeNull();
    expect(mac.textContent).not.toContain("Allowed domains");
  }, 30000);                                  // three links made in turn, each as the owner makes it
});
