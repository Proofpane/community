import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { HookRequest, Proposal } from "../api";
import { useStore } from "../store";

// What a connected app proposed, signed or accepted only by the owner's own word on the page: the signed payload's id
// is the proposal's, so the server knows which proposal the command is.

type Call = { path: string; method: string; body: Record<string, unknown> | undefined };
type Route = (body: Record<string, unknown> | undefined) => [number, unknown];
const SERVER_TIME = 1_790_000_000;
const PID = "0123456789abcdef0123456789abcdef";
let calls: Call[] = [];

function serve(more: Record<string, Route> = {}) {
  calls = [];
  const routes: Record<string, Route> = {
    "POST /api/ade/link/nonce": (b) => [200, { nonce: `n-${String(b?.purpose)}`, rp_id: "pp.example", deployment: "pp.example", account: "u", server_time: SERVER_TIME }],
    "POST /api/ade/link/commands": () => [200, { seq: 9, expires_at: SERVER_TIME + 3600 }],
    "POST /api/ade/link/links/L1/resume": () => [200, { seq: 10 }],
    "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
    "POST /api/account/confirm": () => [200, { confirmation: "conf-1" }],
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
  get: vi.fn(async () => ({ id: "AQ", type: "public-key", response: { clientDataJSON: bytes(1), authenticatorData: bytes(2), signature: bytes(3), userHandle: bytes(4) } })),
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
  return { ...(await import("./Proposals")), Approvals: (await import("./Approvals")).Approvals, refreshApprovals };
}

const proposal = (over: Partial<Proposal> = {}): Proposal => ({
  id: PID, kind: "instruction", state: "proposed", target: "link:L1", session: "mac", link_id: "L1", app: "Claude", grant_id: "g1",
  text: "run the tests", decision: null, why: "the build is red", made_at: SERVER_TIME - 60, expires_at: SERVER_TIME + 4 * 3600,
  decided_at: null, seq: null, edited: false, said: null, ref: null, request_id: null, ...over,
});
const held = (over: Partial<HookRequest> = {}): HookRequest => ({ id: "req-1", command: "git push origin main", cwd: "/work", state: "pending", created_at: SERVER_TIME, ...over });

afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); credentials.get.mockClear();
  delete (navigator as unknown as { credentials?: unknown }).credentials;
  vi.resetModules();
  useStore.setState(useStore.getInitialState());
});

describe("proposals, grouped by the app that made them", () => {
  it("lists newest first under each app, and leaves a held command's answer to the command's own item", async () => {
    const { byApp } = await fresh();
    const ps = [
      proposal({ id: "a".repeat(32), made_at: 10 }), proposal({ id: "b".repeat(32), made_at: 30 }),
      proposal({ id: "c".repeat(32), made_at: 20, grant_id: "g2", app: "Other" }),
      proposal({ id: "d".repeat(32), made_at: 40, kind: "approval", request_id: "req-1", link_id: null }),
      proposal({ id: "e".repeat(32), made_at: 50, kind: "approval", request_id: "req-gone", link_id: null }),
      proposal({ id: "f".repeat(32), made_at: 60, state: "refused" }),
    ];
    const groups = byApp(ps, [held()]);
    expect(groups.map((g) => [g.app, g.items.map((p) => p.id[0])])).toEqual([["Claude", ["e", "b", "a"]], ["Other", ["c"]]]);
  });

  it("are counted in the badge, but a held command's answer counts as its command", async () => {
    const { proposalAlerts } = await import("../api");
    expect(proposalAlerts([proposal(), proposal({ kind: "approval" }), proposal({ kind: "stop", state: "sent" })])).toBe(1);
    expect(proposalAlerts(undefined)).toBe(0);
  });
});

