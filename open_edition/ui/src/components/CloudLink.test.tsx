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
import { CLIENT_CHANGED, hostRefused, NO_CRYPTOGRAPHY } from "../cloudLink";
import { Channel, pairLine, type TicketOut } from "./Channel";

const FILE = join(__dirname, "../../../tests/vectors/cloud_link_page.json");
const SHA256 = createHash("sha256").update(readFileSync(join(__dirname, "../../../ade_link.py"))).digest("hex");
const WRITTEN = "@CLIENT_SHA256@";            // where the file writes the client's sha256, which changes with it

interface Vectors {
  about: string; answer: Omit<TicketOut, "url">; name: string; blocks: Record<string, string>; mac_line: string;
  host_refused: string; client_changed: string; no_cryptography: string;
}
const vectors = (): Vectors => JSON.parse(readFileSync(FILE, "utf8"));
const answer = (v: Vectors, url: string): TicketOut => ({ ...v.answer, url });

describe("the block for a session on claude.ai", () => {
  it("is the vectors file's, the client's sha256 written in its place, and this Mac's line is as it was", () => {
    const v = vectors();
    const blocks = Object.fromEntries(Object.keys(v.blocks).map((url) =>
      [url, pairLine(answer(v, url), { name: v.name, kind: "cloud" })?.split(SHA256).join(WRITTEN)]));
    const mac = pairLine(answer(v, "https://ade.example.test"), { name: v.name, kind: "local" });
    const said = { host_refused: hostRefused("{host}"), client_changed: CLIENT_CHANGED, no_cryptography: NO_CRYPTOGRAPHY };
    if (process.env.CLOUD_LINK_WRITE_VECTORS) {
      writeFileSync(FILE, JSON.stringify({ ...v, blocks, mac_line: mac, ...said }, null, 2) + "\n");
    }
    expect(blocks).toEqual(v.blocks);
    expect(mac).toBe(v.mac_line);
    expect(said).toEqual({ host_refused: v.host_refused, client_changed: v.client_changed, no_cryptography: v.no_cryptography });
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
