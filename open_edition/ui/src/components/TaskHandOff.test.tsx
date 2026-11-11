import { createHash } from "node:crypto";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Agent } from "../agents";
import { useStore } from "../store";
import type { HandOffTask, OfferPacket, OfferView, SecretFinding, SecretHold } from "../taskOffersApi";
import { clock } from "../when";
import { OffersWaiting, TaskHandOff } from "./TaskHandOff";

// RiskTerms is faked by a marker that shows the props this part gives it, so a test sees which risk point's text opens
// over which control, with which cases and owner, and when; the real one renders the text and never the control.
vi.mock("./RiskTerms", async () => {
  const { createElement } = await import("react");
  return {
    RiskTerms: (p: { point: number; kind: string; open?: boolean; cases?: string[]; owner?: string }) =>
      createElement("div", { "data-testid": "risk", "data-point": String(p.point), "data-kind": p.kind, "data-open": String(!!p.open),
                             "data-cases": (p.cases ?? []).join(","), "data-owner": p.owner ?? "" }),
  };
});

type Route = (body: Record<string, unknown> | undefined) => [number, unknown];
type Call = { path: string; method: string; body: Record<string, unknown> | undefined };
let calls: Call[] = [];

// the server, at fetch, as the page's api() reaches it: each route by its method and path, and every call kept
function serve(routes: Record<string, Route>) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path: String(url), method, body });
    const route = routes[`${method} ${url}`];
    const [status, data] = route ? route(body) : [404, { detail: `no route for ${method} ${url}` }];
    return new Response(JSON.stringify(data), { status });
  }));
}

const B = "/api/ade/teammate";
const TASK = "0123456789abcdef0123456789abcdef";
const TASK_B = "fedcba9876543210fedcba9876543210";
const DRAFT = "d0d0d0d0d0d0d0d0d0d0d0d0d0d0d0d0";
const DRAFT2 = "d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1";
const OFFER = "e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0";
const T = 1_790_000_000;
// the server's own words (task_offers.py): the track off for want of an operator, a version that differs, a draft gone
const OFF = "This deployment does not name its operator, so offers and custody are off.";
const CHANGED = "The task changed; look again.";
const DRAFT_GONE = "The draft has expired: make it again.";
const SEEN = "If that address is someone's here, they will see your request.";
const HIDDEN = String.fromCodePoint(0x202e);

const task = (over: Partial<HandOffTask> = {}): HandOffTask => ({
  id: TASK, agent_id: "a1a2a3a4a5a6", state: "suspended", state_version: 7, title: "Fix the parser",
  repo_address: "https://github.com/acme/demo", continued_from: null, custody_confirmed: false, custody_cross: false,
  crossings_ended: false, offer: null, ...over,
});
const offer = (over: Partial<OfferView> = {}): OfferView => ({
  id: OFFER, kind: "pressed", state: "offered", task_id: "f".repeat(32), title: "Fix the parser",
  goal: "Make the parser take trailing commas.",
  steps: [{ n: 1, objective: "Write the failing test", check: null }, { n: 2, objective: "Fix it", check: "npm test -- parser" }],
  from_email: "owner@example.org", to_email: null, repo_slug: "acme/demo", branch: `pp/task-${"f".repeat(32)}`, commit: "a".repeat(40),
  packet_digest: "b".repeat(64), loaded_changes: [], hold: null, why: null, created_at: T, expires_at: T + 7 * 86400,
  terms: null, other_group_agents: [], ...over,
});
const agent = (id: string, name: string, repo: string | null, retired = false) => ({ id, name, repo, retired } as unknown as Agent);
const AGENTS = [agent("a00000000001", "Builder", "demo"), agent("a00000000002", "Seat agent", "demo"),
                agent("a00000000003", "Other repo", "elsewhere"), agent("a00000000004", "Retired one", "demo", true),
                agent("a00000000005", "Scratch", null)];

// the owner's custody as the pane reads it: a custodian set, so the task's custody controls are offered
const CUSTODIAN = { id: "c".repeat(32), state: "accepted", owner_email: "owner@example.org", custodian_email: "t@example.org",
                    confirmed_at: T, agents: [], why: null, created_at: T, updated_at: T, terms: null };
const routes = (over: Record<string, Route> = {}): Record<string, Route> => ({
  [`GET ${B}/offers`]: () => [200, { made: [], received: [] }],
  [`GET ${B}/custody`]: () => [200, { as_owner: CUSTODIAN, as_custodian: [], tasks: [] }],
  "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
  "POST /api/account/confirm": () => [200, { confirmation: "conf-1" }],
  ...over,
});
/** What the page asked a confirmation for, as the server's /api/account/confirm received it. */
const confirmedFor = () => calls.filter((c) => c.path === "/api/account/confirm" && c.method === "POST").map((c) => c.body);
const posted = (path: string) => calls.find((c) => c.method === "POST" && c.path === path)?.body;
const reads = (path: string) => calls.filter((c) => c.method === "GET" && c.path === path).length;
/** The risk text's marker beside one control, by the control's group. */
const textOver = (group: string) => within(screen.getByRole("group", { name: group })).getByTestId("risk");

async function givePassword() {
  fireEvent.change(await screen.findByLabelText("Your password"), { target: { value: "pw" } });
  fireEvent.click(screen.getByRole("button", { name: /^Confirm$/ }));
}

/** The page's own intervals of `ms`, stood in for: each callback kept, to be run by hand, with the id it was given;
 *  every other interval, Testing Library's own polling among them, left as it is. */
function standInIntervals(ms: number) {
  const real = globalThis.setInterval;
  const ticks: { run: () => void; id: number }[] = [];
  vi.spyOn(globalThis as unknown as { setInterval: (run: () => void, wait?: number) => unknown }, "setInterval")
    .mockImplementation((run: () => void, wait?: number) => {
      if (wait !== ms) return real(run, wait);
      ticks.push({ run, id: 900_000 + ticks.length });
      return ticks[ticks.length - 1].id;
    });
  const cleared = vi.spyOn(globalThis as unknown as { clearInterval: (id: unknown) => void }, "clearInterval");
  return { ticks, cleared };
}

async function draftFor(address: string) {
  fireEvent.click(await screen.findByRole("button", { name: "Hand to a teammate" }));
  fireEvent.change(screen.getByLabelText("Your teammate's email address"), { target: { value: address } });
  fireEvent.click(screen.getByRole("button", { name: /Offer it/ }));
  await screen.findByLabelText("Your password");
}

afterEach(() => {
  cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); useStore.setState(useStore.getInitialState());
});

