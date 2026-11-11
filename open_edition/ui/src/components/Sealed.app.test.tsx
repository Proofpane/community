import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { HistorySession, LinkView } from "../api";
import { eventSlot, historySlot } from "../e2e";
import { useStore } from "../store";
import { LinkCard } from "./Channel";
import { MachineSessions } from "./History";

// The desktop and phone apps (APP) cannot unlock the keys, whatever the account's state: their passkeys are not this
// site's. A stand-in for e2e.ts records what the page asks it to open.
const fake = vi.hoisted(() => ({ state: "locked" as "locked" | "unlocked", opened: [] as string[] }));

vi.mock("../api", async (importOriginal) => ({ ...(await importOriginal<typeof import("../api")>()), APP: true }));
vi.mock("../e2e", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../e2e")>()),
  useE2E: () => ({ state: fake.state, on: true, generation: fake.state === "locked" ? 1 : 2, unlock: async () => {},
                   unlockWithCode: async () => {}, lock: () => {} }),
  openSealed: async (envelope: string) => { fake.opened.push(envelope); return { ok: false, why: "not here" }; },
}));

const seal = (value: unknown, slot: string) => `e2e1.${Buffer.from(JSON.stringify({ slot, value }), "utf8").toString("base64url")}`;
const link = (over: Partial<LinkView> = {}): LinkView => ({
  id: "L1", name: "mac", kind: "local", session_ref: null, interval_min: 5, created_at: 1, last_seen_at: null,
  last_heartbeat_at: null, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0, checkpoint_at: null,
  events: 1, last_event: null, commands: [], stopped_at: null, ...over,
});

afterEach(() => { cleanup(); localStorage.clear(); fake.opened.length = 0; useStore.setState(useStore.getInitialState()); });

describe("sealed text in the desktop and phone apps", () => {
  it("says it is encrypted and to read it in a browser at the ADE's address, offers no Unlock and opens nothing", () => {
    localStorage.setItem("pp-open-server", "https://pp.example");
    useStore.setState({ user: { id: "acct-1", email: "o@example.org", can_sign_in_again: true } });
    const CID = "0123456789abcdef0123456789abcdef";
    const last = { seq: 5, time: "2026-10-08T00:00:00Z", kind: "step", from: "mac", to: "owner", ref: null, from_id: "L1", client_id: CID,
      text: seal({ text: "ran the tests", session: null, said_at: null }, eventSlot("acct-1", "L1", "step", undefined, null, CID)) };
    const row: HistorySession = { id: "s1", title: "", project: null, branch: null, started_at: 1, last_at: 2, link_id: "L1", link: "mac",
      kind: "local", listed_at: 3, sealed: seal({ title: "t", project: null, branch: null, repositories: null }, historySlot("acct-1", "L1", "s1", false)) };
    for (const state of ["locked", "unlocked"] as const) {
      fake.state = state;
      render(<><LinkCard l={link({ last_event: last })} questions={[]} onTicket={() => {}} /><MachineSessions sessions={[row]} /></>);
      expect(screen.getAllByText("Encrypted: read it in a browser at pp.example")).toHaveLength(2);
      expect(screen.queryByRole("button", { name: /Unlock/ })).toBeNull();
      expect(document.body.textContent).not.toContain("ran the tests");
      // the page's markup as a whole, its attributes too, read without the HTML sinks the app's sources never name
      expect(new XMLSerializer().serializeToString(document.body)).not.toContain("e2e1.");
      cleanup();
    }
    expect(fake.opened).toEqual([]);
  });
});
