import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../store";
import type { CustodyOverview, CustodyView, HandOffTask } from "../taskOffersApi";
import { TaskCustody } from "./TaskCustody";

// C's RiskTerms is faked by a marker that shows the props G gives it, so a test sees which risk point's text opens over
// which control, with which cases and owner, and when; the real one renders the text and never the control.
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
const C1 = "c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1";
const C2 = "c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2";
const C3 = "c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3";
const T1 = "7777777777777777aaaaaaaaaaaaaaaa";
const T = 1_790_000_000;
const ORG = "0f0f0f0f-1111-2222-3333-444444444444";
const HIDDEN = String.fromCodePoint(0x202e);
// the server's own words (task_offers.py): the track off under the local provider, custody suspended, a request gone
const OFF = ("Offers and custody need each person's workspace to be a machine of their own, as the fly provider gives it; "
             + "this deployment runs every workspace on one machine, so they are off.");
const SUSPENDED = "A receiving agent changed: the custodian confirms the agents again.";
const DRAFT_GONE = "The draft has expired: make it again.";
const SEEN = "If that address is someone's here, they will see your request.";

const custody = (over: Partial<CustodyView> = {}): CustodyView => ({
  id: C1, state: "asked", owner_email: "owner@example.org", custodian_email: null, confirmed_at: null, agents: [], why: null,
  created_at: T, updated_at: T, terms: null, ...over,
});
const overview = (over: Partial<CustodyOverview> = {}): CustodyOverview => ({ as_owner: null, as_custodian: [], tasks: [], ...over });
const held = (over: Partial<HandOffTask> = {}): HandOffTask => ({
  id: T1, agent_id: "a00000000001", state: "running", state_version: 4, title: "Fix the parser", repo_address: "https://github.com/acme/demo",
  continued_from: { task: "1".repeat(32), offer: "2".repeat(32) }, custody_confirmed: false, custody_cross: false, crossings_ended: false,
  offer: null, ...over,
});
// GET /api/ade/agents as the app answers it: groups, each with its agents (only the fields the part reads matter), each
// agent's account the one it names, null where it names none, which is not the user's default account (the workspace
// runs such an agent on whichever account new sessions start on, and the server takes it as no receiving agent)
const agentsRead = (agents: { id: string; name: string; repo: string | null; retired: boolean; account: string | null }[]) =>
  ({ groups: [{ name: "Agents", agents }], waiting: [], live: 0, polled_at: null });
const AGENTS = agentsRead([
  { id: "a00000000001", name: "Builder", repo: "demo", retired: false, account: "cccccccccccc" },
  { id: "a00000000002", name: "Reviewer", repo: "demo", retired: false, account: "aaaaaaaaaaaa" },
  { id: "a00000000003", name: "Retired one", repo: "demo", retired: true, account: null },
  { id: "a00000000004", name: "Scratch", repo: null, retired: false, account: null },
  { id: "a00000000005", name: "Fresh", repo: "demo", retired: false, account: "bbbbbbbbbbbb" },
  { id: "a00000000006", name: "Unnamed", repo: "demo", retired: false, account: null },
]);
// GET /api/ade/agents/accounts: the user's accounts, the default one, a personal one and a Team seat confirmed,
// one not; the default one confirmed, so that an agent naming no account is seen not to be taken as on it
const ACCOUNTS = { accounts: [
  { id: "1".repeat(32), account: "default", state: "confirmed", grp: "personal", grp_kind: "personal" },
  { id: "2".repeat(32), account: "aaaaaaaaaaaa", state: "confirmed", grp: `org:${ORG}`, grp_kind: "team" },
  { id: "3".repeat(32), account: "bbbbbbbbbbbb", state: "reported", grp: "personal", grp_kind: "personal" },
  { id: "4".repeat(32), account: "cccccccccccc", state: "confirmed", grp: "personal", grp_kind: "personal" },
] };
const NAMES_NONE = "it names no account of its own, so no task goes to it";