describe("a task's hand-off, on its owner's pane", () => {
  it("with the track off and nothing standing, says why in the read's words and offers nothing", async () => {
    serve(routes({
      [`GET ${B}/offers`]: () => [200, { made: [], received: [], off: OFF }],
      [`GET ${B}/custody`]: () => [200, { as_owner: CUSTODIAN, as_custodian: [], tasks: [], off: OFF }],
    }));
    render(<TaskHandOff task={task()} onChanged={() => {}} />);
    expect((await screen.findByRole("status")).textContent).toBe(OFF);
    expect(screen.queryAllByRole("button")).toEqual([]);
    expect(screen.queryAllByRole("checkbox")).toEqual([]);
    expect(screen.queryAllByTestId("risk")).toEqual([]);
  });

  it("when its first read fails otherwise, says the error and offers Try again, which reads again", async () => {
    let up = false;
    serve(routes({ [`GET ${B}/offers`]: () => (up ? [200, { made: [], received: [] }] : [502, { detail: "The app did not answer." }]) }));
    render(<TaskHandOff task={task()} onChanged={() => {}} />);
    expect((await screen.findByRole("alert")).textContent).toContain("The app did not answer.");
    expect(screen.queryByRole("button", { name: "Hand to a teammate" })).toBeNull();     // nothing offered before a read
    up = true;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("button", { name: "Hand to a teammate" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(reads(`${B}/offers`)).toBe(2);
  });

  it("when a later read fails otherwise, keeps what it showed, says the error, and Try again reads again", async () => {
    let up = true;
    serve(routes({ [`GET ${B}/offers`]: () => (up ? [200, { made: [], received: [] }] : [502, { detail: "The app did not answer." }]) }));
    render(<TaskHandOff task={task()} onChanged={() => {}} />);
    expect(await screen.findByRole("button", { name: "Confirm for custody" })).toBeTruthy();
    up = false;
    fireEvent.focus(window);
    expect((await screen.findByRole("alert")).textContent).toContain("The app did not answer.");
    expect(screen.getByRole("button", { name: "Hand to a teammate" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Confirm for custody" })).toBeTruthy();
    up = true;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("keeps custody's controls when the custody read fails after one answered", async () => {
    let up = true;
    serve(routes({ [`GET ${B}/custody`]: () => (up ? [200, { as_owner: CUSTODIAN, as_custodian: [], tasks: [] }] : [502, { detail: "The app did not answer." }]) }));
    render(<TaskHandOff task={task()} onChanged={() => {}} />);
    expect(await screen.findByRole("button", { name: "Confirm for custody" })).toBeTruthy();
    up = false;
    const before = reads(`${B}/custody`);
    fireEvent.focus(window);
    await waitFor(() => expect(reads(`${B}/custody`)).toBe(before + 1));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByRole("button", { name: "Confirm for custody" })).toBeTruthy();
  });

  it("reads again as the owner comes back to the page, so a custodian set meanwhile brings Confirm for custody", async () => {
    let own: unknown = null;
    serve(routes({ [`GET ${B}/custody`]: () => [200, { as_owner: own, as_custodian: [], tasks: [] }] }));
    render(<TaskHandOff task={task()} onChanged={() => {}} />);
    expect(await screen.findByRole("button", { name: "Hand to a teammate" })).toBeTruthy();
    await waitFor(() => expect(reads(`${B}/custody`)).toBe(1));
    expect(screen.queryByRole("button", { name: "Confirm for custody" })).toBeNull();
    own = CUSTODIAN;
    fireEvent.focus(window);
    expect(await screen.findByRole("button", { name: "Confirm for custody" })).toBeTruthy();
    expect(reads(`${B}/offers`)).toBe(2);
    fireEvent(document, new Event("visibilitychange"));
    await waitFor(() => expect(reads(`${B}/offers`)).toBe(3));
  });

  it("forgets a form and a draft opened on one task when the pane shows another", async () => {
    serve(routes({ [`POST ${B}/offers`]: () => [200, { offer: offer({ id: DRAFT, state: "draft", task_id: TASK }) }] }));
    const { rerender } = render(<TaskHandOff task={task()} onChanged={() => {}} />);
    await draftFor("t@example.org");
    expect(screen.getByRole("group", { name: "Handing this task to a teammate" })).toBeTruthy();
    rerender(<TaskHandOff task={task({ id: TASK_B, title: "Another task" })} onChanged={() => {}} />);
    expect(await screen.findByRole("button", { name: "Hand to a teammate" })).toBeTruthy();
    expect(screen.queryByLabelText("Your password")).toBeNull();
    expect(screen.queryByRole("group", { name: "Handing this task to a teammate" })).toBeNull();
    expect(screen.queryByLabelText("Your teammate's email address")).toBeNull();
    expect(textOver("Hand to a teammate").dataset.open).toBe("false");
  });

  it("opens risk point 12's text over Hand to a teammate each time its form opens, and the form works with it open", async () => {
    serve(routes({ [`POST ${B}/offers`]: () => [200, { offer: offer({ id: DRAFT, state: "draft", task_id: TASK }) }] }));
    render(<TaskHandOff task={task()} onChanged={() => {}} />);
    const hand = await screen.findByRole("button", { name: "Hand to a teammate" });
    expect({ ...textOver("Hand to a teammate").dataset }).toMatchObject({ point: "12", kind: "control", open: "false", cases: "" });
    fireEvent.click(hand);
    expect(textOver("Hand to a teammate").dataset.open).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(textOver("Hand to a teammate").dataset.open).toBe("false");
    fireEvent.click(hand);
    expect(textOver("Hand to a teammate").dataset.open).toBe("true");          // again: every time, not once
    // the form goes on with the text open: nothing waits on it, and no press of it is asked
    fireEvent.change(screen.getByLabelText("Your teammate's email address"), { target: { value: "t@example.org" } });
    fireEvent.click(screen.getByRole("button", { name: /Offer it/ }));
    await waitFor(() => expect(posted(`${B}/offers`)).toEqual({ task_id: TASK, state_version: 7, address: "t@example.org" }));
    expect(await screen.findByLabelText("Your password")).toBeTruthy();
    expect(textOver("Hand to a teammate").dataset.open).toBe("true");
  });

  it("says the same at once whatever the address, someone's or no one's, whatever the draft's answer", async () => {
    // the app answers a draft the same way for both; were its answers to differ, the page says the same all the same
    const drafts = [offer({ id: DRAFT, state: "draft", task_id: TASK, to_email: "t@example.org" }),
                    offer({ id: DRAFT2, state: "draft", task_id: TASK, to_email: null, from_email: null })];
    let n = 0;
    serve(routes({ [`POST ${B}/offers`]: () => [200, { offer: drafts[n++] }] }));
    render(<TaskHandOff task={task()} onChanged={() => {}} />);
    const shown: string[] = [];
    for (const address of ["t@example.org", "no.one@example.org"]) {
      await draftFor(address);
      const group = screen.getByRole("group", { name: "Hand to a teammate" });
      expect(within(group).getByRole("status").textContent).toBe(SEEN);
      shown.push(group.textContent ?? "");
      fireEvent.click(within(group).getByRole("button", { name: "Cancel" }));
    }
    expect(shown[1]).toBe(shown[0]);
    expect(shown[0]).not.toContain("t@example.org");
  });

  it("confirms the offer for its draft, not for the task, then shows its hold, whose Cancel withdraws it", async () => {
    const at = Math.floor(Date.now() / 1000) + 180;
    const held = offer({ id: DRAFT, state: "held", task_id: TASK, from_email: null, to_email: "t@example.org",
                         hold: { id: "h".repeat(32), kind: "task.offer", takes_effect_at: at } });
    let confirmed = false;
    serve(routes({
      [`GET ${B}/offers`]: () => [200, { made: confirmed ? [held] : [], received: [] }],
      [`POST ${B}/offers`]: () => [200, { offer: offer({ id: DRAFT, state: "draft", task_id: TASK }) }],
      [`POST ${B}/offers/${DRAFT}/confirm`]: () => { confirmed = true; return [200, { offer: held, hold: held.hold }]; },
      [`POST ${B}/offers/${DRAFT}/withdraw`]: () => [200, { offer: { ...held, state: "withdrawn", hold: null } }],
    }));
    const onChanged = vi.fn();
    render(<TaskHandOff task={task()} onChanged={onChanged} />);
    await draftFor("t@example.org");
    expect(posted(`${B}/offers/${DRAFT}/confirm`)).toBeUndefined();          // nothing confirmed before the owner's word
    await givePassword();
    await waitFor(() => expect(posted(`${B}/offers/${DRAFT}/confirm`)).toEqual({ confirmation: "conf-1" }));
    expect(confirmedFor()).toEqual([{ password: "pw", change: `task-offer:${DRAFT}` }]);
    expect(await screen.findByText(`Your offer to t@example.org goes ahead at ${clock(at)} unless you cancel it.`)).toBeTruthy();
    expect(onChanged).toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Hand to a teammate" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(posted(`${B}/offers/${DRAFT}/withdraw`)).toEqual({}));
  });

  it("goes back to the form, the address kept, when the draft can no longer be confirmed, saying why", async () => {
    // the draft gone or out of date, or the task no longer one that can be offered (the route's 412)
    for (const [status, detail] of [[409, DRAFT_GONE], [409, CHANGED], [404, "No such offer."], [412, "The task can no longer be offered."]] as const) {
      serve(routes({
        [`POST ${B}/offers`]: () => [200, { offer: offer({ id: DRAFT, state: "draft", task_id: TASK }) }],
        [`POST ${B}/offers/${DRAFT}/confirm`]: () => [status, { detail }],
      }));
      const onChanged = vi.fn();
      render(<TaskHandOff task={task()} onChanged={onChanged} />);
      await draftFor("t@example.org");
      await givePassword();
      expect(await screen.findByText(detail)).toBeTruthy();
      expect(screen.queryByLabelText("Your password")).toBeNull();
      expect((screen.getByLabelText("Your teammate's email address") as HTMLInputElement).value).toBe("t@example.org");
      expect(onChanged).toHaveBeenCalled();
      cleanup();
    }
  });

  it("keeps the confirmation open, to try again, when the confirmation itself is refused", async () => {
    const stale = "That confirmation is not one this account made in the last 5 minutes: confirm again.";
    serve(routes({
      [`POST ${B}/offers`]: () => [200, { offer: offer({ id: DRAFT, state: "draft", task_id: TASK }) }],
      [`POST ${B}/offers/${DRAFT}/confirm`]: () => [403, { detail: stale }],
    }));
    render(<TaskHandOff task={task()} onChanged={() => {}} />);
    await draftFor("t@example.org");
    await givePassword();
    const confirm = screen.getByRole("group", { name: "Handing this task to a teammate" });
    expect(await within(confirm).findByText(stale)).toBeTruthy();
    expect(within(confirm).getByLabelText("Your password")).toBeTruthy();
  });

  it("when the track goes off as the draft is asked for, says why in place of every control", async () => {
    let on = true;
    serve(routes({
      [`GET ${B}/offers`]: () => [200, on ? { made: [], received: [] } : { made: [], received: [], off: OFF }],
      [`POST ${B}/offers`]: () => { on = false; return [409, { detail: OFF, off: "teammate" }]; },
    }));
    const onChanged = vi.fn();
    render(<TaskHandOff task={task()} onChanged={onChanged} />);
    fireEvent.click(await screen.findByRole("button", { name: "Hand to a teammate" }));
    fireEvent.change(screen.getByLabelText("Your teammate's email address"), { target: { value: "t@example.org" } });
    fireEvent.click(screen.getByRole("button", { name: /Offer it/ }));
    await waitFor(() => expect(screen.queryAllByRole("button")).toEqual([]));
    expect(screen.getByRole("status").textContent).toBe(OFF);
    expect(onChanged).toHaveBeenCalled();
  });

  it("says what a held offer with no hold waits for, by the task's state", async () => {
    const held = offer({ id: OFFER, state: "held", task_id: TASK, from_email: null, to_email: "t@example.org", hold: null });
    serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [held], received: [] }] }));
    for (const [state, words] of [
      ["offering", "Your offer to t@example.org goes ahead: its hold has ended."],
      ["running", "Your offer to t@example.org waits for the running attempt to end."],
      ["needs_owner", "Your offer to t@example.org did not begin when the attempt ended: it ends shortly, saying why."],
      ["done", "Your offer to t@example.org did not begin when the attempt ended: it ends shortly, saying why."],
    ] as const) {
      render(<TaskHandOff task={task({ state, offer: { id: OFFER, state: "held" } })} onChanged={() => {}} />);
      expect((await screen.findByRole("status")).textContent).toContain(words);
      expect(screen.getByRole("button", { name: "Withdraw" })).toBeTruthy();
      // nothing of custody while an offer of the task stands
      expect(screen.queryByRole("button", { name: "Confirm for custody" })).toBeNull();
      expect(screen.queryAllByRole("checkbox")).toEqual([]);
      cleanup();
    }
  });

  it("says when a custody hand-off goes to the custodian, with its hold and once it has ended", async () => {
    const at = Math.floor(Date.now() / 1000) + 180;
    const handOff = (hold: OfferView["hold"]) => offer({ id: OFFER, kind: "custody", state: "held", task_id: TASK, from_email: null,
                                                         to_email: null, hold });
    serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [handOff({ id: "h".repeat(32), kind: "task.offer", takes_effect_at: at })], received: [] }] }));
    render(<TaskHandOff task={task({ state: "offering", offer: { id: OFFER, state: "held" } })} onChanged={() => {}} />);
    expect(await screen.findByText(`This task goes to your custodian at ${clock(at)} unless you cancel it.`)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
    cleanup();
    serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [handOff(null)], received: [] }] }));
    render(<TaskHandOff task={task({ state: "offering", offer: { id: OFFER, state: "held" } })} onChanged={() => {}} />);
    expect(await screen.findByText("This task goes to your custodian: its hold has ended.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Withdraw" })).toBeTruthy();
    cleanup();
    // its hold ended while the task is no longer offering: it is not said to go to the custodian
    render(<TaskHandOff task={task({ state: "suspended", offer: { id: OFFER, state: "held" } })} onChanged={() => {}} />);
    expect(await screen.findByText("Its hold has ended.")).toBeTruthy();
    expect(screen.queryByText(/goes to your custodian/)).toBeNull();
    expect(screen.getByRole("button", { name: "Withdraw" })).toBeTruthy();
  });

  it("reads its offers again when the pane shows the task's offer, or the task, in another state, and says what they say", async () => {
    let now: OfferView["state"] = "offered";
    serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [offer({ id: OFFER, state: now, task_id: TASK, from_email: null, to_email: "t@example.org" })], received: [] }] }));
    const { rerender } = render(<TaskHandOff task={task({ state: "offering", offer: { id: OFFER, state: "held" } })} onChanged={() => {}} />);
    expect(await screen.findByText(/^Your offer to t@example\.org waits for an answer until /)).toBeTruthy();
    expect(reads(`${B}/offers`)).toBe(1);
    // the offer's state alone moves on the pane: its hold ended, and it waits for an answer
    rerender(<TaskHandOff task={task({ state: "offering", offer: { id: OFFER, state: "offered" } })} onChanged={() => {}} />);
    await waitFor(() => expect(reads(`${B}/offers`)).toBe(2));
    // then the task's: the teammate declined, and the task is back
    now = "declined";
    rerender(<TaskHandOff task={task({ state: "suspended", offer: { id: OFFER, state: "declined" } })} onChanged={() => {}} />);
    expect(await screen.findByText("Your teammate declined your last offer of this task.")).toBeTruthy();
    expect(reads(`${B}/offers`)).toBe(3);
    expect(screen.queryByText(/waits for an answer/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Withdraw" })).toBeNull();
  });

  it("says the same of an offer to someone's address and to no one's, which differ only in what the owner cannot read", async () => {
    const at = Math.floor(Date.now() / 1000) + 180;
    for (const [state, hold] of [["offering", { id: "h".repeat(32), kind: "task.offer" as const, takes_effect_at: at }], ["offering", null]] as const) {
      const shown: string[] = [];
      // for an address that is no one's, nothing is pushed, so the offer names no commit once its hold has ended
      for (const [to, commit] of [["t@example.org", "a".repeat(40)], ["no.one@example.org", null]] as const) {
        serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [offer({ id: OFFER, state: "held", task_id: TASK, from_email: null, to_email: to, commit, hold })], received: [] }] }));
        render(<TaskHandOff task={task({ state, offer: { id: OFFER, state: "held" } })} onChanged={() => {}} />);
        shown.push((await screen.findByRole("status")).textContent!.replace(to, "<address>"));
        cleanup();
      }
      expect(shown[1]).toBe(shown[0]);
      expect(shown[0]).not.toMatch(/sent|seen|reach/);
    }
  });

  it("says, on a running task's form, that confirming the offer stops its attempt", async () => {
    serve(routes());
    render(<TaskHandOff task={task({ state: "running" })} onChanged={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Hand to a teammate" }));
    expect(screen.getByText("This task is working now. Confirming the offer stops that attempt, and the offer can begin only once it ends.")).toBeTruthy();
  });

  it("offers Withdraw on an offer waiting for an answer, and says until when it waits", async () => {
    const waiting = offer({ id: OFFER, state: "offered", task_id: TASK, from_email: null, to_email: "t@example.org" });
    serve(routes({
      [`GET ${B}/offers`]: () => [200, { made: [waiting], received: [] }],
      [`POST ${B}/offers/${OFFER}/withdraw`]: () => [200, { offer: { ...waiting, state: "withdrawn" } }],
    }));
    const onChanged = vi.fn();
    render(<TaskHandOff task={task({ state: "offering", offer: { id: OFFER, state: "offered" } })} onChanged={onChanged} />);
    expect(await screen.findByText(/^Your offer to t@example\.org waits for an answer until /)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Withdraw" }));
    await waitFor(() => expect(posted(`${B}/offers/${OFFER}/withdraw`)).toEqual({}));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("says how the last offer ended without the teammate taking it, the same words for an address that is no one's", async () => {
    for (const [ended, words] of [
      [offer({ task_id: TASK, state: "declined", from_email: null }), "Your teammate declined your last offer of this task."],
      [offer({ task_id: TASK, state: "expired", from_email: null }), "Your last offer of this task expired before it was answered."],
      [offer({ task_id: TASK, state: "failed", from_email: null, why: "The workspace could not push the task's work." }),
       "Your last offer of this task failed. The workspace could not push the task's work."],
    ] as const) {
      serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [ended], received: [] }] }));
      render(<TaskHandOff task={task()} onChanged={() => {}} />);
      expect(await screen.findByText(words)).toBeTruthy();
      expect(screen.getByRole("button", { name: "Hand to a teammate" })).toBeTruthy();
      cleanup();
    }
  });

  it("takes a handed task back with no confirmation", async () => {
    serve(routes({ [`POST ${B}/tasks/${TASK}/take-back`]: () => [200, { task: task({ state: "returning" }) }] }));
    const onChanged = vi.fn();
    render(<TaskHandOff task={task({ state: "handed_over", offer: { id: OFFER, state: "accepted" } })} onChanged={onChanged} />);
    expect(await screen.findByText("Handed to a teammate.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Hand to a teammate" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Confirm for custody" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Take back" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(posted(`${B}/tasks/${TASK}/take-back`)).toEqual({});
    expect(calls.filter((c) => c.path === "/api/account/confirm")).toEqual([]);
  });

  it("says the teammate's task's state in three words, with its step while it is not done, as the accepted offer gives it", async () => {
    const handed = task({ state: "handed_over", offer: { id: OFFER, state: "accepted" } });
    for (const [their, words] of [[{ their_state: "running" as const, their_step: 2 }, "Their task is running, on step 2."],
                                  [{ their_state: "waiting" as const, their_step: 3 }, "Their task is waiting, on step 3."],
                                  [{ their_state: "done" as const, their_step: null }, "Their task is done."]] as const) {
      serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [offer({ task_id: TASK, state: "accepted", ...their })], received: [] }] }));
      render(<TaskHandOff task={handed} onChanged={() => {}} />);
      expect(await screen.findByText(words)).toBeTruthy();
      expect(screen.getByText("Handed to a teammate.")).toBeTruthy();
      cleanup();
    }
    // nothing where the offer says nothing of it: their task stopped or gone, or an app from before it
    for (const their of [{ their_state: null, their_step: null }, {}]) {
      serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [offer({ task_id: TASK, state: "accepted", ...their })], received: [] }] }));
      render(<TaskHandOff task={handed} onChanged={() => {}} />);
      expect(await screen.findByText("Handed to a teammate.")).toBeTruthy();
      expect(screen.queryByText(/^Their task is/)).toBeNull();
      cleanup();
    }
  });

  it("reads the offers again every 15 seconds while the task is with the teammate and the page is shown, and not otherwise", async () => {
    const { ticks, cleared } = standInIntervals(15000);
    let step = 2;
    serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [offer({ task_id: TASK, state: "accepted", their_state: "running", their_step: step })], received: [] }] }));
    const pane = (state: string) => <TaskHandOff task={task({ state, offer: { id: OFFER, state: "accepted" } })} onChanged={() => {}} />;
    const { rerender } = render(pane("suspended"));
    await waitFor(() => expect(reads(`${B}/offers`)).toBe(1));
    expect(ticks).toHaveLength(0);                                   // not with the teammate: no reading again
    rerender(pane("handed_over"));
    expect(await screen.findByText("Their task is running, on step 2.")).toBeTruthy();
    expect(ticks).toHaveLength(1);
    const before = reads(`${B}/offers`);
    step = 3;
    act(() => ticks[0].run());
    expect(await screen.findByText("Their task is running, on step 3.")).toBeTruthy();
    expect(reads(`${B}/offers`)).toBe(before + 1);
    const shown = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    act(() => ticks[0].run());                                       // the page not shown: nothing read
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    expect(reads(`${B}/offers`)).toBe(before + 1);
    shown.mockReturnValue("visible");
    rerender(pane("returning"));                                     // the task on its way back: it stops
    expect(cleared).toHaveBeenCalledWith(ticks[0].id);
    expect(ticks).toHaveLength(1);
  });

  it("says what the app says of the hand-off while the task is on its way back: that the teammate's workspace does not answer", async () => {
    const words = "The teammate's workspace does not answer: the task comes back once it does.";
    serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [offer({ id: OFFER, task_id: TASK, state: "accepted", from_email: null, why: words })], received: [] }] }));
    render(<TaskHandOff task={task({ state: "returning", offer: { id: OFFER, state: "accepted" } })} onChanged={() => {}} />);
    expect(await screen.findByText("Coming back from your teammate: your workspace checks their work before the task goes on.")).toBeTruthy();
    expect(screen.getByText(words)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Take back" })).toBeNull();
  });

  it("offers no hand-off in a state no offer can be made from, but still its custody", async () => {
    for (const state of ["ready", "needs_owner"]) {
      serve(routes());
      render(<TaskHandOff task={task({ state })} onChanged={() => {}} />);
      expect(await screen.findByRole("button", { name: "Confirm for custody" })).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Hand to a teammate" })).toBeNull();
      cleanup();
    }
  });

  it("offers a task received from a teammate neither a hand-off nor custody, and says why in one line", async () => {
    serve(routes());
    render(<TaskHandOff task={task({ continued_from: { task: "1".repeat(32), offer: OFFER } })} onChanged={() => {}} />);
    expect(await screen.findByText("This task came from a teammate, so it can only go back to them: it cannot be handed on or confirmed for custody.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Hand to a teammate" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Confirm for custody" })).toBeNull();
    expect(screen.queryAllByRole("checkbox")).toEqual([]);
    expect(screen.queryAllByTestId("risk")).toEqual([]);
  });

  it("says a task with no repository address cannot be handed, and offers it no custody", async () => {
    serve(routes());
    render(<TaskHandOff task={task({ repo_address: null })} onChanged={() => {}} />);
    expect(await screen.findByText("A task with no repository address cannot be handed to a teammate.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Hand to a teammate" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Confirm for custody" })).toBeNull();
  });
});

describe("a task's custody, on its owner's pane", () => {
  it("offers custody's controls when a custodian is set, saying what the confirmation covers, and not before", async () => {
    serve(routes({ [`GET ${B}/custody`]: () => [200, { as_owner: null, as_custodian: [], tasks: [] }] }));
    render(<TaskHandOff task={task()} onChanged={() => {}} />);
    expect(await screen.findByRole("button", { name: "Hand to a teammate" })).toBeTruthy();
    await waitFor(() => expect(calls.some((c) => c.path === `${B}/custody`)).toBe(true));
    expect(screen.queryByRole("group", { name: "Confirm for custody" })).toBeNull();
    expect(screen.queryByRole("group", { name: "Custody's crossing" })).toBeNull();
    cleanup();
    serve(routes({ [`GET ${B}/custody`]: () => [200, { as_owner: { ...CUSTODIAN, state: "ended" }, as_custodian: [], tasks: [] }] }));
    render(<TaskHandOff task={task()} onChanged={() => {}} />);
    await waitFor(() => expect(calls.some((c) => c.path === `${B}/custody`)).toBe(true));
    expect(screen.queryByRole("button", { name: "Confirm for custody" })).toBeNull();
    cleanup();
    serve(routes());
    render(<TaskHandOff task={task()} onChanged={() => {}} />);
    expect(await screen.findByRole("button", { name: "Confirm for custody" })).toBeTruthy();
    expect(screen.getByText("Confirming covers this task's title, goal, steps and every decision, your answers among them, its checks, "
      + "external commands and protected paths, and its agent's brief's version, as they stand now. A change to any of them leaves it unconfirmed.")).toBeTruthy();
  });

  it("confirms the task for custody, point 12's text open while it is asked, for this task at the version the pane read", async () => {
    serve(routes({ [`POST ${B}/tasks/${TASK}/custody-confirm`]: () => [200, { task: task({ custody_confirmed: true }) }] }));
    const onChanged = vi.fn();
    render(<TaskHandOff task={task()} onChanged={onChanged} />);
    expect(await screen.findByText("Not confirmed for custody, so no custodian takes it.")).toBeTruthy();
    expect({ ...textOver("Confirm for custody").dataset }).toMatchObject({ point: "12", kind: "control", open: "false", cases: "" });
    fireEvent.click(screen.getByRole("button", { name: "Confirm for custody" }));
    expect(textOver("Confirm for custody").dataset.open).toBe("true");
    expect(await screen.findByLabelText("Your password")).toBeTruthy();
    expect(textOver("Confirm for custody").dataset.open).toBe("true");    // still, while the confirmation is asked
    await givePassword();
    await waitFor(() => expect(posted(`${B}/tasks/${TASK}/custody-confirm`)).toEqual({ state_version: 7, confirmation: "conf-1" }));
    expect(confirmedFor()).toEqual([{ password: "pw", change: `task-custody-confirm:${TASK}` }]);
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(textOver("Confirm for custody").dataset.open).toBe("false");
  });

  it("says why a confirmation for custody was refused, and the pane reads the task again", async () => {
    serve(routes({ [`POST ${B}/tasks/${TASK}/custody-confirm`]: () => [409, { detail: CHANGED }] }));
    const onChanged = vi.fn();
    render(<TaskHandOff task={task()} onChanged={onChanged} />);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm for custody" }));
    await givePassword();
    expect(await screen.findByText(CHANGED)).toBeTruthy();
    expect(onChanged).toHaveBeenCalled();
    expect(screen.getByLabelText("Your password")).toBeTruthy();            // ConfirmIt stays, to try again
  });

  it("says a task is confirmed for custody, and asks for no confirmation again", async () => {
    serve(routes());
    render(<TaskHandOff task={task({ custody_confirmed: true })} onChanged={() => {}} />);
    expect(await screen.findByText("Confirmed for custody, as it stands.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Confirm for custody" })).toBeNull();
  });

  it("ticks custody's crossing with point 12's text and its case over the tick, its confirmation, then its hold", async () => {
    const at = Math.floor(Date.now() / 1000) + 180;
    const hold = { id: "c".repeat(32), kind: "task.crossing", takes_effect_at: at };
    serve(routes({
      [`POST ${B}/tasks/${TASK}/custody-cross`]: () => [200, { task: task(), hold }],
      [`POST /api/ade/holds/${hold.id}/cancel`]: () => [200, { cancelled_at: at - 100 }],
    }));
    const onChanged = vi.fn();
    render(<TaskHandOff task={task()} onChanged={onChanged} />);
    const tick = await screen.findByRole("checkbox", { name: "Let custody take this task between your personal plans and a Team organisation's seats" });
    expect(screen.getByText(/^Only the organisation this task is bound to, if it is bound, and never into or out of an Enterprise/)).toBeTruthy();
    expect({ ...textOver("Custody's crossing").dataset }).toMatchObject({ point: "12", kind: "control", open: "false", cases: "custody_cross" });
    fireEvent.click(tick);
    expect(textOver("Custody's crossing").dataset.open).toBe("true");
    expect(await screen.findByLabelText("Your password")).toBeTruthy();
    expect(textOver("Custody's crossing").dataset.open).toBe("true");     // still, while the confirmation is asked
    await givePassword();
    await waitFor(() => expect(posted(`${B}/tasks/${TASK}/custody-cross`)).toEqual({ state_version: 7, confirmation: "conf-1" }));
    expect(confirmedFor()).toEqual([{ password: "pw", change: `task-custody-cross:${TASK}` }]);
    expect(await screen.findByText(`It takes effect at ${clock(at)} unless you cancel it.`)).toBeTruthy();
    expect(onChanged).toHaveBeenCalled();
    fireEvent.click(within(screen.getByRole("group", { name: "Custody's crossing" })).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === `/api/ade/holds/${hold.id}/cancel`)).toBe(true));
  });

  it("keeps the tick's hold said until the pane shows the tick; at its time, reads again once and says the app applies it", async () => {
    const at = Math.floor(Date.now() / 1000);                                // its time comes about a second from now
    serve(routes({ [`POST ${B}/tasks/${TASK}/custody-cross`]: () => [200, { task: task(), hold: { id: "c".repeat(32), kind: "task.crossing", takes_effect_at: at } }] }));
    const onChanged = vi.fn();
    const { rerender } = render(<TaskHandOff task={task()} onChanged={onChanged} />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Let custody take this task between your personal plans and a Team organisation's seats" }));
    await givePassword();
    expect(await screen.findByText(`It takes effect at ${clock(at)} unless you cancel it.`)).toBeTruthy();
    const told = onChanged.mock.calls.length;
    const offersRead = reads(`${B}/offers`);
    expect(await screen.findByText("Its hold has ended: the tick takes effect once the app applies it.", undefined, { timeout: 3000 })).toBeTruthy();
    await waitFor(() => expect(onChanged.mock.calls.length).toBe(told + 1));
    expect(reads(`${B}/offers`)).toBe(offersRead + 1);                     // the read that settles the user's holds
    expect(within(screen.getByRole("group", { name: "Custody's crossing" })).queryByRole("button", { name: "Cancel" })).toBeNull();
    await new Promise((r) => setTimeout(r, 1500));
    expect(onChanged.mock.calls.length).toBe(told + 1);                     // once
    expect(screen.getByText("Its hold has ended: the tick takes effect once the app applies it.")).toBeTruthy();
    rerender(<TaskHandOff task={task({ custody_cross: true, state_version: 8 })} onChanged={onChanged} />);
    expect(screen.queryByText(/Its hold has ended/)).toBeNull();
    const box = screen.getByRole("checkbox", { name: "Let custody take this task between your personal plans and a Team organisation's seats" }) as HTMLInputElement;
    expect([box.checked, box.disabled]).toEqual([true, true]);
  });

  it("offers no crossing tick on a task whose crossings a press ended, and says why", async () => {
    serve(routes());
    render(<TaskHandOff task={task({ crossings_ended: true })} onChanged={() => {}} />);
    expect(await screen.findByText("A press ended this task's crossings, so custody keeps it in the group it is on.")).toBeTruthy();
    expect(screen.queryAllByRole("checkbox")).toEqual([]);
    expect(screen.queryByRole("group", { name: "Custody's crossing" })).toBeNull();
    expect(screen.getAllByTestId("risk").map((m) => m.dataset.cases)).not.toContain("custody_cross");
    expect(screen.getByRole("button", { name: "Confirm for custody" })).toBeTruthy();
  });
});

