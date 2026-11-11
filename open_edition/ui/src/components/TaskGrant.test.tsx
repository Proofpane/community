import { Component, type ReactNode } from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { GrantWideningView, TaskGrantView, TaskView } from "../api";
import { useStore } from "../store";
import { dayTime } from "../when";

// A task's grant on the page (the app's ade/task_grants.py): shown on its task and in the owner's list, signed with
// one of the owner's passkeys over a payload the page composes around a nonce, an ask for more signed as it was asked or
// refused with the sign-in alone, and none signed where passkeys do not work (the phone and desktop apps).

type Call = { path: string; method: string; body: Record<string, unknown> | undefined };
type Route = (body: Record<string, unknown> | undefined) => [number, unknown];
const SERVER_TIME = 1_790_000_000;
const TASK = "0123456789abcdef0123456789abcdef";
const AGENT = "a1a2a3a4a5a6";
const LINK = "fedcba9876543210fedcba9876543210";
const ASK = "sha256:" + "ab".repeat(32);
const V1 = "1f".repeat(32);
const V2 = "2e".repeat(32);
const BRANCH = `pp/task-${TASK}`;
const ASK_ID = "9".repeat(32);
const GRANT_PATH = `/api/ade/agents/tasks/${TASK}/grant`;
let calls: Call[] = [];

function serve(more: Record<string, Route> = {}) {
  calls = [];
  const routes: Record<string, Route> = {
    "POST /api/ade/link/nonce": (b) => [200, { nonce: `n-${String(b?.purpose)}`, rp_id: "pp.example", deployment: "pp.example", account: "u", server_time: SERVER_TIME, credentials: [] }],
    [`POST ${GRANT_PATH}`]: () => [200, { task: {}, grant: {} }],
    [`POST /api/ade/agents/tasks/grant-widenings/${ASK_ID}/refuse`]: () => [200, { widening: { id: ASK_ID, state: "refused" } }],
    "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 1 }],
    ...more,
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path: String(url), method, body });
    const route = routes[`${method} ${url}`];
    const [status, data] = route ? route(body) : [404, { detail: `no route for ${method} ${url}` }];
    return new Response(JSON.stringify(data), { status });
  }));
}

const posted = (path: string) => calls.filter((c) => c.method === "POST" && c.path === path);
const bytes = (n: number) => new Uint8Array([n]).buffer;
const credentials = {
  get: vi.fn(async (_: { publicKey: { challenge: Uint8Array; userVerification: string } }) => ({ id: "AQ", type: "public-key", response: { clientDataJSON: bytes(1), authenticatorData: bytes(2), signature: bytes(3), userHandle: bytes(4) } })),
};

/** The modules afresh, with or without passkeys in this browser (read once, when the page loads). */
async function fresh(passkeys = true) {
  vi.resetModules();
  if (passkeys) {
    vi.stubGlobal("PublicKeyCredential", function PublicKeyCredential() {});
    Object.defineProperty(navigator, "credentials", { value: credentials, configurable: true });
  }
  const { useStore: store } = await import("../store");
  const refreshApprovals = vi.fn(async () => {});
  store.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true }, refreshApprovals });
  return { ...(await import("./TaskGrant")), Approvals: (await import("./Approvals")).Approvals, store, refreshApprovals };
}

/** JSON with its keys sorted at every level: RFC 8785 for what these payloads hold (ASCII text, whole numbers), written
 *  here on its own rather than taken from the page. */
