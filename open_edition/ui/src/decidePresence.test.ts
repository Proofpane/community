import { afterEach, describe, expect, it, vi } from "vitest";

// Approving a held command asks the phone for the person (Face ID, Touch ID or the passcode: presence.ts) before it is
// sent, on both doors: the command's own Approve (Api.decide) and an answer to an app's proposal (Api.decideProposed).
// Every approval carries a confirmation, so the proposal door does not skip the phone's question
// when one is given. Denying asks nothing of the phone.

const presence = vi.hoisted(() => ({ confirmPresence: vi.fn(async (_reason: string) => {}) }));
vi.mock("./presence", () => presence);

import { Api } from "./api";

const sent: { url: string; body: unknown }[] = [];
const answering = () => vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
  sent.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
  return new Response(JSON.stringify({ state: "approved" }), { status: 200 });
}));

afterEach(() => { vi.unstubAllGlobals(); presence.confirmPresence.mockClear(); sent.length = 0; });

describe("approving a held command asks the phone for the person, on both doors", () => {
  it("the proposal door, with the confirmation made for the command", async () => {
    answering();
    await Api.decideProposed("req-1", true, "prop-1", "conf-1");
    expect(presence.confirmPresence).toHaveBeenCalledTimes(1);
    expect(presence.confirmPresence).toHaveBeenCalledWith("Approve the held command");
    expect(sent).toEqual([{ url: "/api/ade/approvals/req-1", body: { approve: true, proposal: "prop-1", confirmation: "conf-1" } }]);
  });

  it("the command's own Approve, with its confirmation", async () => {
    answering();
    await Api.decide("req-1", true, "conf-1");
    expect(presence.confirmPresence).toHaveBeenCalledWith("Approve the held command");
    expect(sent[0].body).toEqual({ approve: true, confirmation: "conf-1" });
  });

  it("nothing is sent when the phone's question is not answered", async () => {
    answering();
    presence.confirmPresence.mockRejectedValueOnce(new Error("Not approved"));
    await expect(Api.decideProposed("req-1", true, "prop-1", "conf-1")).rejects.toThrow("Not approved");
    expect(sent).toEqual([]);
  });

  it("denying asks nothing of the phone, on either door", async () => {
    answering();
    await Api.decideProposed("req-1", false, "prop-1");
    await Api.decide("req-2", false);
    expect(presence.confirmPresence).not.toHaveBeenCalled();
    expect(sent.map((s) => s.body)).toEqual([{ approve: false, proposal: "prop-1" }, { approve: false }]);
  });
});