describe("a push the secret scan held, on the task's pane", () => {
  const SHAPE = "a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)";
  const F1: SecretFinding = { finding: "1".repeat(32), kind: "line", shape: SHAPE, file: "tests/fixture.py", line: 4, place: 3, column: 9, commit: "c".repeat(40), lifted: false };
  const F2: SecretFinding = { finding: "2".repeat(32), kind: "commit", shape: SHAPE, file: null, line: 8, column: 1, commit: "d".repeat(40), lifted: false };
  const F3: SecretFinding = { finding: "3".repeat(32), kind: "name", shape: SHAPE, file: "notes/\u2026.txt", place: 2, commit: "e".repeat(40), lifted: true };
  const hold = (over: Partial<SecretHold> = {}): SecretHold => ({ task_id: TASK, offer: OFFER, side: "owner", findings: [F1, F2, F3], ...over });
  const holding = (h: unknown, over: Record<string, Route> = {}) =>
    routes({ [`GET ${B}/offers`]: () => [200, { made: [], received: [], secret_holds: [h] }], ...over });
  // the app's task_offers.lift_subject, written here from its words
  const subject = (ids: string[]) =>
    `task-secret-lift:${TASK}:${createHash("sha256").update([...new Set(ids)].sort().join(",")).digest("hex").slice(0, 32)}`;
  const box = (group: HTMLElement, words: string) => within(group).getByRole("checkbox", { name: words }) as HTMLInputElement;

  it("names each finding, never a value, where its shape begins; lifts only those ticked, with a confirmation made for them", async () => {
    serve(holding(hold(), { [`POST ${B}/tasks/${TASK}/secret-lift`]: () => [200, { hold: hold(), task: task() }] }));
    const onChanged = vi.fn();
    render(<TaskHandOff task={task({ state: "needs_owner" })} onChanged={onChanged} />);
    const group = await screen.findByRole("group", { name: "Held by the secret scan" });
    const one = box(group, `${SHAPE}, in tests/fixture.py (path 3 of the commit), on line 4, at character 9 (commit cccccccccccccccccccccccccccccccccccccccc)`);
    const two = box(group, `${SHAPE}, in commit dddddddddddddddddddddddddddddddddddddddd: its message or a header, such as its author, on line 8 of the commit object, at character 1`);
    expect(within(group).getByText(`${SHAPE}, in the name of path 2 of the commit: notes/\u2026.txt (commit eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee) (lifted)`)).toBeTruthy();
    expect(within(group).getAllByRole("checkbox")).toHaveLength(2);           // none for what is lifted already
    expect([one.checked, two.checked]).toEqual([false, false]);               // nothing ticked at first
    expect(within(group).getByText(/Tick only what is not a secret/)).toBeTruthy();
    const nothing = within(group).getByRole("button", { name: "Tick what is not a secret" }) as HTMLButtonElement;
    expect(nothing.disabled).toBe(true);
    // two ticked, then one of them unticked: the one left is what is lifted, with a confirmation made for it alone
    fireEvent.click(one);
    fireEvent.click(two);
    expect(within(group).getByRole("button", { name: "Lift the 2 ticked" })).toBeTruthy();
    fireEvent.click(two);
    fireEvent.click(within(group).getByRole("button", { name: "Lift the one ticked" }));
    // while it is being confirmed, no tick changes: what it lifts is what it was asked for
    await screen.findByLabelText("Your password");
    expect([one.disabled, two.disabled]).toEqual([true, true]);
    await givePassword();
    await waitFor(() => expect(posted(`${B}/tasks/${TASK}/secret-lift`)).toEqual({ findings: [F1.finding], confirmation: "conf-1" }));
    expect(confirmedFor()).toEqual([{ password: "pw", change: subject([F1.finding]) }]);
    expect(subject([F1.finding])).not.toBe(subject([F1.finding, F2.finding]));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("on a task received, says the teammate's own work is not going back, and lifts what they tick on that task", async () => {
    serve(holding(hold({ side: "teammate", findings: [F1, F2] }), {
      [`POST ${B}/tasks/${TASK}/secret-lift`]: () => [200, { hold: hold({ side: "teammate" }), task: task() }] }));
    render(<TaskHandOff task={task({ continued_from: { task: TASK_B, offer: OFFER } })} onChanged={() => {}} />);
    const group = await screen.findByRole("group", { name: "Held by the secret scan" });
    expect(within(group).getByText(/Your work on this task is not going back to its owner/)).toBeTruthy();
    expect(within(group).getByText(/the owner cannot lift it for you/)).toBeTruthy();
    fireEvent.click(within(group).getAllByRole("checkbox")[1]);
    fireEvent.click(within(group).getAllByRole("checkbox")[0]);
    fireEvent.click(within(group).getByRole("button", { name: "Lift the 2 ticked" }));
    await givePassword();
    await waitFor(() => expect(posted(`${B}/tasks/${TASK}/secret-lift`)).toEqual({ findings: [F2.finding, F1.finding], confirmation: "conf-1" }));
    expect(confirmedFor()).toEqual([{ password: "pw", change: subject([F1.finding, F2.finding]) }]);
  });

  it("tells two findings of one shape in one file and commit apart by their lines, and says none from a workspace that gives none", async () => {
    // two such findings must not read the same while the page asks which to lift
    const seventh: SecretFinding = { ...F1, finding: "4".repeat(32), line: 7 };
    const unsaid: SecretFinding[] = [{ ...F1, finding: "5".repeat(32), line: null }, { ...F2, finding: "6".repeat(32), line: undefined },
      { ...F3, finding: "7".repeat(32), place: null, lifted: false }];
    serve(holding(hold({ findings: [F1, seventh, ...unsaid] })));
    render(<TaskHandOff task={task({ state: "needs_owner" })} onChanged={() => {}} />);
    const group = await screen.findByRole("group", { name: "Held by the secret scan" });
    expect(box(group, `${SHAPE}, in tests/fixture.py (path 3 of the commit), on line 4, at character 9 (commit cccccccccccccccccccccccccccccccccccccccc)`)).toBeTruthy();
    expect(box(group, `${SHAPE}, in tests/fixture.py (path 3 of the commit), on line 7, at character 9 (commit cccccccccccccccccccccccccccccccccccccccc)`)).toBeTruthy();
    expect(box(group, `${SHAPE}, in tests/fixture.py (path 3 of the commit), at character 9 (commit cccccccccccccccccccccccccccccccccccccccc)`)).toBeTruthy();
    expect(box(group, `${SHAPE}, in commit dddddddddddddddddddddddddddddddddddddddd: its message or a header, such as its author, at character 1`)).toBeTruthy();
    expect(box(group, `${SHAPE}, in a file's name: notes/\u2026.txt (commit eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee)`)).toBeTruthy();
    expect(new Set(within(group).getAllByRole("checkbox").map((b) => b.getAttribute("aria-label") ?? b.closest("label")?.textContent)).size).toBe(5);
  });

  it("says each finding's path's place, so findings whose paths read the same are told apart", async () => {
    // two paths can read the same once shown (a part hidden, a byte written out, a character the app drops or a path it
    // cuts at 300 characters, NFC beside NFD, two spaces drawn as one), so the place is said for each, not only where a
    // part was hidden or a byte written out
    const hidden = String.fromCharCode(0x2026);
    const nfc = "caf" + String.fromCharCode(0xE9) + ".txt", nfd = "cafe" + String.fromCharCode(0x301) + ".txt";
    const at = (n: string, file: string, place: number | null): SecretFinding => ({ ...F1, finding: n.repeat(32), file, line: 3, column: 8, place });
    const pairs: [string, string][] = [[`${hidden}/config.py`, `${hidden}/config.py`], ["a\\x01.txt", "a\\x01.txt"],
      ["ab.txt", "ab.txt"], [nfc, nfd], ["a  b.txt", "a b.txt"]];
    const findings = pairs.flatMap(([one, two], k) => [at(String(2 * k), one, 2 * k + 1), at(String(2 * k + 1), two, 2 * k + 2)]);
    serve(holding(hold({ findings: [...findings, at("x", "ab.txt", null)] })));
    render(<TaskHandOff task={task({ state: "needs_owner" })} onChanged={() => {}} />);
    const group = await screen.findByRole("group", { name: "Held by the secret scan" });
    for (const [k, [one, two]] of pairs.slice(0, 4).entries()) {
      expect(box(group, `${SHAPE}, in ${one} (path ${2 * k + 1} of the commit), on line 3, at character 8 (commit cccccccccccccccccccccccccccccccccccccccc)`)).toBeTruthy();
      expect(box(group, `${SHAPE}, in ${two} (path ${2 * k + 2} of the commit), on line 3, at character 8 (commit cccccccccccccccccccccccccccccccccccccccc)`)).toBeTruthy();
    }
    // two spaces and one, whose accessible names read alike: each says its own place
    for (const p of [9, 10]) {
      expect(within(group).getByRole("checkbox", { name: new RegExp(`b\\.txt \\(path ${p} of the commit\\), on line 3`) })).toBeTruthy();
    }
    // a finding a workspace gave no place says none
    expect(box(group, `${SHAPE}, in ab.txt, on line 3, at character 8 (commit cccccccccccccccccccccccccccccccccccccccc)`)).toBeTruthy();
    expect(within(group).getAllByRole("checkbox")).toHaveLength(11);
  });

  it("says a finding's commit whole, so two commits whose ids begin alike read apart", async () => {
    // two commits whose ids share their first 12 characters (a minute of
    // one core's work made them), each holding a value at the same line and character of its message, read the same
    // with the commit cut there
    const one = "0205bc31ba55ae9be4eea86eae365a70293c459e", two = "0205bc31ba55aa8d7c1fefb126546d720616a7d8";
    const at = (n: string, commit: string): SecretFinding => ({ ...F2, finding: n.repeat(32), commit, line: 6, column: 3 });
    serve(holding(hold({ findings: [at("8", one), at("9", two)] })));
    render(<TaskHandOff task={task({ state: "needs_owner" })} onChanged={() => {}} />);
    const group = await screen.findByRole("group", { name: "Held by the secret scan" });
    for (const c of [one, two]) {
      expect(box(group, `${SHAPE}, in commit ${c}: its message or a header, such as its author, on line 6 of the commit object, at character 3`)).toBeTruthy();
    }
  });

  it("says what comes next once each finding is lifted, and offers no lift", async () => {
    serve(holding(hold({ findings: [F3] })));
    render(<TaskHandOff task={task()} onChanged={() => {}} />);
    expect(await screen.findByText("Each is lifted: hand the task again to push it.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Lift the|Tick what/ })).toBeNull();
    expect(within(screen.getByRole("group", { name: "Held by the secret scan" })).queryAllByRole("checkbox")).toEqual([]);
  });

  it("shows only this task's hold, and while the track is off, offers no tick and no lift", async () => {
    serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [], received: [], secret_holds: [hold({ task_id: TASK_B })] }] }));
    render(<TaskHandOff task={task()} onChanged={() => {}} />);
    expect(await screen.findByRole("button", { name: "Hand to a teammate" })).toBeTruthy();
    expect(screen.queryByRole("group", { name: "Held by the secret scan" })).toBeNull();
    cleanup();
    serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [], received: [], secret_holds: [hold()], off: OFF }] }));
    render(<TaskHandOff task={task({ state: "needs_owner" })} onChanged={() => {}} />);
    const group = await screen.findByRole("group", { name: "Held by the secret scan" });
    expect(within(group).getByText(`${SHAPE}, in tests/fixture.py (path 3 of the commit), on line 4, at character 9 (commit cccccccccccccccccccccccccccccccccccccccc)`)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Lift the|Tick what/ })).toBeNull();
    expect(within(group).queryAllByRole("checkbox")).toEqual([]);
  });

  it("says a refusal that cannot succeed again where it was asked, and reads the hold again", async () => {
    // refused, and read again: F2 is no longer among what holds the push, so of the two ticked F1 alone stays ticked
    let now = hold();
    serve(routes({
      [`GET ${B}/offers`]: () => [200, { made: [], received: [], secret_holds: [now] }],
      [`POST ${B}/tasks/${TASK}/secret-lift`]: () => {
        now = hold({ findings: [F1, F3] });
        return [409, { detail: "This task is not held for those findings now: look again." }];
      },
    }));
    const onChanged = vi.fn();
    render(<TaskHandOff task={task({ state: "needs_owner" })} onChanged={onChanged} />);
    const group = await screen.findByRole("group", { name: "Held by the secret scan" });
    fireEvent.click(within(group).getAllByRole("checkbox")[0]);
    fireEvent.click(within(group).getAllByRole("checkbox")[1]);
    fireEvent.click(within(group).getByRole("button", { name: "Lift the 2 ticked" }));
    await givePassword();
    expect(await screen.findByText("This task is not held for those findings now: look again.")).toBeTruthy();
    expect(onChanged).toHaveBeenCalled();
    expect(reads(`${B}/offers`)).toBeGreaterThan(1);
    const again = await within(screen.getByRole("group", { name: "Held by the secret scan" })).findByRole("button", { name: "Lift the one ticked" });
    fireEvent.click(again);
    await givePassword();
    await waitFor(() => expect(calls.filter((c) => c.method === "POST" && c.path === `${B}/tasks/${TASK}/secret-lift`).map((c) => c.body?.findings)).toEqual([[F1.finding, F2.finding], [F1.finding]]));
    expect(confirmedFor().map((c) => c?.change)).toEqual([subject([F1.finding, F2.finding]), subject([F1.finding])]);
  });
});