const routes = (view: () => CustodyOverview, over: Record<string, Route> = {}): Record<string, Route> => ({
  [`GET ${B}/custody`]: () => [200, view()],
  "GET /api/ade/agents": () => [200, AGENTS],
  "GET /api/ade/agents/accounts": () => [200, ACCOUNTS],
  "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
  "POST /api/account/confirm": () => [200, { confirmation: "conf-1" }],
  ...over,
});
/** What the page asked a confirmation for, as the server's /api/account/confirm received it. */
const confirmedFor = () => calls.filter((c) => c.path === "/api/account/confirm" && c.method === "POST").map((c) => c.body);
const posted = (path: string) => calls.find((c) => c.method === "POST" && c.path === path)?.body;
const reads = (path: string) => calls.filter((c) => c.method === "GET" && c.path === path).length;
const marker = (group: HTMLElement) => ({ ...within(group).getByTestId("risk").dataset });
const labels = (group: HTMLElement) => within(group).queryAllByRole("checkbox").map((b) => b.closest("label")?.textContent);

async function givePassword() {
  fireEvent.change(await screen.findByLabelText("Your password"), { target: { value: "pw" } });
  fireEvent.click(screen.getByRole("button", { name: /^Confirm$/ }));
}

async function askFor(address: string) {
  const mine = await screen.findByRole("group", { name: "Your custodian" });
  fireEvent.click(within(mine).getByRole("button", { name: "Ask a teammate to be your custodian" }));
  fireEvent.change(within(mine).getByLabelText("Your teammate's email address"), { target: { value: address } });
  fireEvent.click(within(mine).getByRole("button", { name: /^Ask$/ }));
  await within(mine).findByLabelText("Your password");
  return mine;
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); useStore.setState(useStore.getInitialState()); });

