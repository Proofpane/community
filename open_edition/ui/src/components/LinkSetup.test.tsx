// The line the page gives to link a session is `ade_link.py
// setup`, which links it and puts its hooks in its settings itself; and a link that has said nothing yet says that a
// session linked that way has its hooks already, before the block to add by hand, which stays for a session linked
// before.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// passkeys work here, as in a browser at the server's own address (the composer under a conversation offers to sign)
vi.hoisted(() => {
  const get = vi.fn(async () => null);
  (globalThis as unknown as { PublicKeyCredential: unknown }).PublicKeyCredential = function PublicKeyCredential() {};
  Object.defineProperty(navigator, "credentials", { value: { get }, configurable: true });
});

import type { LinkView } from "../api";
import { useStore } from "../store";
import { Channel, CLIENT, Conversation, pairLine } from "./Channel";

const T = Math.floor(Date.now() / 1000);

type Answer = [number, unknown];
type Route = () => Answer;
/** The server as the test has it answer: each "METHOD /path" its route; anything else 404. */
function serve(routes: Record<string, Route>) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    const route = routes[`${method} ${url}`];
    const [status, data] = route ? route() : [404, { detail: `no route for ${method} ${url}` }];
    return new Response(JSON.stringify(data), { status });
  }));
}

const link = (over: Partial<LinkView> = {}): LinkView => ({
  id: "L1", name: "MacBook Pro", kind: "local", session_ref: null, interval_min: 15, created_at: T - 3600,
  last_seen_at: T - 60, last_heartbeat_at: T - 60, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0,
  checkpoint_at: null, events: 0, last_event: null, commands: [], stopped_at: null, ...over,
});

beforeEach(() => {
  useStore.setState({ user: { id: "u", email: "o@example.org", can_sign_in_again: true } });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  useStore.setState(useStore.getInitialState());
});

const OUT = { ticket: "PP-ABCDEFGH", expires_at: T + 600, url: "https://pp.example" };

describe("linking a session with one line", () => {
  it("gives setup as the line that links a session on this Mac", () => {
    expect(pairLine(OUT, { name: "mac", kind: "local" }))
      .toBe(`${CLIENT} setup --url https://pp.example --ticket PP-ABCDEFGH --name 'mac' --kind local --mirror`);
  });

  it("gives setup for a session in the cloud too, which it links and gives no hook", () => {
    expect(pairLine(OUT, { name: "web", kind: "cloud" }))
      .toBe(`${CLIENT} setup --url https://pp.example --ticket PP-ABCDEFGH --name 'web' --kind cloud`
        + ` --ref "https://claude.ai/code/session_\${CLAUDE_CODE_REMOTE_SESSION_ID#cse_}"`);
  });

  it("says a session linked with setup has its hooks already, above the block to add by hand", async () => {
    serve({
      "GET /api/ade/link/sessions": () => [200, { links: [link()], questions: [], public_url: "https://pp.example" }],
      "GET /api/ade/link/history": () => [200, { sessions: [] }],
      "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [] }],
    });
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    const said = await screen.findByText("Linked with setup? Its hooks are in its settings already; with --mirror, "
      + "its next turn shows here.");
    const block = screen.getByLabelText("The settings to add");
    // above the block and its words, which stay for a session linked before setup
    expect(said.compareDocumentPosition(block) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("says nothing of setup for a session in the cloud, which loads no hook", async () => {
    const web = link({ kind: "cloud", name: "web" });
    serve({
      "GET /api/ade/link/sessions": () => [200, { links: [web], questions: [], public_url: "https://pp.example" }],
      "GET /api/ade/link/history": () => [200, { sessions: [] }],
      "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [] }],
    });
    render(<Conversation l={web} onBack={() => {}} onClose={() => {}} />);
    await screen.findByText(/Nothing said yet\. A session in the cloud/);
    expect(screen.queryByText(/Linked with setup/)).toBeNull();
  });
});

describe("where the line runs, since it puts the hooks in the folder it runs in", () => {
  /** The panel, its link made: the session named Studio, where it runs, the password's word, then the line's card. */
  async function cardFor(where: RegExp) {
    serve({
      "GET /api/ade/link/sessions": () => [200, { links: [link()], questions: [], public_url: "https://pp.example" }],
      "GET /api/ade/link/history": () => [200, { sessions: [] }],
      "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
      "POST /api/account/confirm": () => [200, { confirmation: "c7" }],
      "POST /api/ade/link/tickets": () => [200, { ...OUT, keys: [] }],
    });
    render(<Channel onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Link a session/ }));
    fireEvent.change(screen.getByLabelText("What to call it"), { target: { value: "Studio" } });
    fireEvent.click(screen.getByLabelText(where));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.change(await screen.findByLabelText("Your password"), { target: { value: "right one" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await screen.findByRole("button", { name: /Copy the line/ });
    return screen.getByRole("region", { name: "Linking Studio" });
  }
  const said = () => screen.getAllByRole("status").map((el) => el.textContent).join("\n");

  it("asks for a session on this Mac to run it in the folder the session runs in, and says what it puts there", async () => {
    const card = await cardFor(/^This Mac/);
    expect(card.textContent).toContain("run this line on this Mac, in a terminal in the folder the session runs in "
      + "(in the Claude Code session itself, in the background: it waits out the hold):");
    expect(card.textContent).toContain("It also puts the session's hooks in the settings file Claude Code reads for "
      + "that folder (at the repository's root, or the main checkout's for a worktree), and says which file and what "
      + "it changed.");
    expect(card.textContent).toContain("--mirror adds the mirror too: each turn's words and each tool call's line "
      + "come to this page, sealed once Encryption is on; take it out of the line to link without them.");
    // Said puts its words in once its card is drawn, from an effect: waited for, since read in the same turn as the
    // card's appearance it raced the page's own scheduled work
    await vi.waitFor(() => expect(said()).toContain("Copy the line and run it on this Mac, in a terminal in the folder "
      + "the session runs in (in the Claude Code session itself, in the background: it waits out the hold); the ticket works once"));
  });

  it("says nothing of a folder or hooks for a session on claude.ai, which reads no local settings file", async () => {
    const card = await cardFor(/^claude\.ai/);
    expect(card.textContent).toContain("run this line in the claude.ai session:");
    expect(card.textContent).not.toMatch(/folder the session runs in|settings file|--mirror/);
  });

  // a session on claude.ai runs no hook of the ADE's, and the card says how its link
  // is kept heard there, and when it is not; a session on this Mac is heard through its hook, and is told nothing of it
  const keptHeard = "A session on claude.ai runs no hook of the ADE's: the link client keeps its link heard with a "
    + "heartbeat at the link's interval while the session runs, which ends when the session ends; after its container "
    + "restarts, the session's next post through the client starts it again.";
  it("says how a session on claude.ai is kept heard, which runs no hook of the ADE's", async () => {
    expect((await cardFor(/^claude\.ai/)).textContent).toContain(keptHeard);
  });
  it("says nothing of it for a session on this Mac, which is heard through its hook", async () => {
    expect((await cardFor(/^This Mac/)).textContent).not.toContain("runs no hook of the ADE's");
  });
});
