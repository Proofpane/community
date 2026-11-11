import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Agent } from "../agents";
import { useStore } from "../store";
import type { CustodyView, HandOffTask, OfferView } from "../taskOffersApi";
import { clock } from "../when";
import { TaskCustody } from "./TaskCustody";
import { OffersWaiting, TaskHandOff } from "./TaskHandOff";

// The page parts with the teammate part off while hand-offs stand: the reads say why (`off`), and each part shows it
// in place of every control that starts or advances a hand-off, beside what ends or undoes one that stands. And one
// Cancel for each hold, the hand-off part telling the task's pane when it shows the standing offer with its own,
// and letting go of its copy of custody's crossing hold once the task's view carries it.

// RiskTerms is faked by a marker, so a test sees whether any risk point's text is offered at all
vi.mock("./RiskTerms", async () => {
  const { createElement } = await import("react");
  return {
    RiskTerms: (p: { point: number; open?: boolean }) =>
      createElement("div", { "data-testid": "risk", "data-point": String(p.point), "data-open": String(!!p.open) }),
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
const OFFER = "e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0";
const HOLD = "f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0";
const CROSS = "c".repeat(32);
const T = 1_790_000_000;
// the server's own words (task_offers.py): the part off for want of an operator, and a refused Take back
const OFF = "This deployment does not name its operator, so offers and custody are off.";
const NOT_WITH = "This task is not with a teammate.";
const TICK = "Let custody take this task between your personal plans and a Team organisation's seats";

const task = (over: Partial<HandOffTask> = {}): HandOffTask => ({
  id: TASK, agent_id: "a1a2a3a4a5a6", state: "suspended", state_version: 7, title: "Fix the parser",
  repo_address: "https://github.com/acme/demo", continued_from: null, custody_confirmed: false, custody_cross: false,
  crossings_ended: false, offer: null, ...over,
});
const offer = (over: Partial<OfferView> = {}): OfferView => ({
  id: OFFER, kind: "pressed", state: "offered", task_id: TASK, title: "Fix the parser",
  goal: "Make the parser take commas.", steps: [{ n: 1, objective: "Fix it", check: "npm test -- parser" }],
  from_email: "owner@example.org", to_email: "t@example.org", repo_slug: "acme/demo", branch: `pp/task-${TASK}`,
  commit: "a".repeat(40), packet_digest: "b".repeat(64), loaded_changes: [".mcp.json"], hold: null, why: null,
  created_at: T, expires_at: T + 7 * 86400, terms: null, other_group_agents: [], ...over,
});
const custody = (over: Partial<CustodyView> = {}): CustodyView => ({
  id: "c1".repeat(16), state: "asked", owner_email: "owner@example.org", custodian_email: null, confirmed_at: T,
  agents: [], why: null, created_at: T, updated_at: T, terms: null, ...over,
});
// the owner's custody as the pane reads it: a custodian set, so custody's controls would be offered were the part on
const CUSTODIAN = custody({ id: "c0".repeat(16), state: "accepted", custodian_email: "t@example.org" });
const offRoutes = (made: OfferView[], over: Record<string, Route> = {}): Record<string, Route> => ({
  [`GET ${B}/offers`]: () => [200, { made, received: [], off: OFF }],
  [`GET ${B}/custody`]: () => [200, { as_owner: CUSTODIAN, as_custodian: [], tasks: [], off: OFF }],
  ...over,
});
const posted = (path: string) => calls.find((c) => c.method === "POST" && c.path === path)?.body;
const reads = (path: string) => calls.filter((c) => c.method === "GET" && c.path === path).length;
const buttons = () => screen.queryAllByRole("button").map((b) => b.textContent?.trim());
const statuses = () => screen.queryAllByRole("status").map((s) => s.textContent);
/** Nothing that starts or advances a hand-off: no hand-off, custody confirmation or tick, and no risk text offered. */
function nothingStarts() {
  expect(screen.queryByRole("button", { name: "Hand to a teammate" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Confirm for custody" })).toBeNull();
  expect(screen.queryAllByRole("checkbox")).toEqual([]);
  expect(screen.queryAllByTestId("risk")).toEqual([]);
}
const handed = () => task({ state: "handed_over", offer: { id: OFFER, state: "accepted" } });

afterEach(() => {
  cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks();
  useStore.setState(useStore.getInitialState());
});

describe("a task's hand-off on its owner's pane, with the part off", () => {
  it("says why and keeps Take back on a handed task, which takes it back with no confirmation", async () => {
    serve(offRoutes([offer({ state: "accepted" })], {
      [`POST ${B}/tasks/${TASK}/take-back`]: () => [200, { task: task({ state: "returning" }) }],
    }));
    const onChanged = vi.fn();
    render(<TaskHandOff task={handed()} onChanged={onChanged} />);
    expect((await screen.findByRole("status")).textContent).toBe(OFF);
    expect(screen.getByText("Handed to a teammate.")).toBeTruthy();
    expect(buttons()).toEqual(["Take back"]);
    nothingStarts();
    fireEvent.click(screen.getByRole("button", { name: "Take back" }));
    await waitFor(() => expect(posted(`${B}/tasks/${TASK}/take-back`)).toEqual({}));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(calls.filter((c) => c.path.startsWith("/api/account/confirm"))).toEqual([]);
  });

  it("says why and keeps an offer's Cancel in its hold, which withdraws it", async () => {
    const at = Math.floor(Date.now() / 1000) + 180;
    const held = offer({ state: "held", hold: { id: HOLD, kind: "task.offer", takes_effect_at: at } });
    serve(offRoutes([held], {
      [`POST ${B}/offers/${OFFER}/withdraw`]: () => [200, { offer: { ...held, state: "withdrawn" } }],
    }));
    render(<TaskHandOff task={task({ state: "offering", offer: { id: OFFER, state: "held" } })}
      onChanged={() => {}} />);
    expect(await screen.findByText(`Your offer to t@example.org goes ahead at ${clock(at)} unless you cancel it.`))
      .toBeTruthy();
    expect(statuses()).toContain(OFF);
    expect(buttons()).toEqual(["Cancel"]);
    nothingStarts();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(posted(`${B}/offers/${OFFER}/withdraw`)).toEqual({}));
  });

  it("says why and keeps Withdraw on an offer waiting for an answer, and on one the read does not list", async () => {
    for (const made of [[offer()], []]) {
      serve(offRoutes(made, {
        [`POST ${B}/offers/${OFFER}/withdraw`]: () => [200, { offer: offer({ state: "withdrawn" }) }],
      }));
      render(<TaskHandOff task={task({ state: "offering", offer: { id: OFFER, state: "offered" } })}
        onChanged={() => {}} />);
      expect((await screen.findAllByRole("status")).map((s) => s.textContent)).toContain(OFF);
      expect(buttons()).toEqual(["Withdraw"]);
      nothingStarts();
      fireEvent.click(screen.getByRole("button", { name: "Withdraw" }));
      await waitFor(() => expect(posted(`${B}/offers/${OFFER}/withdraw`)).toEqual({}));
      cleanup();
    }
  });

  it("says why and where a task coming back is, with nothing to press", async () => {
    serve(offRoutes([offer({ state: "accepted" })]));
    render(<TaskHandOff task={task({ state: "returning", offer: { id: OFFER, state: "accepted" } })}
      onChanged={() => {}} />);
    expect((await screen.findByRole("status")).textContent).toBe(OFF);
    expect(screen.getByText(/^Coming back from your teammate: your workspace checks their work before the task goes/))
      .toBeTruthy();
    expect(buttons()).toEqual([]);
    nothingStarts();
  });

  it("says a refused Take back in its own words, and never the reason twice", async () => {
    serve(offRoutes([offer({ state: "accepted" })], {
      [`POST ${B}/tasks/${TASK}/take-back`]: () => [412, { detail: NOT_WITH }],
    }));
    render(<TaskHandOff task={handed()} onChanged={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Take back" }));
    expect((await screen.findByRole("alert")).textContent).toBe(NOT_WITH);
    cleanup();
    // a change refused because the part went off: the reason once, as the read says it
    serve(offRoutes([], { [`POST ${B}/tasks/${TASK}/take-back`]: () => [409, { detail: OFF, off: "teammate" }] }));
    render(<TaskHandOff task={handed()} onChanged={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Take back" }));
    await waitFor(() => expect(posted(`${B}/tasks/${TASK}/take-back`)).toEqual({}));
    await waitFor(() => expect(reads(`${B}/offers`)).toBe(2));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getAllByText(OFF)).toHaveLength(1);
  });

  it("says why alone on a task received from a teammate", async () => {
    serve(offRoutes([]));
    render(<TaskHandOff task={task({ continued_from: { task: "1".repeat(32), offer: OFFER } })}
      onChanged={() => {}} />);
    expect((await screen.findByRole("status")).textContent).toBe(OFF);
    expect(screen.queryByText(/This task came from a teammate/)).toBeNull();
    expect(buttons()).toEqual([]);
  });
});

describe("whether the part shows the task's standing offer, as it tells the pane", () => {
  it("tells true while it shows a standing offer with its own Cancel or Withdraw, the part on or off", async () => {
    const at = Math.floor(Date.now() / 1000) + 180;
    const held = offer({ state: "held", hold: { id: HOLD, kind: "task.offer", takes_effect_at: at } });
    for (const [label, made, shown, off] of [
      ["in its hold", [held], true, false], ["in its hold, the part off", [held], true, true],
      ["waiting", [offer()], true, false], ["declined", [offer({ state: "declined" })], false, false],
    ] as const) {
      serve({
        [`GET ${B}/offers`]: () => [200, { made, received: [], ...(off ? { off: OFF } : {}) }],
        [`GET ${B}/custody`]: () => [200, { as_owner: null, as_custodian: [], tasks: [] }],
      });
      const told = vi.fn();
      const state = shown ? "offering" : "suspended";
      render(<TaskHandOff task={task({ state, offer: { id: OFFER, state: made[0].state } })} onChanged={() => {}}
        onOfferShown={told} />);
      await waitFor(() => expect(reads(`${B}/offers`)).toBe(1));
      await waitFor(() => expect(told.mock.calls.at(-1), label).toEqual([shown]), { timeout: 2000 });
      expect(told.mock.calls.some((c) => c[0] === true), label).toBe(shown);
      cleanup();
      expect(told.mock.calls.at(-1), label).toEqual([false]);             // gone with the part
    }
  });

  it("tells false while it shows no offer: when its read fails, and on a handed task", async () => {
    serve({ [`GET ${B}/offers`]: () => [502, { detail: "The app did not answer." }] });
    const told = vi.fn();
    render(<TaskHandOff task={task({ state: "offering", offer: { id: OFFER, state: "held" } })} onChanged={() => {}}
      onOfferShown={told} />);
    expect((await screen.findByRole("alert")).textContent).toContain("The app did not answer.");
    expect(told.mock.calls.some((c) => c[0] === true)).toBe(false);
    cleanup();
    serve(offRoutes([offer({ state: "accepted" })]));
    const again = vi.fn();
    render(<TaskHandOff task={handed()} onChanged={() => {}} onOfferShown={again} />);
    expect(await screen.findByRole("button", { name: "Take back" })).toBeTruthy();
    expect(again.mock.calls.some((c) => c[0] === true)).toBe(false);
  });

  it("tells true on an offering task the read does not list, where it shows Withdraw by the task's offer", async () => {
    serve(offRoutes([]));
    const told = vi.fn();
    render(<TaskHandOff task={task({ state: "offering", offer: { id: OFFER, state: "held" } })} onChanged={() => {}}
      onOfferShown={told} />);
    expect(await screen.findByRole("button", { name: "Withdraw" })).toBeTruthy();
    await waitFor(() => expect(told.mock.calls.at(-1)).toEqual([true]));
  });
});

describe("custody's crossing hold, listed once", () => {
  const box = () => screen.getByRole("checkbox", { name: TICK }) as HTMLInputElement;
  const group = () => screen.getByRole("group", { name: "Custody's crossing" });
  const viewed = (at: number) => [{ id: CROSS, kind: "task.crossing", takes_effect_at: at }];

  async function ticked(at: number) {
    serve({
      [`GET ${B}/offers`]: () => [200, { made: [], received: [] }],
      [`GET ${B}/custody`]: () => [200, { as_owner: CUSTODIAN, as_custodian: [], tasks: [] }],
      "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
      "POST /api/account/confirm": () => [200, { confirmation: "conf-1" }],
      [`POST ${B}/tasks/${TASK}/custody-cross`]: () =>
        [200, { task: task(), hold: { id: CROSS, kind: "task.crossing", takes_effect_at: at } }],
    });
    const onChanged = vi.fn();
    const view = render(<TaskHandOff task={task()} onChanged={onChanged} />);
    fireEvent.click(await screen.findByRole("checkbox"));
    fireEvent.change(await screen.findByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: /^Confirm$/ }));
    expect(await screen.findByText(`It takes effect at ${clock(at)} unless you cancel it.`)).toBeTruthy();
    return { onChanged, rerender: view.rerender };
  }

  it("lists its own copy until the view carries the hold, then leaves it to the pane, the tick shown", async () => {
    const at = Math.floor(Date.now() / 1000) + 180;
    const { onChanged, rerender } = await ticked(at);
    expect(within(group()).getByRole("button", { name: "Cancel" })).toBeTruthy();
    rerender(<TaskHandOff task={task({ holds: viewed(at), state_version: 8 })} onChanged={onChanged} />);
    await waitFor(() => expect(within(group()).queryByRole("button", { name: "Cancel" })).toBeNull());
    expect(screen.queryByText(/It takes effect at/)).toBeNull();
    expect([box().checked, box().disabled]).toEqual([true, true]);
    // the pane's Cancel took it: the view lists it no more, and this part does not list it again
    rerender(<TaskHandOff task={task({ holds: [], state_version: 9 })} onChanged={onChanged} />);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText(/It takes effect at/)).toBeNull();
    expect(within(group()).queryByRole("button", { name: "Cancel" })).toBeNull();
    expect([box().checked, box().disabled]).toEqual([false, false]);
  });

  it("shows the tick on its way while the view lists the hold, with no copy of its own, and no Cancel", async () => {
    serve({
      [`GET ${B}/offers`]: () => [200, { made: [], received: [] }],
      [`GET ${B}/custody`]: () => [200, { as_owner: CUSTODIAN, as_custodian: [], tasks: [] }],
    });
    const at = Math.floor(Date.now() / 1000) + 180;
    render(<TaskHandOff task={task({ holds: viewed(at) })} onChanged={() => {}} />);
    expect(await screen.findByRole("group", { name: "Custody's crossing" })).toBeTruthy();
    expect([box().checked, box().disabled]).toEqual([true, true]);
    expect(within(group()).queryByRole("button")).toBeNull();
  });

  it("still reads again once at the hold's time when the view carries it", async () => {
    const at = Math.floor(Date.now() / 1000) + 2;                            // its time comes in two to three seconds
    const { onChanged, rerender } = await ticked(at);
    const told = onChanged.mock.calls.length;
    const offersRead = reads(`${B}/offers`);
    rerender(<TaskHandOff task={task({ holds: viewed(at), state_version: 8 })} onChanged={onChanged} />);
    await waitFor(() => expect(screen.queryByText(/It takes effect at/)).toBeNull());
    expect(onChanged.mock.calls.length).toBe(told);                         // its time has not come yet
    await waitFor(() => expect(onChanged.mock.calls.length).toBe(told + 1), { timeout: 6000 });
    expect(reads(`${B}/offers`)).toBe(offersRead + 1);
    await new Promise((r) => setTimeout(r, 1500));
    expect(onChanged.mock.calls.length).toBe(told + 1);                     // once
    expect(screen.queryByText(/Its hold has ended/)).toBeNull();              // the pane says the hold, not this part
  });
});

describe("offers waiting for a teammate, with the part off", () => {
  const AGENTS = [{ id: "a00000000001", name: "Builder", repo: "demo", retired: false } as unknown as Agent];

  it("says why and lists each offer that waits with Decline alone, which declines it", async () => {
    serve({
      [`GET ${B}/offers`]: () => [200, { made: [], received: [offer()], off: OFF }],
      [`POST ${B}/offers/${OFFER}/decline`]: () => [200, { offer: offer({ state: "declined" }) }],
    });
    const onChanged = vi.fn();
    render(<OffersWaiting agents={AGENTS} onChanged={onChanged} />);
    const group = await screen.findByRole("group", { name: "Offer of Fix the parser" });
    expect(group.textContent).toContain("owner@example.org offers you a task: Fix the parser");
    expect(statuses()).toContain(OFF);
    expect(buttons()).toEqual(["Decline"]);
    expect(screen.queryAllByRole("checkbox")).toEqual([]);
    expect(screen.queryAllByTestId("risk")).toEqual([]);
    expect(screen.queryByRole("note", { name: "Files a next run would load" })).toBeNull();
    fireEvent.click(within(group).getByRole("button", { name: "Decline" }));
    await waitFor(() => expect(posted(`${B}/offers/${OFFER}/decline`)).toEqual({}));
    expect(await screen.findByText("Declined.")).toBeTruthy();
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("offers the acceptance again once a read says the part is on", async () => {
    let off = true;
    serve({ [`GET ${B}/offers`]: () => [200, { made: [], received: [offer()], ...(off ? { off: OFF } : {}) }] });
    render(<OffersWaiting agents={AGENTS} onChanged={() => {}} />);
    expect(await screen.findByRole("button", { name: "Decline" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Accept into..." })).toBeNull();
    off = false;
    fireEvent.focus(window);
    expect(await screen.findByRole("button", { name: "Accept into..." })).toBeTruthy();
    expect(screen.queryByText(OFF)).toBeNull();
  });
});

describe("the account's custody, with the part off", () => {
  const C1 = "c1".repeat(16), C2 = "c2".repeat(16), C3 = "c3".repeat(16), T1 = "7".repeat(32);
  const BUILDER = [{ agent_id: "a00000000001", name: "Builder", grp: "personal" }];
  const held = task({ id: T1, state: "running", state_version: 4,
                      continued_from: { task: "1".repeat(32), offer: OFFER } });
  const overview = {
    as_owner: custody({ id: C1, state: "accepted", custodian_email: "t@example.org" }),
    as_custodian: [custody({ id: C2, owner_email: "first@example.org" }),
                   custody({ id: C3, state: "accepted", owner_email: "second@example.org", agents: BUILDER })],
    tasks: [held], off: OFF,
  };

  it("says why and keeps End, a request's Decline and Give back, and nothing that asks, accepts or keeps", async () => {
    serve({
      [`GET ${B}/custody`]: () => [200, overview],
      [`POST ${B}/custody/${C1}/end`]: () => [200, { custody: custody({ id: C1, state: "ended" }) }],
      [`POST ${B}/custody/${C2}/decline`]: () => [200, { custody: custody({ id: C2, state: "declined" }) }],
      [`POST ${B}/custody/${C3}/end`]: () => [200, { custody: custody({ id: C3, state: "ended" }) }],
      [`POST ${B}/tasks/${T1}/give-back`]: () => [200, { task: held }],
    });
    render(<TaskCustody />);
    const mine = await screen.findByRole("group", { name: "Your custodian" });
    expect(screen.getByRole("status").textContent).toBe(OFF);
    expect(within(mine).getByText("Your custodian is t@example.org.")).toBeTruthy();
    const first = screen.getByRole("group", { name: "Custody for first@example.org" });
    const second = screen.getByRole("group", { name: "Custody for second@example.org" });
    const row = screen.getByRole("group", { name: "Fix the parser" });
    expect(buttons()).toEqual(["End", "Decline", "End", "Give back"]);
    expect(within(second).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Builder, personal plans"]);
    expect(screen.getByText("Give back sends it back now.")).toBeTruthy();
    expect(screen.queryByText(/Keep finishes/)).toBeNull();
    expect(screen.queryAllByTestId("risk")).toEqual([]);
    for (const [where, name, path, body] of [
      [mine, "End", `${B}/custody/${C1}/end`, {}], [first, "Decline", `${B}/custody/${C2}/decline`, {}],
      [second, "End", `${B}/custody/${C3}/end`, {}],
      [row, "Give back", `${B}/tasks/${T1}/give-back`, { state_version: 4 }],
    ] as const) {
      fireEvent.click(within(where).getByRole("button", { name }));
      await waitFor(() => expect(posted(path)).toEqual(body));
    }
    expect(calls.filter((c) => c.path.startsWith("/api/account/confirm"))).toEqual([]);
  });

  it("offers no End on custody that has ended, so nothing to press where nothing stands", async () => {
    const ended = custody({ id: C1, state: "ended", custodian_email: "t@example.org", agents: BUILDER });
    serve({ [`GET ${B}/custody`]: () => [200, { as_owner: ended, as_custodian: [], tasks: [], off: OFF }] });
    render(<TaskCustody />);
    expect(await screen.findByText("Custody with t@example.org ended.")).toBeTruthy();
    expect(buttons()).toEqual([]);
  });
});