describe("the account's custody", () => {
  it("with the track off and nothing standing, says why in the read's words and offers nothing", async () => {
    serve(routes(() => ({ ...overview(), off: OFF })));
    render(<TaskCustody />);
    expect((await screen.findByRole("status")).textContent).toBe(OFF);
    expect(screen.queryAllByRole("button")).toEqual([]);
    expect(screen.queryAllByRole("checkbox")).toEqual([]);
    expect(screen.queryAllByTestId("risk")).toEqual([]);
  });

  it("says that a custodian takes a task only where the relay is on, custody's hand-off being its second step", async () => {
    serve(routes(() => overview()));
    render(<TaskCustody />);
    await screen.findByRole("group", { name: "Your custodian" });
    expect(screen.getByText("A custodian is a teammate whose agents may take over your tasks confirmed for custody while your own accounts are out of room, as the relay's second step: only where this deployment has the relay on, and the task's own Relay switch is on.")).toBeTruthy();
  });

  it("when its first read fails otherwise, says the error and offers Try again, which reads again", async () => {
    let up = false;
    serve(routes(() => overview(), { [`GET ${B}/custody`]: () => (up ? [200, overview()] : [502, { detail: "The app did not answer." }]) }));
    render(<TaskCustody />);
    expect((await screen.findByRole("alert")).textContent).toContain("The app did not answer.");
    expect(screen.queryByRole("group", { name: "Your custodian" })).toBeNull();
    up = true;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("group", { name: "Your custodian" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(reads(`${B}/custody`)).toBe(2);
  });

  it("when a later read fails otherwise, keeps what it showed, says the error, and offers Try again", async () => {
    let up = true;
    const own = custody({ state: "accepted", custodian_email: "t@example.org", confirmed_at: T,
                          agents: [{ agent_id: "a00000000001", name: "Builder", grp: "personal" }] });
    serve(routes(() => overview(), {
      [`GET ${B}/custody`]: () => (up ? [200, overview({ as_owner: own })] : [502, { detail: "The app did not answer." }]),
      [`POST ${B}/custody/${C1}/end`]: () => { up = false; return [200, { custody: { ...own, state: "ended" } }]; },
    }));
    render(<TaskCustody />);
    const mine = await screen.findByRole("group", { name: "Your custodian" });
    fireEvent.click(within(mine).getByRole("button", { name: "End" }));
    expect((await screen.findByText("The app did not answer.")).closest("[role=alert]")).toBeTruthy();
    expect(within(mine).getByText("Your custodian is t@example.org.")).toBeTruthy();
    up = true;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.queryByText("The app did not answer.")).toBeNull());
  });

  it("asks a teammate to be the custodian: point 12's text over the form while it is open, then the request's own confirmation", async () => {
    let view = overview();
    serve(routes(() => view, {
      [`POST ${B}/custody`]: () => [200, { custody: custody({ id: C1, owner_email: null }) }],
      [`POST ${B}/custody/${C1}/confirm`]: () => {
        view = overview({ as_owner: custody({ id: C1, owner_email: null, custodian_email: "t@example.org", confirmed_at: T }) });
        return [200, { custody: view.as_owner }];
      },
    }));
    render(<TaskCustody />);
    const mine = await screen.findByRole("group", { name: "Your custodian" });
    expect(within(mine).getByText("You have no custodian.")).toBeTruthy();
    expect(marker(mine)).toMatchObject({ point: "12", kind: "control", open: "false", cases: "" });
    fireEvent.click(within(mine).getByRole("button", { name: "Ask a teammate to be your custodian" }));
    expect(marker(mine).open).toBe("true");
    fireEvent.change(within(mine).getByLabelText("Your teammate's email address"), { target: { value: "t@example.org" } });
    fireEvent.click(within(mine).getByRole("button", { name: /^Ask$/ }));
    await waitFor(() => expect(posted(`${B}/custody`)).toEqual({ address: "t@example.org" }));
    expect(await within(mine).findByLabelText("Your password")).toBeTruthy();
    expect(within(mine).getByRole("status").textContent).toBe(SEEN);
    expect(marker(mine).open).toBe("true");                                  // the text stays open while it is asked
    expect(posted(`${B}/custody/${C1}/confirm`)).toBeUndefined();
    await givePassword();
    await waitFor(() => expect(posted(`${B}/custody/${C1}/confirm`)).toEqual({ confirmation: "conf-1" }));
    expect(confirmedFor()).toEqual([{ password: "pw", change: `task-custody:${C1}` }]);
    expect(await within(mine).findByText("You asked t@example.org to be your custodian. It waits for an answer.")).toBeTruthy();
    expect(marker(mine).open).toBe("false");
  });

  it("goes back to the address form, the address kept, when the request can no longer be confirmed, saying why", async () => {
    for (const [status, detail] of [[409, DRAFT_GONE], [404, "No such custody request."]] as const) {
      serve(routes(() => overview(), {
        [`POST ${B}/custody`]: () => [200, { custody: custody({ id: C1, owner_email: null }) }],
        [`POST ${B}/custody/${C1}/confirm`]: () => [status, { detail }],
      }));
      render(<TaskCustody />);
      const mine = await askFor("t@example.org");
      await givePassword();
      expect(await screen.findByText(detail)).toBeTruthy();
      expect(within(mine).queryByLabelText("Your password")).toBeNull();
      expect((within(mine).getByLabelText("Your teammate's email address") as HTMLInputElement).value).toBe("t@example.org");
      expect(marker(mine).open).toBe("true");                                // the form is open again, and its text with it
      cleanup();
    }
  });

  it("keeps the request's confirmation open, to try again, when the confirmation itself is refused", async () => {
    const stale = "That confirmation is not one this account made in the last 5 minutes: confirm again.";
    serve(routes(() => overview(), {
      [`POST ${B}/custody`]: () => [200, { custody: custody({ id: C1, owner_email: null }) }],
      [`POST ${B}/custody/${C1}/confirm`]: () => [403, { detail: stale }],
    }));
    render(<TaskCustody />);
    await askFor("t@example.org");
    await givePassword();
    const confirm = screen.getByRole("group", { name: "Asking a teammate to be your custodian" });
    expect(await within(confirm).findByText(stale)).toBeTruthy();
    expect(within(confirm).getByLabelText("Your password")).toBeTruthy();
  });

  it("opens point 12's text again each time the request's form opens", async () => {
    serve(routes(() => overview()));
    render(<TaskCustody />);
    const mine = await screen.findByRole("group", { name: "Your custodian" });
    fireEvent.click(within(mine).getByRole("button", { name: "Ask a teammate to be your custodian" }));
    expect(marker(mine).open).toBe("true");
    fireEvent.click(within(mine).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(marker(mine).open).toBe("false"));
    fireEvent.click(await within(mine).findByRole("button", { name: "Ask a teammate to be your custodian" }));
    expect(marker(mine).open).toBe("true");
  });

  it("says how the owner's custody stands, naming nobody where a request ended unanswered", async () => {
    for (const [own, words, button] of [
      [custody({ state: "accepted", custodian_email: "t@example.org", confirmed_at: T, agents: [{ agent_id: "a00000000001", name: "Builder", grp: "personal" }] }),
       "Your custodian is t@example.org.", "End"],
      [custody({ state: "declined", confirmed_at: T }), "Your request to your teammate was declined.", "Ask a teammate to be your custodian"],
      [custody({ state: "expired", confirmed_at: T }), "Your request to your teammate expired before it was answered.", "Ask a teammate to be your custodian"],
      // with no agents: a request ended unanswered, or an accepted custody the server reduced 90 days after it ended
      [custody({ state: "ended", confirmed_at: T }), "Your request to your teammate, or the custody it became, ended.", "Ask a teammate to be your custodian"],
      [custody({ state: "ended", custodian_email: "t@example.org", confirmed_at: T, agents: [{ agent_id: "a00000000001", name: "Builder", grp: "personal" }] }),
       "Custody with t@example.org ended.", "Ask a teammate to be your custodian"],
    ] as const) {
      serve(routes(() => overview({ as_owner: own })));
      render(<TaskCustody />);
      const mine = await screen.findByRole("group", { name: "Your custodian" });
      expect(within(mine).getByText(words)).toBeTruthy();
      expect(within(mine).getByRole("button", { name: button })).toBeTruthy();
      cleanup();
    }
  });

  it("ends a request still asked with the sign-in alone", async () => {
    let view = overview({ as_owner: custody({ state: "asked", owner_email: null, custodian_email: "t@example.org", confirmed_at: T }) });
    serve(routes(() => view, { [`POST ${B}/custody/${C1}/end`]: () => {
      view = overview({ as_owner: custody({ state: "ended", owner_email: null, confirmed_at: T }) });
      return [200, { custody: view.as_owner }];
    } }));
    render(<TaskCustody />);
    const mine = await screen.findByRole("group", { name: "Your custodian" });
    expect(within(mine).getByText("You asked t@example.org to be your custodian. It waits for an answer.")).toBeTruthy();
    fireEvent.click(within(mine).getByRole("button", { name: "End" }));
    await waitFor(() => expect(posted(`${B}/custody/${C1}/end`)).toEqual({}));
    expect(await within(mine).findByText("Your request to your teammate, or the custody it became, ended.")).toBeTruthy();
    expect(calls.filter((c) => c.path === "/api/account/confirm")).toEqual([]);
  });

  it("says why the owner's custody is suspended, and ends it with the sign-in alone", async () => {
    const agents = [{ agent_id: "a00000000001", name: "Builder", grp: "personal" }];
    let view = overview({ as_owner: custody({ state: "suspended", owner_email: null, custodian_email: "t@example.org", confirmed_at: T, agents, why: SUSPENDED }) });
    serve(routes(() => view, { [`POST ${B}/custody/${C1}/end`]: () => {
      view = overview({ as_owner: custody({ state: "ended", owner_email: null, custodian_email: "t@example.org", confirmed_at: T, agents }) });
      return [200, { custody: view.as_owner }];
    } }));
    render(<TaskCustody />);
    const mine = await screen.findByRole("group", { name: "Your custodian" });
    expect(within(mine).getByText(`Custody with t@example.org is suspended. ${SUSPENDED}`)).toBeTruthy();
    fireEvent.click(within(mine).getByRole("button", { name: "End" }));
    await waitFor(() => expect(posted(`${B}/custody/${C1}/end`)).toEqual({}));
    expect(await within(mine).findByText("Custody with t@example.org ended.")).toBeTruthy();
    expect(within(mine).getByRole("button", { name: "Ask a teammate to be your custodian" })).toBeTruthy();
    expect(calls.filter((c) => c.path === "/api/account/confirm")).toEqual([]);
  });
});