describe("an instruction a connected app proposed", () => {
  it("is signed as the command it proposes, the payload's id the proposal's, saying who proposed it", async () => {
    serve();
    const { Proposals, refreshApprovals } = await fresh();
    render(<Proposals proposals={[proposal()]} requests={[]} />);
    const card = screen.getByLabelText("An instruction for mac");
    expect(card.textContent).toContain("Its words, the app's until you sign them:");
    expect(card.textContent).toContain("run the tests");
    expect(card.textContent).toContain("Its reason, its own word: the build is red");
    expect(screen.getByText(/Proposed by “/).textContent).toContain("Claude");
    fireEvent.click(screen.getByRole("button", { name: /Review and sign/ }));
    expect((screen.getByLabelText("What mac should do") as HTMLTextAreaElement).value).toBe("run the tests");
    fireEvent.click(screen.getByRole("button", { name: /^Review$/ }));
    expect(await screen.findByText(/^You are signing an instruction to mac \(link L1\), proposed by “Claude” \(its own word\), good until .+:$/)).toBeTruthy();
    expect(screen.getByText("Words proposed by “Claude”: once you sign them, they are your instruction to mac.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(refreshApprovals).toHaveBeenCalled());
    const [{ body }] = posted("/api/ade/link/commands");
    expect(body?.payload).toMatchObject({ purpose: "instruction", to: "L1", text: "run the tests", id: PID });
  });

  it("draws an app's name with what is not shown as itself written out, clipped to its own lines", async () => {
    serve();
    const { Proposals } = await fresh();
    const filler = String.fromCodePoint(0x3164);       // a Hangul filler, drawn as nothing; the server now refuses it
    render(<Proposals proposals={[proposal({ app: `Claude${filler}` })]} requests={[]} />);
    const head = screen.getByText(/Proposed by “/);
    expect(head.textContent).toContain("Claude[U+3164]");
    expect(head.querySelector("bdi")?.classList.contains("overflow-hidden")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /Review and sign/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Review$/ }));
    const what = await screen.findByText(/^You are signing an instruction to mac \(link L1\), proposed by “Claude\[U\+3164\]” \(its own word\)/);
    expect(what.classList.contains("overflow-hidden")).toBe(true);
    const said = screen.getByText("Words proposed by “Claude[U+3164]”: once you sign them, they are your instruction to mac.");
    expect(said.classList.contains("overflow-hidden")).toBe(true);
  });

  it("says so in the same way when the owner changed the words", async () => {
    serve();
    const { Proposals } = await fresh();
    render(<Proposals proposals={[proposal({ app: `Claude${String.fromCodePoint(0x3164)}` })]} requests={[]} />);
    fireEvent.click(screen.getByRole("button", { name: /Review and sign/ }));
    fireEvent.change(screen.getByLabelText("What mac should do"), { target: { value: "run the fast tests" } });
    fireEvent.click(screen.getByRole("button", { name: /^Review$/ }));
    const said = await screen.findByText("You changed the words “Claude[U+3164]” proposed: once you sign them, they are your instruction to mac.");
    expect(said.classList.contains("overflow-hidden")).toBe(true);
  });

  it("may be changed before it is signed, and the review says so", async () => {
    serve();
    const { Proposals } = await fresh();
    render(<Proposals proposals={[proposal()]} requests={[]} />);
    fireEvent.click(screen.getByRole("button", { name: /Review and sign/ }));
    fireEvent.change(screen.getByLabelText("What mac should do"), { target: { value: "run the fast tests" } });
    fireEvent.click(screen.getByRole("button", { name: /^Review$/ }));
    expect(await screen.findByText("You changed the words “Claude” proposed: once you sign them, they are your instruction to mac.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(posted("/api/ade/link/commands")).toHaveLength(1));
    expect(posted("/api/ade/link/commands")[0].body?.payload).toMatchObject({ text: "run the fast tests", id: PID });
  });

  it("shows its words whole for review, with how long they are, never in a box that scrolls", async () => {
    serve();
    const { Proposals } = await fresh();
    const lines = Array.from({ length: 80 }, (_, i) => `step ${i + 1}: ${"x".repeat(40)}`);
    const text = lines.join("\n");
    render(<Proposals proposals={[proposal({ text })]} requests={[]} />);
    const card = screen.getByLabelText("An instruction for mac");
    expect(card.textContent).toContain(`Its words, the app's until you sign them: ${[...text].length.toLocaleString("en-US")} characters on 80 lines, all shown.`);
    const words = screen.getByLabelText("Its words, whole");
    expect(words.textContent).toBe(text);
    expect(words.className).not.toMatch(/max-h|overflow/);
    expect(screen.queryAllByRole("note")).toEqual([]);
  });

  it("flags what in the words breaks the rules an app's words are held to", async () => {
    serve();
    const { Proposals, wordsFacts } = await fresh();
    const cp = (code: number) => String.fromCodePoint(code);
    const text = `do no${cp(0x0336)}t merge x${cp(0x0301).repeat(3)}${cp(0x200b)}`;
    render(<Proposals proposals={[proposal({ text })]} requests={[]} />);
    const notes = screen.getAllByRole("note").map((n) => n.textContent);
    expect(notes).toEqual([
      "It holds characters not shown as themselves: each is written here as [U+XXXX].",
      "It holds a mark drawn over or around a character, which could change how it reads.",
      "It holds a character with more than 2 marks on it, which are drawn over the lines around it.",
    ]);
    expect(wordsFacts("x".repeat(4001)).flags).toEqual(["It is longer than the 4,000 characters a command may be."]);
    expect(wordsFacts("one").size).toBe("3 characters on 1 line, all shown");
  });

  it("where passkeys do not work, says where to sign it, and still may be refused", async () => {
    serve();
    const { Proposals } = await fresh(false);
    render(<Proposals proposals={[proposal()]} requests={[]} />);
    expect(screen.queryByRole("button", { name: /Review and sign/ })).toBeNull();
    expect(screen.getByText(/Commands are signed with one of your passkeys/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Refuse" })).toBeTruthy();
  });
});

describe("an answer and a resume a connected app proposed", () => {
  it("an answer carries its question's seq and digest, its id the proposal's", async () => {
    serve();
    const { Proposals } = await fresh();
    const { textDigest } = await import("../passkey");
    render(<Proposals proposals={[proposal({ kind: "answer", ref: 7, question: "which cap?", text: "per user", target: "question:7" })]} requests={[]} />);
    expect(screen.getByLabelText("An answer to question [7] from mac").textContent).toContain("which cap?");
    fireEvent.click(screen.getByRole("button", { name: /Review and sign/ }));
    fireEvent.click(screen.getByRole("button", { name: /Review the answer/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(posted("/api/ade/link/commands")).toHaveLength(1));
    expect(posted("/api/ade/link/commands")[0].body?.payload).toMatchObject({ purpose: "answer", ref: 7, digest: await textDigest("which cap?"), text: "per user", id: PID });
  });

  it("an answer whose question has gone cannot be signed", async () => {
    serve();
    const { Proposals } = await fresh();
    render(<Proposals proposals={[proposal({ kind: "answer", ref: 7, question: null })]} requests={[]} />);
    expect((screen.getByRole("button", { name: /Review and sign/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Its question has gone, so it cannot be signed.")).toBeTruthy();
  });

  it("never shows an envelope: a question sealed since, or words shaped as one, are said to be and not signed", async () => {
    serve();
    const { Proposals, QUESTION_SEALED, ENVELOPE_WORDS } = await fresh();
    const envelope = "e2e1." + "A".repeat(120);
    const { container } = render(<Proposals requests={[]} proposals={[
      proposal({ id: "1".repeat(32), kind: "answer", ref: 7, question: envelope, text: "per user", made_at: 3 }),
      proposal({ id: "2".repeat(32), kind: "answer", ref: 8, question: null, question_encrypted: true, made_at: 2 }),
      proposal({ id: "3".repeat(32), text: envelope, why: envelope, made_at: 1 }),
    ]} />);
    expect(container.textContent).not.toContain("e2e1.");
    expect(screen.getAllByText(QUESTION_SEALED)).toHaveLength(2);
    expect(screen.queryByText("Its question has gone, so it cannot be signed.")).toBeNull();
    expect(screen.getByLabelText("An instruction for mac").textContent).toContain(ENVELOPE_WORDS);
    for (const b of screen.getAllByRole("button", { name: /Review and sign/ })) expect((b as HTMLButtonElement).disabled).toBe(true);
  });

  it("a resume is signed after the stop it lifts, its id the proposal's", async () => {
    serve();
    const { Proposals } = await fresh();
    render(<Proposals proposals={[proposal({ kind: "resume", text: null, stopped_at: SERVER_TIME + 5 })]} requests={[]} />);
    fireEvent.click(screen.getByRole("button", { name: /Review and sign/ }));
    fireEvent.click(screen.getByRole("button", { name: /Review the resume/ }));
    expect(await screen.findByText(/^You are signing a resume of mac \(link L1\), proposed by “Claude”/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(posted("/api/ade/link/links/L1/resume")).toHaveLength(1));
    const payload = posted("/api/ade/link/links/L1/resume")[0].body?.payload as Record<string, unknown>;
    expect(payload).toMatchObject({ purpose: "resume", id: PID, text: "" });
    expect(payload.created_at as number).toBeGreaterThan(SERVER_TIME + 5);
  });

  it("a payload's id that is not a proposal's shape is refused before anything is asked", async () => {
    serve();
    await fresh();
    const { prepareSigned } = await import("../passkey");
    await expect(prepareSigned("instruction", "L1", { text: "x", id: "not-an-id" })).rejects.toThrow(/32 hex digits/);
    expect(calls).toEqual([]);
  });
});

describe("a stop, a refusal and refusing all", () => {
  it("a stop is accepted with two clicks", async () => {
    serve({ [`POST /api/connector/proposals/${PID}/accept`]: () => [200, proposal({ kind: "stop", state: "sent" })] });
    const { Proposals, refreshApprovals } = await fresh();
    render(<Proposals proposals={[proposal({ kind: "stop", text: null })]} requests={[]} />);
    fireEvent.click(screen.getByRole("button", { name: /Stop mac/ }));
    expect(posted(`/api/connector/proposals/${PID}/accept`)).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: /Click again to stop mac/ }));
    await vi.waitFor(() => expect(refreshApprovals).toHaveBeenCalled());
    expect(posted(`/api/connector/proposals/${PID}/accept`)).toHaveLength(1);
  });

  it("is refused with words the app reads back", async () => {
    serve({ [`POST /api/connector/proposals/${PID}/refuse`]: () => [200, proposal({ state: "refused" })] });
    const { Proposals, refreshApprovals } = await fresh();
    render(<Proposals proposals={[proposal()]} requests={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Refuse" }));
    fireEvent.change(screen.getByLabelText("Words for the app (it reads them back)"), { target: { value: "  not now " } });
    fireEvent.click(screen.getByRole("button", { name: /Refuse it/ }));
    await vi.waitFor(() => expect(refreshApprovals).toHaveBeenCalled());
    expect(posted(`/api/connector/proposals/${PID}/refuse`)[0].body).toEqual({ words: "not now" });
  });

  it("an app's waiting proposals are refused all at once with two clicks", async () => {
    serve({ "POST /api/connector/grants/g1/refuse-all": () => [200, { refused: 2 }] });
    const { Proposals, refreshApprovals } = await fresh();
    render(<Proposals proposals={[proposal(), proposal({ id: "1".repeat(32), kind: "stop" })]} requests={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Refuse all" }));
    expect(posted("/api/connector/grants/g1/refuse-all")).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Click again to refuse all 2" }));
    await vi.waitFor(() => expect(refreshApprovals).toHaveBeenCalled());
    expect(posted("/api/connector/grants/g1/refuse-all")).toHaveLength(1);
  });
});

describe("an answer to a held command a connected app proposed", () => {
  const approve = proposal({ kind: "approval", decision: "approve", request_id: "req-1", link_id: null, session: null, text: null, target: "approval:req-1" });

  it("is shown on the command's own item, and approving as proposed takes the account's confirmation", async () => {
    serve({ "POST /api/ade/approvals/req-1": () => [200, { state: "approved" }] });
    const { Approvals, refreshApprovals } = await fresh();
    render(<Approvals records={[]} requests={[held()]} proposals={[approve]} onClose={() => {}} onOpen={() => {}} />);
    expect(screen.queryByLabelText("Proposals")).toBeNull();                       // not listed twice
    expect(screen.getByText(/proposes:/).textContent).toMatch(/“Claude” \(its own word\) proposes: approve it once\./);
    fireEvent.click(screen.getByRole("button", { name: "Approve once" }));
    fireEvent.change(await screen.findByLabelText("Your password"), { target: { value: "pw" } });
    expect(posted("/api/ade/approvals/req-1")).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: /Confirm/ }));
    await vi.waitFor(() => expect(refreshApprovals).toHaveBeenCalled());
    expect(posted("/api/ade/approvals/req-1")[0].body).toEqual({ approve: true, proposal: PID, confirmation: "conf-1" });
  });

  it("draws the app's name with what is not shown as itself written out, there and in the confirmation", async () => {
    serve();
    const { Approvals } = await fresh();
    const named = { ...approve, app: `Claude${String.fromCodePoint(0x3164)}` };
    render(<Approvals records={[]} requests={[held()]} proposals={[named]} onClose={() => {}} onOpen={() => {}} />);
    const line = screen.getByText(/proposes:/);
    expect(line.textContent).toContain("“Claude[U+3164]” (its own word) proposes");
    expect(line.querySelector("bdi")?.classList.contains("overflow-hidden")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Approve once" }));
    expect((await screen.findByRole("group", { name: /^Approving this command as/ })).getAttribute("aria-label"))
      .toBe("Approving this command as “Claude[U+3164]” proposed");
  });

  it("denying needs no confirmation, and carries the proposal", async () => {
    serve({ "POST /api/ade/approvals/req-1": () => [200, { state: "denied" }] });
    const { Approvals } = await fresh();
    render(<Approvals records={[]} requests={[held()]} proposals={[approve]} onClose={() => {}} onOpen={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    await vi.waitFor(() => expect(posted("/api/ade/approvals/req-1")).toHaveLength(1));
    expect(posted("/api/ade/approvals/req-1")[0].body).toEqual({ approve: false, proposal: PID });
    expect(calls.some((c) => c.path === "/api/account/confirm")).toBe(false);
  });

  it("approving against a proposed denial takes the confirmation made for the command, as its own Approve does", async () => {
    serve({ "POST /api/ade/approvals/req-1": () => [200, { state: "approved" }] });
    const { Approvals } = await fresh();
    render(<Approvals records={[]} requests={[held()]} proposals={[{ ...approve, decision: "deny" }]} onClose={() => {}} onOpen={() => {}} />);
    expect(screen.getByText(/proposes:/).textContent).toContain("proposes: deny it.");
    fireEvent.click(screen.getByRole("button", { name: "Approve once" }));
    expect(await screen.findByRole("group", { name: "Approving this command" })).toBeTruthy();
    fireEvent.change(await screen.findByLabelText("Your password"), { target: { value: "pw" } });
    expect(posted("/api/ade/approvals/req-1")).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: /Confirm/ }));
    await vi.waitFor(() => expect(posted("/api/ade/approvals/req-1")).toHaveLength(1));
    expect(posted("/api/account/confirm")[0].body).toEqual({ password: "pw", change: "request-approve:req-1" });
    expect(posted("/api/ade/approvals/req-1")[0].body).toEqual({ approve: true, proposal: PID, confirmation: "conf-1" });
  });

  it("a held command's own Approve takes a confirmation made for it; its Deny the sign-in alone", async () => {
    serve({ "POST /api/ade/approvals/req-1": () => [200, { state: "approved" }] });
    const { Approvals, refreshApprovals } = await fresh();
    render(<Approvals records={[]} requests={[held({ token_name: "Mac session", session: "s1" })]} proposals={[]} onClose={() => {}} onOpen={() => {}} />);
    // the card says which session asked (HeldFrom.test.tsx)
    expect(screen.getByText(/^A held command/).textContent).toBe("A held command from “Mac session”; its hook says session s1, folder /work");
    fireEvent.click(screen.getByRole("button", { name: "Approve once" }));
    expect(await screen.findByRole("group", { name: "Approving this command" })).toBeTruthy();
    fireEvent.change(await screen.findByLabelText("Your password"), { target: { value: "pw" } });
    expect(posted("/api/ade/approvals/req-1")).toEqual([]);                         // nothing approved before the word
    fireEvent.click(screen.getByRole("button", { name: /Confirm/ }));
    await vi.waitFor(() => expect(refreshApprovals).toHaveBeenCalled());
    expect(posted("/api/account/confirm")[0].body).toEqual({ password: "pw", change: "request-approve:req-1" });
    expect(posted("/api/ade/approvals/req-1")[0].body).toEqual({ approve: true, confirmation: "conf-1" });
  });

  it("drops an Approve prompt once an app's proposal takes the command's place, and never shows it again later", async () => {
    serve();
    const { Approvals } = await fresh();
    const props = { records: [], onClose: () => {}, onOpen: () => {} };
    const { rerender } = render(<Approvals {...props} requests={[held()]} proposals={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve once" }));
    expect(await screen.findByRole("group", { name: "Approving this command" })).toBeTruthy();
    rerender(<Approvals {...props} requests={[held()]} proposals={[approve]} />);           // a proposal arrives
    expect(screen.queryByRole("group", { name: "Approving this command" })).toBeNull();
    rerender(<Approvals {...props} requests={[held()]} proposals={[]} />);                  // and is gone again
    await act(async () => {});
    expect(screen.queryByRole("group", { name: "Approving this command" })).toBeNull();
    expect(screen.getByRole("button", { name: "Approve once" })).toBeTruthy();
  });

  it("moves the keyboard into an Approve prompt and says it, on the command's own Approve and on a proposal's", async () => {
    for (const proposals of [[], [approve]]) {
      serve();
      const { Approvals } = await fresh();
      render(<Approvals records={[]} requests={[held()]} proposals={proposals} onClose={() => {}} onOpen={() => {}} />);
      fireEvent.click(screen.getByRole("button", { name: "Approve once" }));
      const prompt = await screen.findByRole("group", { name: /^Approving this command/ });
      await vi.waitFor(() => expect(prompt.contains(document.activeElement)).toBe(true));
      expect(document.activeElement).toBe(within(prompt).getByLabelText("Your password"));
      await vi.waitFor(() => expect(screen.getAllByRole("status").some((s) => s.textContent === "Approving this command: confirm it to approve it once.")).toBe(true));
      cleanup();
    }
  });

  it("drops a proposal's Approve prompt once another proposal takes its place on the command", async () => {
    serve();
    const { Approvals } = await fresh();
    const props = { records: [], requests: [held()], onClose: () => {}, onOpen: () => {} };
    const { rerender } = render(<Approvals {...props} proposals={[approve]} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve once" }));
    expect(await screen.findByRole("group", { name: /^Approving this command as/ })).toBeTruthy();
    const other = { ...approve, id: "f".repeat(32), decision: "deny" as const, app: "Another app" };
    rerender(<Approvals {...props} proposals={[other]} />);                              // withdrawn, another made
    expect(screen.queryByRole("group", { name: /^Approving this command/ })).toBeNull();
    expect(screen.getByText(/proposes:/).textContent).toContain("proposes: deny it.");
  });

  it("a held command's own Deny needs no confirmation, and Cancel leaves the command waiting", async () => {
    serve({ "POST /api/ade/approvals/req-1": () => [200, { state: "denied" }] });
    const { Approvals } = await fresh();
    render(<Approvals records={[]} requests={[held()]} proposals={[]} onClose={() => {}} onOpen={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve once" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: "Approving this command" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    await vi.waitFor(() => expect(posted("/api/ade/approvals/req-1")).toHaveLength(1));
    expect(posted("/api/ade/approvals/req-1")[0].body).toEqual({ approve: false });
    expect(calls.some((c) => c.path === "/api/account/confirm" && c.method === "POST")).toBe(false);
  });

  it("a command without one keeps its own buttons, and the panel shows the others first", async () => {
    serve();
    const { Approvals } = await fresh();
    render(<Approvals records={[]} requests={[held()]} proposals={[proposal({ kind: "stop" })]} onClose={() => {}} onOpen={() => {}} />);
    const panel = screen.getByLabelText("Approvals");
    const section = within(panel).getByLabelText("Proposals");
    expect(section.textContent).toContain("A stop of mac");
    expect(screen.queryByText(/proposes:/)).toBeNull();
    expect(screen.queryByText("Nothing is waiting for you.")).toBeNull();
    const order = [...panel.querySelectorAll("[aria-label='Proposals'], pre")].map((e) => e.getAttribute("aria-label") ?? "pre");
    expect(order[0]).toBe("Proposals");
  });
});

describe("the store's proposals", () => {
  it("are read with the approvals, and not asked again once the deployment has no connector", async () => {
    let off = false;
    serve({
      "GET /api/ade/approvals": () => [200, { requests: [] }],
      "GET /api/connector/proposals": () => (off ? [404, { detail: "Not Found" }] : [200, { proposals: [proposal()] }]),
    });
    useStore.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true } });
    await useStore.getState().refreshApprovals();
    expect(useStore.getState().proposals.map((p) => p.id)).toEqual([PID]);
    off = true;
    await useStore.getState().refreshApprovals();
    expect(useStore.getState().proposals).toEqual([]);
    expect(useStore.getState().proposalsOff).toBe(true);
    const asked = calls.filter((c) => c.path === "/api/connector/proposals").length;
    await useStore.getState().refreshApprovals();
    expect(calls.filter((c) => c.path === "/api/connector/proposals").length).toBe(asked);
    useStore.getState().signOut();
    expect(useStore.getState().proposalsOff).toBe(false);
    expect(useStore.getState().proposals).toEqual([]);
  });
});