describe("offers waiting for a teammate, in the Agents panel", () => {
  it("with the track off, says why and, nothing waiting, offers nothing", async () => {
    serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [], received: [], off: OFF }] }));
    render(<OffersWaiting agents={AGENTS} onChanged={() => {}} />);
    expect((await screen.findByRole("status")).textContent).toBe(OFF);
    expect(screen.queryAllByRole("button")).toEqual([]);
    expect(screen.queryAllByTestId("risk")).toEqual([]);
  });

  it("when the read fails otherwise, says the error and offers Try again, which reads again", async () => {
    let up = false;
    serve(routes({ [`GET ${B}/offers`]: () => (up ? [200, { made: [], received: [offer()] }] : [502, { detail: "The app did not answer." }]) }));
    render(<OffersWaiting agents={AGENTS} onChanged={() => {}} />);
    expect((await screen.findByRole("alert")).textContent).toContain("The app did not answer.");
    up = true;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("group", { name: "Offer of Fix the parser" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("lists only the offers that wait for an answer", async () => {
    serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [offer({ task_id: TASK, title: "Made by me" })], received: [
      offer({ id: DRAFT, state: "declined", title: "Declined one" }), offer({ title: "Waiting one" })] }] }));
    render(<OffersWaiting agents={AGENTS} onChanged={() => {}} />);
    expect(await screen.findByRole("group", { name: "Offer of Waiting one" })).toBeTruthy();
    expect(screen.queryByRole("group", { name: "Offer of Declined one" })).toBeNull();
    expect(screen.queryByText(/Made by me/)).toBeNull();
  });

  it("shows who offered it, the owner's checks word for word to adopt, unticked, and each file a next run would load", async () => {
    const check = `npm test -- parser && echo "ok $HOME" | tee 'log.txt'`;
    serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [], received: [offer({
      from_email: `owner${HIDDEN}@example.org`,
      steps: [{ n: 1, objective: "Write the failing test", check: null }, { n: 2, objective: "Fix it", check }, { n: 3, objective: "Tidy", check: `make check${HIDDEN}hs.txt` }],
      loaded_changes: [".claude/settings.json", `src/CLAUDE${HIDDEN}.md`, ".mcp.json"] })] }] }));
    render(<OffersWaiting agents={AGENTS} onChanged={() => {}} />);
    const group = await screen.findByRole("group", { name: "Offer of Fix the parser" });
    // a character not shown as itself is written out, so what is read is what is there, and what runs
    expect(within(group).getByText("owner[U+202E]@example.org")).toBeTruthy();
    expect(within(group).getByText("a".repeat(40))).toBeTruthy();          // the whole commit, not a part of it
    expect(within(group).getByLabelText("Step 2's check").textContent).toBe(check);
    expect(within(group).getByLabelText("Step 3's check").textContent).toBe("make check[U+202E]hs.txt");
    const boxes = [2, 3].map((n) => within(group).getByRole("checkbox", { name: `Adopt step ${n}'s check` }) as HTMLInputElement);
    expect(boxes.map((b) => b.checked)).toEqual([false, false]);
    expect(within(group).getByText("A check is your teammate's command. One you adopt runs in your workspace, as your user.")).toBeTruthy();
    const files = within(within(group).getByRole("note", { name: "Files a next run would load" })).getAllByRole("listitem");
    expect(files.map((f) => f.textContent)).toEqual([".claude/settings.json", "src/CLAUDE[U+202E].md", ".mcp.json"]);
    expect(within(group).queryByText("The offered commit changes no file a next run would load.")).toBeNull();
    fireEvent.click(within(group).getByRole("button", { name: "Accept into..." }));
    expect(within(group).getByTestId("risk").dataset.owner).toBe("owner[U+202E]@example.org");
  });

  it("shows what the task's packet holds: the owner's decisions, the paths its runs must not touch and the commands it knows reach outside, then what was done before, as information", async () => {
    serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [], received: [offer({ packet: {
      decisions: [{ n: 1, text: "Use the standard library only." }, { n: 2, text: `Keep the old API${HIDDEN}.` }],
      protected: ["tests/**", `migrations${HIDDEN}/**`], external: [`make deploy${HIDDEN}`], done_steps: [1, 2],
      outward_effects: [{ tool: "Bash", kind: "git_remote", target: `origin main${HIDDEN}`, state: "done" },
                        { tool: "Bash", kind: "git_remote", target: "origin dev", state: "not_done" }] } })] }] }));
    render(<OffersWaiting agents={AGENTS} onChanged={() => {}} />);
    const group = await screen.findByRole("group", { name: "Offer of Fix the parser" });
    const packet = within(group).getByLabelText("What the task's packet holds");
    expect(within(packet).getByText("The owner's decisions, which go with the task:")).toBeTruthy();
    expect(within(packet).getByText("1. Use the standard library only.")).toBeTruthy();
    // every text the packet holds written out where it holds a character the ADE does not show, as the goal is: a
    // decision, a path, a command and an effect's target
    expect(within(packet).getByText("2. Keep the old API[U+202E].")).toBeTruthy();
    expect(within(packet).getByText("tests/**, migrations[U+202E]/**")).toBeTruthy();
    expect(within(packet).getByText("make deploy[U+202E]")).toBeTruthy();
    expect(within(packet).getByText("Done before the hand-off: steps 1, 2.")).toBeTruthy();
    const effects = within(within(packet).getByRole("list", { name: "What reached outside the machine" })).getAllByRole("listitem");
    expect(effects.map((e) => e.textContent)).toEqual(["Bash git_remote origin main[U+202E]: done", "Bash git_remote origin dev: not done"]);
    expect(packet.textContent).not.toContain(HIDDEN);
  });

  it("shows a packet that holds information alone: the steps done before the hand-off, or what reached outside", async () => {
    const empty: OfferPacket = { decisions: [], protected: [], external: [], done_steps: [], outward_effects: [] };
    const effect = { tool: "Bash", kind: "git_remote", target: "origin main", state: "done" };
    const cases: [Partial<OfferPacket>, string][] = [[{ done_steps: [3] }, "Done before the hand-off: step 3."],
      [{ outward_effects: [effect] }, "Bash git_remote origin main: done"]];
    for (const [part, shown] of cases) {
      serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [], received: [offer({ packet: { ...empty, ...part } })] }] }));
      render(<OffersWaiting agents={AGENTS} onChanged={() => {}} />);
      const group = await screen.findByRole("group", { name: "Offer of Fix the parser" });
      const packet = within(group).getByLabelText("What the task's packet holds");
      expect(within(packet).getByText("As information, from the owner's task:")).toBeTruthy();
      expect(packet.textContent).toContain(shown);
      expect(within(packet).queryByText("The owner's decisions, which go with the task:")).toBeNull();
      cleanup();
    }
  });

  it("shows nothing of a packet where the offer gives none, or where it holds nothing", async () => {
    const empty = { decisions: [], protected: [], external: [], done_steps: [], outward_effects: [] };
    for (const over of [{}, { packet: null }, { packet: empty }]) {
      serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [], received: [offer(over)] }] }));
      render(<OffersWaiting agents={AGENTS} onChanged={() => {}} />);
      await screen.findByRole("group", { name: "Offer of Fix the parser" });
      expect(screen.queryByLabelText("What the task's packet holds")).toBeNull();
      cleanup();
    }
  });

  it("says when the offered commit changes no file a next run would load", async () => {
    serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [], received: [offer()] }] }));
    render(<OffersWaiting agents={AGENTS} onChanged={() => {}} />);
    expect(await screen.findByText("The offered commit changes no file a next run would load.")).toBeTruthy();
    expect(screen.queryByRole("note", { name: "Files a next run would load" })).toBeNull();
  });

  it("says nothing of the loaded files while the server says why it has not read them, but why", async () => {
    const why = "The files this offer changes that a next run would load are read once your workspace runs: open the offer again in a moment.";
    serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [], received: [offer({ loaded_changes: [], why })] }] }));
    render(<OffersWaiting agents={AGENTS} onChanged={() => {}} />);
    expect(await screen.findByText(why)).toBeTruthy();
    expect(screen.queryByText("The offered commit changes no file a next run would load.")).toBeNull();
  });

  it("reads again every 5 seconds while the server says why it has not read an offer's files, and stops once it has", async () => {
    const { ticks, cleared } = standInIntervals(5000);
    const why = "The files this offer changes that a next run would load are read once your workspace runs: open the offer again in a moment.";
    let unread = true;
    serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [], received: [offer(unread ? { why } : { loaded_changes: [".mcp.json"] })] }] }));
    render(<OffersWaiting agents={AGENTS} onChanged={() => {}} />);
    expect(await screen.findByText(why)).toBeTruthy();
    expect(ticks).toHaveLength(1);
    expect(reads(`${B}/offers`)).toBe(1);
    act(() => ticks[0].run());
    await waitFor(() => expect(reads(`${B}/offers`)).toBe(2));
    unread = false;
    act(() => ticks[0].run());
    expect(await screen.findByRole("note", { name: "Files a next run would load" })).toBeTruthy();
    expect(reads(`${B}/offers`)).toBe(3);
    // none left unread: the interval is stopped, and no other is started; the note is drawn before the effect that
    // stops the interval has run, so the clearing is waited for
    await waitFor(() => expect(cleared).toHaveBeenCalledWith(ticks[0].id));
    expect(ticks).toHaveLength(1);
  });

  it("stops reading again when it goes, and reads again as the teammate comes back to the page", async () => {
    const { ticks, cleared } = standInIntervals(5000);
    const why = "The files this offer changes that a next run would load are read once your workspace runs: open the offer again in a moment.";
    serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [], received: [offer({ why })] }] }));
    const { unmount } = render(<OffersWaiting agents={AGENTS} onChanged={() => {}} />);
    expect(await screen.findByText(why)).toBeTruthy();
    fireEvent.focus(window);
    await waitFor(() => expect(reads(`${B}/offers`)).toBe(2));
    expect(cleared).not.toHaveBeenCalledWith(ticks[0].id);
    unmount();
    expect(cleared).toHaveBeenCalledWith(ticks[0].id);
  });

  it("opens point 13's text over the acceptance each time, its cases as the acceptance stands, and accepts with the checks adopted", async () => {
    serve(routes({
      [`GET ${B}/offers`]: () => [200, { made: [], received: [offer({ other_group_agents: ["a00000000002"] })] }],
      [`POST ${B}/offers/${OFFER}/accept`]: () => [200, { task_id: "9".repeat(32) }],
    }));
    const onChanged = vi.fn();
    render(<OffersWaiting agents={AGENTS} onChanged={onChanged} />);
    const group = await screen.findByRole("group", { name: "Offer of Fix the parser" });
    const text = () => within(group).getByTestId("risk");
    expect({ ...text().dataset }).toMatchObject({ point: "13", kind: "control", open: "false", owner: "owner@example.org" });
    fireEvent.click(within(group).getByRole("button", { name: "Accept into..." }));
    expect({ ...text().dataset }).toMatchObject({ open: "true", cases: "offer", owner: "owner@example.org" });
    // the agents on the offer's repository, not retired; the server refuses the rest, with why
    expect(within(group).getAllByRole("option").map((o) => o.textContent)).toEqual(["Choose an agent", "Builder", "Seat agent"]);
    fireEvent.click(within(group).getByRole("checkbox", { name: "Adopt step 2's check" }));
    expect(text().dataset.cases).toBe("offer,adopted_check");
    const into = within(group).getByLabelText("The agent to accept it into");
    fireEvent.change(into, { target: { value: "a00000000002" } });           // its account in another group than the task's
    expect(text().dataset.cases).toBe("offer,adopted_check,other_group");
    fireEvent.change(into, { target: { value: "a00000000001" } });
    expect(text().dataset.cases).toBe("offer,adopted_check");
    fireEvent.click(within(group).getByRole("button", { name: "Cancel" }));
    expect(text().dataset.open).toBe("false");
    fireEvent.click(within(group).getByRole("button", { name: "Accept into..." }));
    expect(text().dataset.open).toBe("true");                                // again: every time
    fireEvent.change(within(group).getByLabelText("The agent to accept it into"), { target: { value: "a00000000002" } });
    expect(text().dataset.cases).toBe("offer,adopted_check,other_group");
    fireEvent.click(within(group).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(posted(`${B}/offers/${OFFER}/accept`)).toEqual({ agent_id: "a00000000002", adopt_checks: [2] }));
    expect(calls.filter((c) => c.path.startsWith("/api/account/confirm"))).toEqual([]);     // the contract names no confirmation
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(await screen.findByText("Accepted into Seat agent.")).toBeTruthy();
  });

  it("accepts with no check adopted when none is ticked, and the text then names no adopted check", async () => {
    serve(routes({
      [`GET ${B}/offers`]: () => [200, { made: [], received: [offer()] }],
      [`POST ${B}/offers/${OFFER}/accept`]: () => [200, { task_id: "9".repeat(32) }],
    }));
    render(<OffersWaiting agents={AGENTS} onChanged={() => {}} />);
    const group = await screen.findByRole("group", { name: "Offer of Fix the parser" });
    fireEvent.click(within(group).getByRole("button", { name: "Accept into..." }));
    fireEvent.change(within(group).getByLabelText("The agent to accept it into"), { target: { value: "a00000000001" } });
    expect(within(group).getByTestId("risk").dataset.cases).toBe("offer");
    fireEvent.click(within(group).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(posted(`${B}/offers/${OFFER}/accept`)).toEqual({ agent_id: "a00000000001", adopt_checks: [] }));
  });

  it("says why an acceptance or a decline was refused, and reads again", async () => {
    for (const [route, press, detail] of [
      ["accept", "Accept", "That agent's repository is not this task's."],
      ["decline", "Decline", "This offer no longer waits for you."],
    ] as const) {
      serve(routes({
        [`GET ${B}/offers`]: () => [200, { made: [], received: [offer()] }],
        [`POST ${B}/offers/${OFFER}/${route}`]: () => [route === "accept" ? 412 : 409, { detail }],
      }));
      const onChanged = vi.fn();
      render(<OffersWaiting agents={AGENTS} onChanged={onChanged} />);
      const group = await screen.findByRole("group", { name: "Offer of Fix the parser" });
      if (press === "Accept") {
        fireEvent.click(within(group).getByRole("button", { name: "Accept into..." }));
        fireEvent.change(within(group).getByLabelText("The agent to accept it into"), { target: { value: "a00000000001" } });
      }
      fireEvent.click(within(group).getByRole("button", { name: press }));
      expect((await screen.findByRole("alert")).textContent).toBe(detail);
      await waitFor(() => expect(reads(`${B}/offers`)).toBe(2));
      expect(onChanged).toHaveBeenCalled();
      cleanup();
    }
  });

  it("names the repository it looked for when no agent of the teammate's that can take it has it", async () => {
    serve(routes({ [`GET ${B}/offers`]: () => [200, { made: [], received: [offer()] }] }));
    // a retired agent on the repository takes nothing, so the words say "that can take it"
    render(<OffersWaiting agents={[agent("a00000000003", "Other repo", "elsewhere"), agent("a00000000004", "Retired one", "demo", true)]} onChanged={() => {}} />);
    const group = await screen.findByRole("group", { name: "Offer of Fix the parser" });
    fireEvent.click(within(group).getByRole("button", { name: "Accept into..." }));
    expect(within(group).getByText(/^No agent of yours/).textContent).toBe("No agent of yours that can take it has a repository named demo.");
    expect((within(group).getByRole("button", { name: "Accept" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("declines an offer with the sign-in alone", async () => {
    serve(routes({
      [`GET ${B}/offers`]: () => [200, { made: [], received: [offer()] }],
      [`POST ${B}/offers/${OFFER}/decline`]: () => [200, { offer: offer({ state: "declined" }) }],
    }));
    const onChanged = vi.fn();
    render(<OffersWaiting agents={AGENTS} onChanged={onChanged} />);
    fireEvent.click(await screen.findByRole("button", { name: "Decline" }));
    await waitFor(() => expect(posted(`${B}/offers/${OFFER}/decline`)).toEqual({}));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(calls.filter((c) => c.path.startsWith("/api/account/confirm"))).toEqual([]);
  });
});