describe("custody asked of this user", () => {
  it("opens point 13's text over the acceptance with custody's cases and the owner, and accepts with the agents in order", async () => {
    serve(routes(() => overview({ as_custodian: [custody({ id: C2 })] }), {
      [`POST ${B}/custody/${C2}/accept`]: () => [200, { custody: custody({ id: C2, state: "accepted" }) }],
    }));
    render(<TaskCustody />);
    const request = await screen.findByRole("group", { name: "Custody for owner@example.org" });
    expect(within(request).getByText("owner@example.org asks you to be their custodian.")).toBeTruthy();
    expect(marker(request)).toMatchObject({ point: "13", kind: "control", open: "false", owner: "owner@example.org" });
    fireEvent.click(within(request).getByRole("button", { name: "Accept..." }));
    // always both: whether a task handed while custody stands goes into another group is not known when it is accepted
    expect(marker(request)).toMatchObject({ open: "true", cases: "custody,other_group", owner: "owner@example.org" });
    // the user's own agents with a repository, not retired, each with its account's group as the accounts read says it;
    // an agent that names no account is not taken as on the default one, which the accounts read has confirmed here
    await waitFor(() => expect(labels(request)).toEqual(["Builder, personal plans", `Reviewer, an organisation's seats (${ORG})`,
                                                         "Fresh, its account is not confirmed as yours", `Unnamed, ${NAMES_NONE}`]));
    const accept = within(request).getByRole("button", { name: "Accept" }) as HTMLButtonElement;
    expect(accept.disabled).toBe(true);                                       // no agent chosen yet
    fireEvent.click(within(request).getByRole("checkbox", { name: /^Reviewer/ }));
    fireEvent.click(within(request).getByRole("checkbox", { name: /^Builder/ }));
    expect(within(request).getByText("In order: Reviewer, Builder.")).toBeTruthy();
    fireEvent.click(accept);
    expect(await within(request).findByLabelText("Your password")).toBeTruthy();
    expect(within(request).getByRole("group", { name: "Accepting custody" })).toBeTruthy();
    expect(marker(request).open).toBe("true");                               // still, while the confirmation is asked
    await givePassword();
    await waitFor(() => expect(posted(`${B}/custody/${C2}/accept`)).toEqual({ agents: ["a00000000002", "a00000000001"], confirmation: "conf-1" }));
    expect(confirmedFor()).toEqual([{ password: "pw", change: `task-custody-accept:${C2}` }]);
  });

  it("shows no group, and no error, when the accounts cannot be read, but still an agent that names no account", async () => {
    serve(routes(() => overview({ as_custodian: [custody({ id: C2 })] }), { "GET /api/ade/agents/accounts": () => [500, { detail: "Boom." }] }));
    render(<TaskCustody />);
    const request = await screen.findByRole("group", { name: "Custody for owner@example.org" });
    fireEvent.click(within(request).getByRole("button", { name: "Accept..." }));
    // what an agent names is the agents read's: said whatever the accounts read
    await waitFor(() => expect(labels(request)).toEqual(["Builder", "Reviewer", "Fresh", `Unnamed, ${NAMES_NONE}`]));
    expect(reads("/api/ade/agents/accounts")).toBe(1);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText("Boom.")).toBeNull();
  });

  it("writes out a character not shown as itself in a receiving agent's name", async () => {
    serve(routes(() => overview({ as_custodian: [custody({ id: C3, state: "accepted", confirmed_at: T,
                                                           agents: [{ agent_id: "a00000000002", name: `Rev${HIDDEN}iewer`, grp: "personal" }] })] }), {
      "GET /api/ade/agents": () => [200, agentsRead([{ id: "a00000000002", name: `Rev${HIDDEN}iewer`, repo: "demo", retired: false, account: "cccccccccccc" }])],
    }));
    render(<TaskCustody />);
    const request = await screen.findByRole("group", { name: "Custody for owner@example.org" });
    expect(within(request).getByRole("listitem").textContent).toBe("Rev[U+202E]iewer, personal plans");
    fireEvent.click(within(request).getByRole("button", { name: "Change the agents" }));
    await waitFor(() => expect(labels(request)).toEqual(["Rev[U+202E]iewer, personal plans"]));
    expect(within(request).getByText("In order: Rev[U+202E]iewer.")).toBeTruthy();
  });

  it("opens point 13's text again each time the acceptance opens", async () => {
    serve(routes(() => overview({ as_custodian: [custody({ id: C2 })] })));
    render(<TaskCustody />);
    const request = await screen.findByRole("group", { name: "Custody for owner@example.org" });
    fireEvent.click(within(request).getByRole("button", { name: "Accept..." }));
    expect(marker(request).open).toBe("true");
    fireEvent.click(within(request).getByRole("button", { name: "Cancel" }));
    expect(marker(request).open).toBe("false");
    fireEvent.click(within(request).getByRole("button", { name: "Accept..." }));
    expect(marker(request).open).toBe("true");
  });

  it("when the track goes off at an acceptance, says why in its place and keeps Decline", async () => {
    let on = true;
    serve(routes(() => overview({ as_custodian: [custody({ id: C2 })] }), {
      [`GET ${B}/custody`]: () => [200, { ...overview({ as_custodian: [custody({ id: C2 })] }),
                                          ...(on ? {} : { off: OFF }) }],
      [`POST ${B}/custody/${C2}/accept`]: () => { on = false; return [409, { detail: OFF, off: "teammate" }]; },
    }));
    render(<TaskCustody />);
    const request = await screen.findByRole("group", { name: "Custody for owner@example.org" });
    fireEvent.click(within(request).getByRole("button", { name: "Accept..." }));
    fireEvent.click(await within(request).findByRole("checkbox", { name: /^Builder/ }));
    fireEvent.click(within(request).getByRole("button", { name: "Accept" }));
    await givePassword();
    // a decline still answers while the track is off, so it stays; nothing that would accept does
    await waitFor(() => expect(screen.queryAllByRole("button").map((b) => b.textContent)).toEqual(["Decline"]));
    expect(screen.getByRole("status").textContent).toBe(OFF);
    expect(screen.queryAllByRole("checkbox")).toEqual([]);
    expect(screen.queryAllByTestId("risk")).toEqual([]);
    expect(screen.queryByLabelText("Your password")).toBeNull();
  });

  it("declines a request still asked, and ends an accepted custody, each with the sign-in alone", async () => {
    serve(routes(() => overview({ as_custodian: [
      custody({ id: C2, owner_email: "first@example.org" }),
      custody({ id: C3, state: "accepted", owner_email: "second@example.org", confirmed_at: T,
                agents: [{ agent_id: "a00000000001", name: "Builder", grp: "personal" }] }),
    ] }), {
      [`POST ${B}/custody/${C2}/decline`]: () => [200, { custody: custody({ id: C2, state: "declined", owner_email: "first@example.org" }) }],
      [`POST ${B}/custody/${C3}/end`]: () => [200, { custody: custody({ id: C3, state: "ended", owner_email: "second@example.org" }) }],
    }));
    render(<TaskCustody />);
    const first = await screen.findByRole("group", { name: "Custody for first@example.org" });
    const second = screen.getByRole("group", { name: "Custody for second@example.org" });
    expect(within(first).queryByRole("button", { name: "End" })).toBeNull();
    expect(within(second).queryByRole("button", { name: "Decline" })).toBeNull();
    fireEvent.click(within(first).getByRole("button", { name: "Decline" }));
    await waitFor(() => expect(posted(`${B}/custody/${C2}/decline`)).toEqual({}));
    expect(await screen.findByText("Declined.")).toBeTruthy();
    fireEvent.click(within(second).getByRole("button", { name: "End" }));
    await waitFor(() => expect(posted(`${B}/custody/${C3}/end`)).toEqual({}));
    expect(await screen.findByText("Ended.")).toBeTruthy();
    expect(calls.filter((c) => c.path === "/api/account/confirm")).toEqual([]);
  });

  it("changes the receiving agents of a custody accepted, confirming them as your receiving agents", async () => {
    serve(routes(() => overview({ as_custodian: [custody({ id: C3, state: "accepted", confirmed_at: T,
                                                           agents: [{ agent_id: "a00000000001", name: "Builder", grp: "personal" }] })] }), {
      [`POST ${B}/custody/${C3}/accept`]: () => [200, { custody: custody({ id: C3, state: "accepted" }) }],
    }));
    render(<TaskCustody />);
    const request = await screen.findByRole("group", { name: "Custody for owner@example.org" });
    expect(within(request).getByText(/^You are the custodian of owner@example\.org's tasks\. These agents of yours may take them/)).toBeTruthy();
    fireEvent.click(within(request).getByRole("button", { name: "Change the agents" }));
    expect(marker(request)).toMatchObject({ open: "true", cases: "custody,other_group" });
    expect(await within(request).findByText("In order: Builder.")).toBeTruthy();
    fireEvent.click(await within(request).findByRole("checkbox", { name: /^Reviewer/ }));
    expect(within(request).getByText("In order: Builder, Reviewer.")).toBeTruthy();
    fireEvent.click(within(request).getByRole("button", { name: "Confirm these agents" }));
    expect(await within(request).findByRole("group", { name: "Confirming your receiving agents" })).toBeTruthy();
    await givePassword();
    await waitFor(() => expect(posted(`${B}/custody/${C3}/accept`)).toEqual({ agents: ["a00000000001", "a00000000002"], confirmation: "conf-1" }));
    expect(confirmedFor()).toEqual([{ password: "pw", change: `task-custody-accept:${C3}` }]);
  });

  it("says why a custody is suspended, and confirms its agents again in their order, each with its group", async () => {
    const agents = [{ agent_id: "a00000000002", name: "Reviewer", grp: `org:${ORG}` }, { agent_id: "a00000000001", name: "Builder", grp: "personal" }];
    serve(routes(() => overview({ as_custodian: [custody({ id: C3, state: "suspended", confirmed_at: T, agents, why: SUSPENDED })] }), {
      [`POST ${B}/custody/${C3}/accept`]: () => [200, { custody: custody({ id: C3, state: "accepted", agents }) }],
    }));
    render(<TaskCustody />);
    const request = await screen.findByRole("group", { name: "Custody for owner@example.org" });
    expect(within(request).getByText(`Your custody of owner@example.org's tasks is suspended. ${SUSPENDED}`)).toBeTruthy();
    expect(within(request).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      `Reviewer, an organisation's seats (${ORG})`, "Builder, personal plans"]);
    fireEvent.click(within(request).getByRole("button", { name: "Confirm the agents again" }));
    expect(marker(request)).toMatchObject({ open: "true", cases: "custody,other_group" });
    expect(await within(request).findByText("In order: Reviewer, Builder.")).toBeTruthy();
    await waitFor(() => expect((within(request).getByRole("checkbox", { name: "Builder, personal plans" }) as HTMLInputElement).checked).toBe(true));
    fireEvent.click(within(request).getByRole("button", { name: "Confirm these agents" }));
    expect(await within(request).findByRole("group", { name: "Confirming your receiving agents" })).toBeTruthy();
    await givePassword();
    await waitFor(() => expect(posted(`${B}/custody/${C3}/accept`)).toEqual({ agents: ["a00000000002", "a00000000001"], confirmation: "conf-1" }));
    expect(confirmedFor()).toEqual([{ password: "pw", change: `task-custody-accept:${C3}` }]);
  });

  it("lists a receiving agent since retired, saying so, so it can be taken out before the agents are confirmed again", async () => {
    const agents = [{ agent_id: "a00000000002", name: "Reviewer", grp: "personal" }, { agent_id: "a00000000003", name: "Retired one", grp: "personal" }];
    serve(routes(() => overview({ as_custodian: [custody({ id: C3, state: "suspended", confirmed_at: T, agents, why: SUSPENDED })] }), {
      [`POST ${B}/custody/${C3}/accept`]: () => [200, { custody: custody({ id: C3, state: "accepted", agents: agents.slice(0, 1) }) }],
    }));
    render(<TaskCustody />);
    const request = await screen.findByRole("group", { name: "Custody for owner@example.org" });
    fireEvent.click(within(request).getByRole("button", { name: "Confirm the agents again" }));
    const retired = await within(request).findByRole("checkbox", { name: /^Retired one, retired/ });
    expect((retired as HTMLInputElement).checked).toBe(true);
    expect(within(request).getByText("In order: Reviewer, Retired one.")).toBeTruthy();
    fireEvent.click(retired);
    expect(within(request).getByText("In order: Reviewer.")).toBeTruthy();
    expect(within(request).queryByRole("checkbox", { name: /^Retired one/ })).toBeNull();
    fireEvent.click(within(request).getByRole("button", { name: "Confirm these agents" }));
    await givePassword();
    await waitFor(() => expect(posted(`${B}/custody/${C3}/accept`)).toEqual({ agents: ["a00000000002"], confirmation: "conf-1" }));
  });

  it("keeps a custody task, or gives it back, at the version the section read", async () => {
    serve(routes(() => overview({ tasks: [held()] }), {
      [`POST ${B}/tasks/${T1}/keep`]: () => [200, { task: held({ state_version: 5 }) }],
      [`POST ${B}/tasks/${T1}/give-back`]: () => [200, { task: held({ state: "done", state_version: 5 }) }],
    }));
    render(<TaskCustody />);
    const row = await screen.findByRole("group", { name: "Fix the parser" });
    fireEvent.click(within(row).getByRole("button", { name: "Keep" }));
    await waitFor(() => expect(posted(`${B}/tasks/${T1}/keep`)).toEqual({ state_version: 4 }));
    expect(await screen.findByText("Kept: it goes back when it ends.")).toBeTruthy();
    fireEvent.click(within(row).getByRole("button", { name: "Give back" }));
    await waitFor(() => expect(posted(`${B}/tasks/${T1}/give-back`)).toEqual({ state_version: 4 }));
    expect(calls.filter((c) => c.path === "/api/account/confirm")).toEqual([]);
  });
});