function canon(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(canon).join(",")}]`;
  if (v && typeof v === "object") {
    const o = v as Record<string, unknown>;
    return `{${Object.keys(o).sort().map((k) => `${JSON.stringify(k)}:${canon(o[k])}`).join(",")}}`;
  }
  return JSON.stringify(v);
}

/** The challenge the server checks a step's signature against (passkeys.step_challenge). */
async function challengeOf(payload: Record<string, unknown>): Promise<Uint8Array> {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`proofpane-step/1\0${canon(payload)}`)));
}

const SCOPE = {
  repositories: [{ repo: "demo", push: [BRANCH], merge: ["main"] }], environments: [], secrets: [], egress: [],
  lease_end: SERVER_TIME + 7 * 86400, rungs: { merge: "L2" as const, deploy: "L2-high" as const },
};
/** Where the next version follows (after the newest kept), and the versions withdrawn since the one that stands: none
 *  for a grant at its first version. */
const AFTER_V1 = { withdrawn: [], next: { version: 2, prev: V1 } };
const grant = (over: Partial<TaskGrantView> = {}): TaskGrantView => ({
  version: 1, digest: V1, signed_at: SERVER_TIME - 3600, requester: { kind: "owner" }, widening: null, ask: ASK,
  ask_current: true, lease_ended: false, passkey: "Laptop", ...SCOPE, ...AFTER_V1, ...over,
});
const widening = (over: Partial<GrantWideningView> = {}): GrantWideningView => ({
  id: ASK_ID, task_id: TASK, task_title: "Fix the parser", requester: { kind: "agent", id: AGENT, name: "Triage" },
  base_version: 1, base_digest: V1, ask: ASK,
  adds: { repositories: [{ repo: "demo", push: [], merge: ["release"] }], environments: [{ name: "staging", rung: "L2" }], secrets: [],
          egress: ["registry.npmjs.org"], lease_end: null, rungs: null },
  scope: { ...SCOPE, repositories: [{ repo: "demo", push: [BRANCH], merge: ["main", "release"] }], environments: [{ name: "staging", rung: "L2" }],
           egress: ["registry.npmjs.org"] },
  state: "waiting", created_at: SERVER_TIME - 60, expires_at: SERVER_TIME + 86400, decided_at: null, version: null, ...over,
});
type GrantTask = Pick<TaskView, "id" | "title" | "state" | "grant" | "grant_widenings" | "grant_for">;
const task = (over: Partial<GrantTask> = {}): GrantTask => ({
  id: TASK, title: "Fix the parser", state: "ready", grant: null, grant_widenings: [], grant_for: { repo: "demo", branch: BRANCH, ask: ASK }, ...over,
});
const region = () => screen.getByRole("region", { name: "Its grant" });

/** What the page shows, or that it failed: a grant in a shape the page cannot read leaves no region, rather than a
 *  thrown render, so that a case fails on what it reads. */
class Shown extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <div role="alert">The page failed.</div> : this.props.children; }
}
const signedPayload = () => posted(GRANT_PATH)[0].body!.payload as Record<string, unknown>;

afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); credentials.get.mockClear();
  delete (navigator as unknown as { credentials?: unknown }).credentials;
  vi.resetModules();
  useStore.setState(useStore.getInitialState());
});

describe("a task's grant, on its task", () => {
  it("shows its version, its digest, its scope and the passkey that signed it, and that nothing yet holds an attempt to it", async () => {
    serve();
    const { TaskGrant } = await fresh();
    render(<TaskGrant task={task({ grant: grant({ environments: [{ name: "production", rung: "L2-high" }],
      secrets: [{ name: "NPM_TOKEN", lease_seconds: 8 * 3600 }], egress: ["registry.npmjs.org"] }) })} onChanged={async () => {}} />);
    const box = region();
    expect(box.textContent).toContain("Version 1, signed");
    expect(box.textContent).toContain("with your passkey \u201cLaptop\u201d");
    expect(within(box).getByText(V1.slice(0, 12)).getAttribute("title")).toBe(V1);
    expect(box.textContent).toContain(`demo: pushes ${BRANCH}; merges into main`);
    expect(box.textContent).toContain("Deploys to production at a security key (L2 high)");
    expect(box.textContent).toContain("Secrets by name: NPM_TOKEN for 8 hours");
    expect(box.textContent).toContain("Reaches beyond its programs' own hosts: registry.npmjs.org");
    expect(box.textContent).toContain("Merging into a protected branch takes a passkey (L2); a deploy takes a security key (L2 high)");
    expect(box.textContent).toContain("Nothing yet holds the task's attempts to their grant: it is kept, signed and shown.");
  });

  it("signs its first grant over the payload the server takes, and the authenticator signs that payload's step challenge", async () => {
    serve();
    const { TaskGrant } = await fresh();
    const onChanged = vi.fn(async () => {});
    render(<TaskGrant task={task()} onChanged={onChanged} />);
    const box = region();
    expect(box.textContent).toContain(`No grant yet. Its first names demo with the task's own branch, ${BRANCH}, which its attempts push.`);
    fireEvent.click(within(box).getByRole("button", { name: /Give it a grant/ }));
    fireEvent.change(within(box).getByLabelText("Branches it may merge into"), { target: { value: "main" } });
    fireEvent.click(within(box).getByRole("button", { name: /^Review$/ }));
    expect(await within(box).findByText(/^You are signing version 1 of this task's grant:/)).toBeTruthy();
    fireEvent.click(within(box).getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(posted("/api/ade/link/nonce").map((c) => c.body)).toEqual([{ purpose: "task-grant" }]);
    const payload = signedPayload();
    expect(payload).toEqual({
      v: 2, deployment: "pp.example", purpose: "task-grant", account: "u", nonce: "n-task-grant", id: expect.stringMatching(/^[0-9a-f]{32}$/),
      task: TASK, version: 1, prev: "", ask: ASK, requester: { kind: "owner" }, widening: "",
      repositories: [{ repo: "demo", push: [BRANCH], merge: ["main"] }], environments: [], secrets: [], egress: [],
      lease_end: SERVER_TIME + 7 * 86400, rungs: { merge: "L2", deploy: "L2-high" }, created_at: SERVER_TIME, expires_at: SERVER_TIME + 600,
    });
    const asked = credentials.get.mock.calls[0][0].publicKey;
    expect(Array.from(asked.challenge)).toEqual(Array.from(await challengeOf(payload)));
    expect(asked.userVerification).toBe("required");
  });

  it("tells an account with no passkey that a grant takes one, and asks for no nonce and no prompt", async () => {
    serve({ "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }] });
    const { TaskGrant } = await fresh();
    render(<TaskGrant task={task()} onChanged={async () => {}} />);
    const box = region();
    fireEvent.click(within(box).getByRole("button", { name: /Give it a grant/ }));
    fireEvent.click(within(box).getByRole("button", { name: /^Review$/ }));
    expect(await within(box).findByText(/^A grant is signed with one of this account's passkeys, and it has none: add one in Account first\./)).toBeTruthy();
    expect(posted("/api/ade/link/nonce")).toEqual([]);
    expect(credentials.get).not.toHaveBeenCalled();
  });

  it("changes it in one form: what asks added is kept, each list in its one form, the lease and a rung as chosen", async () => {
    serve();
    const { TaskGrant } = await fresh();
    render(<TaskGrant task={task({ grant: grant({ repositories: [{ repo: "demo", push: [BRANCH], merge: ["main"] }, { repo: "docs", push: ["feature"], merge: [] }],
      secrets: [{ name: "OLD", lease_seconds: 90 }] }) })} onChanged={async () => {}} />);
    const box = region();
    fireEvent.click(within(box).getByRole("button", { name: /Change it/ }));
    expect((within(box).getByLabelText("Branches it may merge into") as HTMLInputElement).value).toBe("main");
    expect((within(box).getByLabelText("Secrets by name") as HTMLTextAreaElement).value).toBe("OLD 90s");
    fireEvent.change(within(box).getByLabelText("Branches it may merge into"), { target: { value: "release, main" } });
    fireEvent.change(within(box).getByLabelText("Environments it may deploy to"), { target: { value: "staging L2\nproduction\n" } });
    fireEvent.change(within(box).getByLabelText("Secrets by name"), { target: { value: "OLD 90s\nNPM_TOKEN 8h" } });
    fireEvent.change(within(box).getByLabelText("Hosts it may reach"), { target: { value: "registry.npmjs.org\napi.example.com\nregistry.npmjs.org" } });
    fireEvent.change(within(box).getByLabelText("Its lease"), { target: { value: "30" } });
    fireEvent.change(within(box).getByLabelText("Merging into a protected branch takes"), { target: { value: "L3" } });
    fireEvent.click(within(box).getByRole("button", { name: /^Review$/ }));
    expect(await within(box).findByText(/^You are signing version 2 of this task's grant:/)).toBeTruthy();
    fireEvent.click(within(box).getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(posted(GRANT_PATH)).toHaveLength(1));
    expect(signedPayload()).toMatchObject({
      version: 2, prev: V1, ask: ASK, requester: { kind: "owner" }, widening: "",
      // every repository it names carries the task's own branch
      repositories: [{ repo: "demo", push: [BRANCH], merge: ["main", "release"] }, { repo: "docs", push: ["feature", BRANCH], merge: [] }],
      environments: [{ name: "production", rung: "L2-high" }, { name: "staging", rung: "L2" }],
      secrets: [{ name: "NPM_TOKEN", lease_seconds: 8 * 3600 }, { name: "OLD", lease_seconds: 90 }],
      egress: ["api.example.com", "registry.npmjs.org"], lease_end: SERVER_TIME + 30 * 86400, rungs: { merge: "L3", deploy: "L2-high" },
    });
  });

  it("says, where it keeps a repository or a push branch an ask added, that this form cannot take one back and that taking one back is not built", async () => {
    // said plainly, and never that it can be done elsewhere, since nothing on the page or in the apps does it
    serve();
    const { TaskGrant } = await fresh();
    render(<TaskGrant task={task({ grant: grant({ repositories: [{ repo: "demo", push: [BRANCH, "release"], merge: ["main"] }, { repo: "docs", push: ["feature", BRANCH], merge: [] }] }) })} onChanged={async () => {}} />);
    const box = region();
    fireEvent.click(within(box).getByRole("button", { name: /Change it/ }));
    const kept = within(box).getByRole("note", { name: "Kept as they are" });
    expect(kept.textContent).toContain(`demo: pushes ${BRANCH}, release`);
    expect(kept.textContent).toContain(`docs: pushes feature, ${BRANCH}`);
    expect(kept.textContent).toContain("This form cannot take back a repository or a push branch an ask added: taking one back is not built yet.");
    expect(kept.textContent).not.toMatch(/elsewhere|another page|in the app|in a browser/i);
    // a grant that names nothing beyond its own repository and branch has nothing kept so, and says nothing of it
    cleanup();
    render(<TaskGrant task={task({ grant: grant() })} onChanged={async () => {}} />);
    fireEvent.click(within(region()).getByRole("button", { name: /Change it/ }));
    expect(within(region()).queryByRole("note", { name: "Kept as they are" })).toBeNull();
  });

  it("says when it pins a goal the task no longer has, and signs it again with the goal as it is now", async () => {
    serve();
    const { TaskGrant } = await fresh();
    render(<TaskGrant task={task({ grant: grant({ ask: "sha256:" + "cd".repeat(32), ask_current: false }) })} onChanged={async () => {}} />);
    const box = region();
    expect(box.textContent).toContain("It pins the task's goal as it was when it was signed, and the goal has changed since.");
    fireEvent.click(within(box).getByRole("button", { name: /Sign it again/ }));
    fireEvent.click(within(box).getByRole("button", { name: /^Review$/ }));
    await within(box).findByText(/^You are signing version 2 of this task's grant:/);
    fireEvent.click(within(box).getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(posted(GRANT_PATH)).toHaveLength(1));
    expect(signedPayload()).toMatchObject({ version: 2, prev: V1, ask: ASK, ...SCOPE });
  });

  it("says which version stands when a newer one was withdrawn, and signs the next version after the withdrawn one", async () => {
    // a version whose passkey was removed, or which no hardware key signed as high security
    // was turned on, is withdrawn, and the grant stands at the newest version a passkey still held signed
    serve();
    const { TaskGrant } = await fresh();
    const g = grant({ withdrawn: [{ version: 2, digest: V2, why: "removed", at: SERVER_TIME - 60 }], next: { version: 3, prev: V2 } } as Partial<TaskGrantView>);
    render(<Shown><TaskGrant task={task({ grant: g })} onChanged={async () => {}} /></Shown>);
    const box = region();
    expect(box.textContent).toContain("Version 1, signed");
    expect(box.textContent).toContain("Version 2 was withdrawn: the passkey that signed it was removed.");
    // the newest not withdrawn: under high security a passkey the account still holds may have signed a withdrawn one
    expect(box.textContent).toContain("It stands at version 1, the newest version not withdrawn.");
    fireEvent.click(within(box).getByRole("button", { name: /Change it/ }));
    fireEvent.click(within(box).getByRole("button", { name: /^Review$/ }));
    expect(await within(box).findByText(/^You are signing version 3 of this task's grant:/)).toBeTruthy();
    fireEvent.click(within(box).getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(posted(GRANT_PATH)).toHaveLength(1));
    expect(signedPayload()).toMatchObject({ version: 3, prev: V2, ask: ASK, ...SCOPE });
  });

  it("says that no version stands when each was withdrawn, and gives it a grant as the version after them", async () => {
    serve();
    const { TaskGrant } = await fresh();
    const none = { version: null, digest: null, withdrawn: [{ version: 1, digest: V1, why: "high_security", at: SERVER_TIME - 60 }], next: { version: 2, prev: V1 } };
    render(<Shown><TaskGrant task={task({ grant: none as unknown as GrantTask["grant"] })} onChanged={async () => {}} /></Shown>);
    const box = region();
    expect(box.textContent).toContain("No version of its grant stands.");
    expect(box.textContent).toContain("Version 1 was withdrawn: high security was turned on, and no hardware key signed it.");
    expect(box.textContent).not.toContain("No grant yet");
    expect(box.textContent).not.toContain("Version 1, signed");
    expect(within(box).queryByRole("button", { name: /Change it|Sign it again/ })).toBeNull();
    fireEvent.click(within(box).getByRole("button", { name: /Give it a grant/ }));
    expect(within(box).queryByRole("option", { name: /its lease as it is/ })).toBeNull();
    fireEvent.change(within(box).getByLabelText("Branches it may merge into"), { target: { value: "main" } });
    fireEvent.click(within(box).getByRole("button", { name: /^Review$/ }));
    expect(await within(box).findByText(/^You are signing version 2 of this task's grant:/)).toBeTruthy();
    fireEvent.click(within(box).getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(posted(GRANT_PATH)).toHaveLength(1));
    expect(signedPayload()).toMatchObject({ version: 2, prev: V1, ask: ASK, ...SCOPE });
  });

  it("is shown on an ended task with nothing to change it", async () => {
    serve();
    const { TaskGrant } = await fresh();
    render(<TaskGrant task={task({ state: "done", grant: grant() })} onChanged={async () => {}} />);
    const box = region();
    expect(box.textContent).toContain("Version 1, signed");
    expect(box.textContent).toContain("The task has ended: its grant does not change.");
    expect(within(box).queryAllByRole("button")).toEqual([]);
  });

  it("control: is not shown by a server from before grants, which says nothing of them", async () => {
    serve();
    const { TaskGrant } = await fresh();
    const { container } = render(<TaskGrant task={task({ grant: undefined, grant_widenings: undefined, grant_for: undefined })} onChanged={async () => {}} />);
    expect(container.textContent).toBe("");
  });
});

describe("a new task's first grant", () => {
  it("where this page signs, composes the new task's first version and signs it with a passkey", async () => {
    serve();
    const { FirstGrant } = await fresh();
    const onDone = vi.fn(async () => {});
    render(<Shown><FirstGrant task={task()} onDone={onDone} /></Shown>);
    const box = screen.getByRole("region", { name: "Its first grant" });
    fireEvent.change(await within(box).findByLabelText("Branches it may merge into"), { target: { value: "main" } });
    expect(box.textContent).toContain(`The task is made. Sign its first grant now: it names demo with the task's own branch, ${BRANCH}, which its attempts push.`);
    fireEvent.click(within(box).getByRole("button", { name: /^Review$/ }));
    expect(await within(box).findByText(/^You are signing version 1 of this task's grant:/)).toBeTruthy();
    fireEvent.click(within(box).getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(signedPayload()).toMatchObject({ version: 1, prev: "", ask: ASK, requester: { kind: "owner" }, widening: "", ...SCOPE });
  });

  it("is left for later with Not now, the task kept as it was made, with no grant", async () => {
    serve();
    const { FirstGrant } = await fresh();
    const onDone = vi.fn(async () => {});
    render(<Shown><FirstGrant task={task()} onDone={onDone} /></Shown>);
    fireEvent.click(await within(screen.getByRole("region", { name: "Its first grant" })).findByRole("button", { name: "Not now" }));
    await vi.waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(posted(GRANT_PATH)).toEqual([]);
    expect(posted("/api/ade/link/nonce")).toEqual([]);
  });

  it("where passkeys do not work (the phone and desktop apps), says the task is made without a grant and where one is given", async () => {
    serve();
    const { FirstGrant } = await fresh(false);
    const onDone = vi.fn(async () => {});
    render(<Shown><FirstGrant task={task()} onDone={onDone} /></Shown>);
    const box = screen.getByRole("region", { name: "Its first grant" });
    // the host is the app's server, which this page's tests do not set (as GRANT_ELSEWHERE's case reads up to it)
    expect(box.textContent).toContain("The task is made without a grant: a grant is signed with one of your passkeys, in a browser at");
    expect(box.textContent).toContain("Give it one there, from the task.");
    expect(within(box).queryByRole("button", { name: /Review/ })).toBeNull();
    fireEvent.click(within(box).getByRole("button", { name: "Done" }));
    await vi.waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(posted("/api/ade/link/nonce")).toEqual([]);
  });

  it("where this page signs and the account's passkeys could not be read, offers the form rather than saying it has none", async () => {
    serve({ "GET /api/account/confirm": () => [500, { detail: "down" }] });
    const { FirstGrant } = await fresh();
    render(<Shown><FirstGrant task={task()} onDone={async () => {}} /></Shown>);
    const box = screen.getByRole("region", { name: "Its first grant" });
    await vi.waitFor(() => expect(within(box).queryByLabelText("Reading this account's passkeys")).toBeNull());
    expect(box.textContent).toContain("The task is made. Sign its first grant now:");
    expect(box.textContent).not.toContain("The task is made without a grant");
    expect(within(box).getByRole("button", { name: /^Review$/ })).toBeTruthy();
  });

  it("for an account with no passkey, says the task is made without a grant, and asks for no nonce", async () => {
    serve({ "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }] });
    const { FirstGrant } = await fresh();
    const onDone = vi.fn(async () => {});
    render(<Shown><FirstGrant task={task()} onDone={onDone} /></Shown>);
    const box = screen.getByRole("region", { name: "Its first grant" });
    expect(await within(box).findByText(/^The task is made without a grant: a grant is signed with one of this account's passkeys, and it has none\./)).toBeTruthy();
    expect(within(box).queryByRole("button", { name: /Review/ })).toBeNull();
    fireEvent.click(within(box).getByRole("button", { name: "Done" }));
    await vi.waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(posted("/api/ade/link/nonce")).toEqual([]);
  });
});

describe("an ask for more in a task's grant", () => {
  it("is signed as it was asked: the version after the one it widens, that version's digest, who asked and the scope it makes", async () => {
    serve();
    const { TaskGrant } = await fresh();
    const w = widening();
    render(<TaskGrant task={task({ grant: grant(), grant_widenings: [w] })} onChanged={async () => {}} />);
    const item = screen.getByRole("group", { name: "An ask for more in its grant" });
    expect(item.textContent).toContain("Its agent \u201cTriage\u201d asks for more in its grant:");
    expect(item.textContent).toContain("demo: merges into release");
    expect(item.textContent).toContain("Deploys to staging at a passkey (L2)");
    expect(item.textContent).toContain("Reaches beyond its programs' own hosts: registry.npmjs.org");
    fireEvent.click(within(item).getByRole("button", { name: /Review and sign/ }));
    expect(await within(item).findByText(/^You are signing version 2 of this task's grant, as its agent asked:/)).toBeTruthy();
    fireEvent.click(within(item).getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(posted(GRANT_PATH)).toHaveLength(1));
    const payload = signedPayload();
    expect(payload).toMatchObject({ purpose: "task-grant", task: TASK, version: 2, prev: V1, ask: ASK, widening: ASK_ID });
    expect(payload.requester).toEqual({ kind: "agent", id: AGENT });        // its name is the page's, never signed
    for (const k of ["repositories", "environments", "secrets", "egress", "lease_end", "rungs"] as const) {
      expect(payload[k], k).toEqual(w.scope[k]);
    }
  });

  it("from a linked session, of a task with no grant, is signed as version 1 naming that session", async () => {
    serve();
    const { TaskGrant } = await fresh();
    const w = widening({ requester: { kind: "link", id: LINK, name: "mac" }, base_version: 0, base_digest: "" });
    render(<TaskGrant task={task({ grant_widenings: [w] })} onChanged={async () => {}} />);
    const item = screen.getByRole("group", { name: "An ask for more in its grant" });
    expect(item.textContent).toContain("A linked session \u201cmac\u201d asks for more in its grant:");
    fireEvent.click(within(item).getByRole("button", { name: /Review and sign/ }));
    await within(item).findByText(/^You are signing version 1 of this task's grant, as a linked session asked:/);
    fireEvent.click(within(item).getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(posted(GRANT_PATH)).toHaveLength(1));
    expect(signedPayload()).toMatchObject({ version: 1, prev: "", requester: { kind: "link", id: LINK }, widening: ASK_ID });
  });

  it("from a linked session since revoked, says so, and is refused, never signed", async () => {
    // what a session revoked for misbehaving, or for a lost machine, asked for is not applied
    serve();
    const { TaskGrant } = await fresh();
    const w = widening({ requester: { kind: "link", id: LINK, name: "mac", revoked: true } as GrantWideningView["requester"] });
    render(<TaskGrant task={task({ grant: grant(), grant_widenings: [w] })} onChanged={async () => {}} />);
    const item = screen.getByRole("group", { name: "An ask for more in its grant" });
    expect(item.textContent).toContain("A linked session \u201cmac\u201d, since revoked, asks for more in its grant:");
    expect(item.textContent).toContain("It was revoked: this ask is not signed, and refusing it clears it.");
    expect(within(item).queryByRole("button", { name: /Review and sign/ })).toBeNull();
    expect(within(item).getByRole("button", { name: "Refuse" })).toBeTruthy();
  });

  it("is refused with the sign-in alone: no nonce, no passkey prompt", async () => {
    serve();
    const { TaskGrant } = await fresh();
    const onChanged = vi.fn(async () => {});
    render(<TaskGrant task={task({ grant: grant(), grant_widenings: [widening()] })} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "Refuse" }));
    await vi.waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(posted(`/api/ade/agents/tasks/grant-widenings/${ASK_ID}/refuse`)).toHaveLength(1);
    expect(posted("/api/ade/link/nonce")).toEqual([]);
    expect(credentials.get).not.toHaveBeenCalled();
  });

  it("where passkeys do not work (the phone and desktop apps), is signed nowhere here, and refused still", async () => {
    serve();
    const { TaskGrant } = await fresh(false);
    render(<TaskGrant task={task({ grant_widenings: [widening({ base_version: 0, base_digest: "" })] })} onChanged={async () => {}} />);
    const box = region();
    expect(box.textContent).toContain("A grant is signed with one of your passkeys, which work only in a browser at");
    expect(within(box).queryByRole("button", { name: /Give it a grant|Review and sign|Change it|Sign it again/ })).toBeNull();
    expect(within(box).getByRole("button", { name: "Refuse" })).toBeTruthy();
  });
});

describe("the owner's list (Approvals)", () => {
  it("holds each ask for more in a task's grant, by its task, and says nothing waits only when none does", async () => {
    serve();
    const { Approvals, refreshApprovals } = await fresh();
    const { unmount } = render(<Approvals records={[]} requests={[]} grantWidenings={[widening()]} onClose={() => {}} onOpen={() => {}} />);
    expect(screen.queryByText("Nothing is waiting for you.")).toBeNull();
    const item = screen.getByRole("group", { name: "An ask for more in its grant" });
    expect(item.textContent).toContain("Fix the parser");
    expect(item.textContent).toContain("Its agent \u201cTriage\u201d asks for more in its grant:");
    fireEvent.click(within(item).getByRole("button", { name: "Refuse" }));
    await vi.waitFor(() => expect(refreshApprovals).toHaveBeenCalled());
    expect(posted(`/api/ade/agents/tasks/grant-widenings/${ASK_ID}/refuse`)).toHaveLength(1);
    unmount();
    render(<Approvals records={[]} requests={[]} grantWidenings={[]} onClose={() => {}} onOpen={() => {}} />);
    expect(screen.getByText("Nothing is waiting for you.")).toBeTruthy();
  });
});

describe("an ask against the grant as it stands", () => {
  // what the server gives beside an ask: the scope of the grant that stands (null where none does) and the task's goal
  const against = (stands: unknown, over: Partial<GrantWideningView> = {}) =>
    widening({ ...over, ...({ stands, goal: "Make the file two lines long." } as Partial<GrantWideningView>) });
  const shown = async (w: GrantWideningView) => {
    const { Approvals } = await fresh();
    render(<Approvals records={[]} requests={[]} grantWidenings={[w]} onClose={() => {}} onOpen={() => {}} />);
    return screen.getByRole("group", { name: "An ask for more in its grant" });
  };

  it("says each value it changes from what to what, a lowered rung as a lowering, and the goal the version pins", async () => {
    serve();
    const stands = { ...SCOPE, environments: [{ name: "production", rung: "L3" as const }], rungs: { merge: "L3" as const, deploy: "L3" as const } };
    const item = await shown(against(stands, {
      requester: { kind: "link", id: LINK, name: "mac" },
      adds: { repositories: [], environments: [{ name: "production", rung: "L2" }], secrets: [], egress: [], lease_end: null, rungs: { merge: "L2", deploy: "L2" } },
      scope: { ...stands, environments: [{ name: "production", rung: "L2" }], rungs: { merge: "L2", deploy: "L2" } },
    }));
    expect(item.textContent).toContain("Deploying to production takes a passkey (L2), lowered from the signing device (L3)");
    expect(item.textContent).toContain("Merging into a protected branch takes a passkey (L2), lowered from the signing device (L3)");
    expect(item.textContent).toContain("A deploy takes a passkey (L2), lowered from the signing device (L3)");
    expect(item.textContent).not.toMatch(/demo|lease ends|Deploys to/);       // what it leaves as it stands is not listed
    expect(within(item).getByRole("note", { name: "The goal it pins" }).textContent)
      .toBe("The version it makes pins the task's goal as it is now: \u201cMake the file two lines long.\u201d");
  });

  it("says what it adds as new, and a value it moves from what to what, raised where a rung is raised", async () => {
    serve();
    const stands = { ...SCOPE, secrets: [{ name: "TOKEN", lease_seconds: 3600 }] };
    const scope = { ...stands, repositories: [{ repo: "demo", push: [BRANCH], merge: ["main", "release"] }, { repo: "docs", push: [BRANCH], merge: [] }],
      environments: [{ name: "staging", rung: "L2" as const }], secrets: [{ name: "TOKEN", lease_seconds: 8 * 3600 }], egress: ["pypi.org"],
      lease_end: SERVER_TIME + 30 * 86400, rungs: { merge: "L3" as const, deploy: "L2-high" as const } };
    const text = (await shown(against(stands, { scope }))).textContent;
    expect(text).toContain("demo, the branches it merges into: from main to main, release");
    expect(text).toContain(`docs, new: pushes ${BRANCH}; merges into none`);
    expect(text).toContain("Deploys to staging, new, at a passkey (L2)");
    expect(text).toContain("Secret TOKEN's lease: from 1 hour to 8 hours");
    expect(text).toContain("Reaches beyond its programs' own hosts, new: pypi.org");
    expect(text).toContain(`Its lease ends ${dayTime(SERVER_TIME + 30 * 86400)}, moved from ${dayTime(SCOPE.lease_end)}`);
    expect(text).toContain("Merging into a protected branch takes the signing device (L3), raised from a passkey (L2)");
    expect(text).not.toContain("A deploy takes");                              // its rung as it stands
    expect(text).not.toContain("the branches it pushes");                     // demo pushes what it pushed
  });

  it("writes out, in the goal it shows, each character that is not shown as itself", async () => {
    serve();
    const item = await shown(widening({ ...({ stands: SCOPE, goal: "Ship it\u202enow" } as Partial<GrantWideningView>) }));
    const note = within(item).getByRole("note", { name: "The goal it pins" });
    expect(note.textContent).toContain("Ship it");
    expect(note.textContent).not.toContain("\u202e");
  });

  it("says, of a task with no grant standing, the first grant the ask makes", async () => {
    serve();
    const text = (await shown(against(null, { base_version: 0, base_digest: "" }))).textContent;
    expect(text).toContain(`demo, new: pushes ${BRANCH}; merges into main, release`);
    expect(text).toContain("Deploys to staging, new, at a passkey (L2)");
    expect(text).toContain("Reaches beyond its programs' own hosts, new: registry.npmjs.org");
    expect(text).toContain(`Its lease ends ${dayTime(SCOPE.lease_end)}`);
    expect(text).toContain("Merging into a protected branch takes a passkey (L2); a deploy takes a security key (L2 high)");
  });
});

describe("the store's approvals", () => {
  it("are read with the asks for more in tasks' grants, and a server from before grants is not asked for them again", async () => {
    serve({ "GET /api/ade/approvals": () => [200, { requests: [] }], "GET /api/ade/agents/tasks/grant-widenings": () => [200, { widenings: [widening()] }] });
    const { store } = await fresh();
    store.setState({ proposalsOff: true, refreshApprovals: store.getInitialState().refreshApprovals });
    await store.getState().refreshApprovals();
    expect((store.getState().grantWidenings ?? []).map((w) => w.id)).toEqual([ASK_ID]);
    serve({ "GET /api/ade/approvals": () => [200, { requests: [] }] });     // a server from before grants: no such route
    await store.getState().refreshApprovals();
    expect(store.getState().grantWidenings).toEqual([]);
    await store.getState().refreshApprovals();
    expect(calls.filter((c) => c.path === "/api/ade/agents/tasks/grant-widenings")).toHaveLength(1);
  });

  it("take a refusal of the asks' route (403 or 405) as a server with no grants, as a 404 is, and ask it no more; a 401 is the sign-in's", async () => {
    // a server from before grants reaches GET /tasks/{id} with the asks' path: a 403 where the account may have no
    // workspace, a 405 where that route takes no GET
    const stamps = { sessions: null, repos: null, approvals: "s1", proposals: null, asks: null, links: null, history: null };
    const summary = { workspace: null, approvals: 0, proposals: null, asks: null, links: { live: 0, alerts: 0 }, agents: null, stamps };
    for (const status of [403, 405, 401]) {
      serve({ "GET /api/ade/approvals": () => [200, { requests: [] }], "GET /api/ade/agents/tasks/grant-widenings": () => [status, { detail: "refused" }] });
      const { store } = await fresh();
      store.setState({ proposalsOff: true, refreshApprovals: store.getInitialState().refreshApprovals, summary });
      await store.getState().refreshApprovals();
      const asked = () => calls.filter((c) => c.path === "/api/ade/agents/tasks/grant-widenings").length;
      if (status === 401) {
        expect(store.getState().grantWideningsOff, "401").toBe(false);
        expect(store.getState().read.approvals, "401").toBeUndefined();
        continue;
      }
      expect([store.getState().grantWideningsOff, store.getState().grantWidenings], String(status)).toEqual([true, []]);
      expect(store.getState().read.approvals, String(status)).toBe("s1");
      await store.getState().refreshApprovals();
      expect(asked(), String(status)).toBe(1);
    }
  });

  it("take any other refusal of the asks' route (400, 409, 422 or 429) as a list not read this time, and ask it again (every 4xx but a 401)", async () => {
    // a server with grants answers these when it is busy or refuses this request: the list is not read yet, and never
    // taken as absent for the page's life
    const stamps = { sessions: null, repos: null, approvals: "s1", proposals: null, asks: null, links: null, history: null };
    const summary = { workspace: null, approvals: 0, proposals: null, asks: null, links: { live: 0, alerts: 0 }, agents: null, stamps };
    for (const status of [400, 409, 422, 429]) {
      serve({ "GET /api/ade/approvals": () => [200, { requests: [] }], "GET /api/ade/agents/tasks/grant-widenings": () => [status, { detail: "not now" }] });
      const { store } = await fresh();
      store.setState({ proposalsOff: true, refreshApprovals: store.getInitialState().refreshApprovals, summary });
      await store.getState().refreshApprovals();
      expect(store.getState().grantWideningsOff, String(status)).toBe(false);
      expect(store.getState().read.approvals, String(status)).toBeUndefined();
      await store.getState().refreshApprovals();
      expect(calls.filter((c) => c.path === "/api/ade/agents/tasks/grant-widenings").length, String(status)).toBe(2);
    }
  });

  it("are not taken as read at the summary's stamp while the asks for more in tasks' grants could not be read", async () => {
    serve({ "GET /api/ade/approvals": () => [200, { requests: [] }], "GET /api/ade/agents/tasks/grant-widenings": () => [500, { detail: "down" }] });
    const { store } = await fresh();
    const stamps = { sessions: null, repos: null, approvals: "s1", proposals: null, asks: null, links: null, history: null };
    store.setState({ proposalsOff: true, refreshApprovals: store.getInitialState().refreshApprovals,
                     summary: { workspace: null, approvals: 1, proposals: null, asks: null, links: { live: 0, alerts: 0 }, agents: null, stamps } });
    await store.getState().refreshApprovals();
    expect(store.getState().read.approvals).toBeUndefined();     // so the next summary reads the list again
    serve({ "GET /api/ade/approvals": () => [200, { requests: [] }], "GET /api/ade/agents/tasks/grant-widenings": () => [200, { widenings: [] }] });
    await store.getState().refreshApprovals();
    expect(store.getState().read.approvals).toBe("s1");
  });
});

describe("a grant's payload (passkey.ts, prepareGrant)", () => {
  it("names each member it signs: a requester's name, or a member an entry of its scope carries beyond its own, is not signed", async () => {
    serve();
    await fresh();
    const { prepareGrant } = await import("../passkey");
    const extra = { surplus: 1 };
    const p = await prepareGrant(
      { task: TASK, version: 2, prev: V1, ask: ASK, requester: { kind: "agent", id: AGENT, name: "Triage" } as unknown as { kind: "agent"; id: string }, widening: ASK_ID },
      { repositories: [{ repo: "demo", push: [BRANCH], merge: [], ...extra }], environments: [{ name: "staging", rung: "L2", ...extra }],
        secrets: [{ name: "TOKEN", lease_seconds: 600, ...extra }], egress: ["pypi.org"], lease_end: SERVER_TIME + 3600,
        rungs: { merge: "L2", deploy: "L3", ...extra } } as never);
    expect(p.payload.requester).toEqual({ kind: "agent", id: AGENT });
    expect(p.payload.repositories).toEqual([{ repo: "demo", push: [BRANCH], merge: [] }]);
    expect(p.payload.environments).toEqual([{ name: "staging", rung: "L2" }]);
    expect(p.payload.secrets).toEqual([{ name: "TOKEN", lease_seconds: 600 }]);
    expect(p.payload.rungs).toEqual({ merge: "L2", deploy: "L3" });
    expect(Array.from(p.challenge)).toEqual(Array.from(await challengeOf(p.payload)));
  });
});
