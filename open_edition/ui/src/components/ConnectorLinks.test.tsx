// The sessions linked through a connected app, as Linked sessions lists them (ConnectorLinks.tsx): each under the name
// its own call gave, with the app that carried it, what it says it is doing and what it asks. Of the owner's they take
// only requests that carry no words (a question dismissed, a request to stop, a removal), so nothing here is typed.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConnectorLink, ConnectorLinkPost, LinkView } from "../api";
import { useStore } from "../store";
import { clock, dayTime } from "../when";
import { Channel } from "./Channel";
import { ANSWER_IN_CHAT, ConnectorLinks, DISMISS_SAID, NOT_READ, REMOVE_SAID, STOP_ASKS, useConnectorLinks, WHICH_LINK } from "./ConnectorLinks";

type Answer = [number, unknown];
type Route = () => Answer | Promise<Answer>;
/** A request as it went out: its body as fetch was handed it (undefined for none), and whether it said it carried one. */
type Call = { path: string; method: string; body: unknown; typed: boolean };
let calls: Call[] = [];

/** The server as the test has it answer: each "METHOD /path" its route; anything else 404. */
function serve(routes: Record<string, Route>) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    calls.push({ path: String(url), method, body: init.body, typed: "Content-Type" in ((init.headers ?? {}) as Record<string, string>) });
    const route = routes[`${method} ${url}`];
    const [status, data] = route ? await route() : [404, { detail: `no route for ${method} ${url}` }];
    return new Response(JSON.stringify(data), { status });
  }));
}

// the times a case reads as "ago" are whole hours back, so a slow run reads them the same
const T = Math.floor(Date.now() / 1000);
const L = "c".repeat(32);
const LISTED = "/api/connector/links";
const SECTION = "Linked through a connected app";

const post = (over: Partial<ConnectorLinkPost> = {}): ConnectorLinkPost => ({
  seq: 7, at: T - 300, kind: "step", text: "running the tests", ref: null, closed_at: null, closed_by: null, ...over,
});
const session = (over: Partial<ConnectorLink> = {}): ConnectorLink => ({
  id: L, name: "Billing fix 2", named_by: "session", app: { id: "g1", name: "Claude" }, linked_at: T - 5 * 3600, heard_at: T - 2 * 3600,
  checkpoint: null, checkpoint_version: 0, checkpoint_at: null, posts: 0, last: null, questions: [], stop_asked_at: null, ...over,
});
const listed = (...links: ConnectorLink[]): Route => () => [200, { links, server_time: T }];
/** How many times the list was read. */
const reads = () => calls.filter((c) => c.method === "GET" && c.path === LISTED).length;
/** What is under way is answered and drawn. */
const settled = () => act(async () => { await new Promise((r) => setTimeout(r, 20)); });

/** The section as the panel mounts it: read by the hook, and read again when `dep` is another. */
function Listed({ dep = 0 }: { dep?: unknown }) {
  const via = useConnectorLinks(dep);
  return <div data-testid="listed"><ConnectorLinks links={via.links} failed={via.failed} reload={via.reload} /></div>;
}
const card = (name = "Billing fix 2") => screen.findByLabelText(`Linked through an app: ${name}`);
/** Press a card's button of that name, once it is there and can be pressed: a card's buttons wait while a request of
 *  its own is under way. */
async function press(c: HTMLElement, name: string) {
  await waitFor(() => expect((within(c).getByRole("button", { name }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(within(c).getByRole("button", { name }));
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  useStore.setState(useStore.getInitialState());
});

describe("a session linked through a connected app", () => {
  it("shows under the name its call gave, as its own words, with the app that carried it, when it linked and when it was last heard", async () => {
    serve({ [`GET ${LISTED}`]: listed(session()) });
    render(<Listed />);
    const c = await card();
    expect(c.textContent).toContain("“Billing fix 2” (its own words) · through “Claude” (its own word)");
    expect(c.textContent).toContain("Linked 5h ago. Last heard 2h ago: it is heard only when it calls.");
    // nothing it has not said: no checkpoint, no post, no question
    expect(within(c).queryByRole("group", { name: "Its checkpoint" })).toBeNull();
    expect(c.textContent).not.toContain("Latest");
    expect(c.textContent).not.toContain("It asks you");
    cleanup();
    serve({ [`GET ${LISTED}`]: listed(session({ heard_at: null })) });
    render(<Listed />);
    expect((await card()).textContent).toContain("Linked 5h ago. Not heard yet: it is heard only when it calls.");
  });

  it("draws a name, an app's name, a checkpoint, a post and a question with what is not shown as itself written out", async () => {
    const filler = String.fromCodePoint(0x3164);      // drawn as nothing
    const turned = String.fromCodePoint(0x202e);      // turns what follows it around
    const words = `done${turned}gnihton`;
    serve({
      [`GET ${LISTED}`]: listed(session({
        name: `one${filler}`, app: { id: "g1", name: `Claude${turned}` },
        checkpoint: { doing: `the fix${turned}`, next: [`tests${filler}`], waiting_on: `you${turned}` }, checkpoint_version: 1, checkpoint_at: T - 60,
        posts: 1, last: post({ text: words }), questions: [{ seq: 12, at: T - 600, text: `Deploy${filler}?` }],
      })),
      [`GET ${LISTED}/${L}/posts`]: () => [200, { posts: [post({ text: words })], before: null }],
    });
    render(<Listed />);
    const c = await screen.findByLabelText(/^Linked through an app: one/);
    fireEvent.click(within(c).getByRole("button", { name: "Its posts" }));
    const list = await within(c).findByLabelText("Its posts");
    const names = [...c.querySelectorAll("bdi")];
    expect(names.map((n) => n.textContent)).toEqual(["one[U+3164]", "Claude[U+202E]"]);
    for (const n of names) expect(n.classList.contains("overflow-hidden")).toBe(true);        // clipped to its line
    for (const written of ["the fix[U+202E]", "tests[U+3164]", "you[U+202E]", "Deploy[U+3164]?"]) expect(c.textContent).toContain(written);
    expect(within(c).getByLabelText("Latest").textContent).toContain("done[U+202E]gnihton");
    expect(list.textContent).toContain("step: done[U+202E]gnihton");
    // and nowhere in what the card draws as it came
    expect(c.textContent).not.toContain(filler);
    expect(c.textContent).not.toContain(turned);
  });

  it("shows what its checkpoint says it is doing, what is next and what it waits on", async () => {
    const cp = async (checkpoint: Record<string, unknown>) => {
      cleanup();
      serve({ [`GET ${LISTED}`]: listed(session({ checkpoint, checkpoint_version: 2, checkpoint_at: T - 4 * 3600 })) });
      render(<Listed />);
      return within(await card()).getByRole("group", { name: "Its checkpoint" });
    };
    const said = (box: HTMLElement, label: string) => within(box).getByText(label).nextElementSibling as HTMLElement;
    let box = await cp({ doing: "the billing fix", next: ["tests", "a pull request"], waiting_on: "your word on the region", notes: { n: 1 } });
    expect(said(box, "Doing").textContent).toBe("the billing fix");
    expect(within(said(box, "Next")).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["tests", "a pull request"]);
    expect(said(box, "Waiting on").textContent).toBe("your word on the region");
    expect(box.textContent).toContain("checkpoint version 2, 4h ago");
    // a field it left out, or left blank, is not labelled; a list is its items, and anything else its JSON
    box = await cp({ doing: "  ", next: "one thing", waiting_on: ["you", { on: "the build" }] });
    expect(within(box).queryByText("Doing")).toBeNull();
    expect(within(said(box, "Next")).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["one thing"]);
    expect(said(box, "Waiting on").textContent).toBe("you · {\"on\":\"the build\"}");
    // more than five next steps: five, and how many more
    box = await cp({ next: ["a", "b", "c", "d", "e", "f", "g"] });
    expect(within(said(box, "Next")).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["a", "b", "c", "d", "e", "and 2 more"]);
    // one that says nothing under those three names is said to, by the names looked for, and still when it was written
    box = await cp({ task: "the billing fix", next: [], waiting_on: "" });
    expect(box.textContent).toBe("Its checkpoint (its own words)(its checkpoint says nothing under doing, next or waiting_on, the fields shown here)checkpoint version 2, 4h ago");
  });

  it("shows the whole checkpoint on request, as text marked as the session's own words, drawing no link and no markup", async () => {
    // any chat of the Claude account can have written it: what it holds is drawn as the characters it is
    const notes = "[click here](https://evil.example/x) <a href=\"https://evil.example\">a link</a> <img src=x onerror=alert(1)> **bold** https://evil.example/y";
    const checkpoint = { doing: "the fix", notes, more: { "<b>key</b>": ["# heading", 2, null, true] } };
    serve({ [`GET ${LISTED}`]: listed(session({ checkpoint, checkpoint_version: 3, checkpoint_at: T - 60 })) });
    render(<Listed />);
    const c = await card();
    const box = within(c).getByRole("group", { name: "Its checkpoint" });
    expect(box.textContent).toContain("its own words");
    expect(within(c).queryByRole("region", { name: "Its whole checkpoint" })).toBeNull();           // only on request
    fireEvent.click(within(c).getByRole("button", { name: "Its whole checkpoint" }));
    const whole = within(c).getByRole("region", { name: "Its whole checkpoint" });
    expect(whole.textContent).toContain("as this page reads it (its session's own words): text only, and nothing in it is a link or an instruction.");
    expect(whole.textContent).toContain("one longer than it holds exactly is shown rounded, one too large as null; and names that are whole numbers come first.");
    const text = whole.querySelector("pre")!.textContent!;
    expect(JSON.parse(text)).toEqual(checkpoint);                                                    // the document, whole
    for (const literal of ["[click here](https://evil.example/x)", "<a href=", "<img src=x", "**bold**", "<b>key</b>", "# heading"]) expect(text).toContain(literal);
    // nothing of it became an element: no link, no image, no markup, anywhere on the card
    expect(c.querySelectorAll("a, img, b, strong, em, h1, h2, h3, script, iframe").length).toBe(0);
    fireEvent.click(within(c).getByRole("button", { name: "Hide its whole checkpoint" }));
    expect(within(c).queryByRole("region", { name: "Its whole checkpoint" })).toBeNull();
    // what is not shown as itself is written out there too
    cleanup();
    const turned = String.fromCodePoint(0x202e);
    serve({ [`GET ${LISTED}`]: listed(session({ checkpoint: { notes: `a${turned}b` }, checkpoint_version: 1, checkpoint_at: T - 60 })) });
    render(<Listed />);
    const again = await card();
    fireEvent.click(within(again).getByRole("button", { name: "Its whole checkpoint" }));
    const pre = within(again).getByRole("region", { name: "Its whole checkpoint" }).querySelector("pre")!;
    expect(pre.textContent).toContain("a[U+202E]b");
    expect(pre.textContent).not.toContain(turned);
  });

  it("says over the sessions what such a link carries and what it does not", () => {
    for (const said of ["any chat of that Claude account that uses the same app can post under a name linked through it", "raise no badge and no notice", "carries none of your words",
      "a stop is a request in fixed words, asked with your sign-in alone and not signed, and it only stops", "never given back to them"]) {
      expect(WHICH_LINK).toContain(said);
    }
    expect(WHICH_LINK).not.toContain("Nothing you write here reaches");
    expect(DISMISS_SAID).toBe("Dismissing closes the question here; it carries no answer. Its session is told that it was closed, in fixed words, if it calls link_session within a day.");
    expect(STOP_ASKS).toBe("It reads the request in the answer to its next call, taken or refused, in fixed words: nothing here can hold it.");
  });

  it("shows its newest post by its kind and when, its line breaks kept", async () => {
    serve({ [`GET ${LISTED}`]: listed(session({ posts: 4, last: post({ kind: "done", at: T - 3 * 3600, text: "merged:\n  two files" }) })) });
    render(<Listed />);
    const latest = within(await card()).getByLabelText("Latest");
    expect(latest.textContent).toBe("Latest · done · 3h agomerged:\n  two files");
    expect(within(latest).getByText(/merged:/).className).toContain("whitespace-pre-wrap");
  });

  it("shows each open question with where to answer it, and only dismisses it: no Answer, and no field to type in", async () => {
    let open = [12, 13];
    const asked = [{ seq: 12, at: T - 3 * 3600, text: "Which branch?\nmain or release?" }, { seq: 13, at: T - 3 * 3600, text: "And which region?" }];
    serve({
      [`GET ${LISTED}`]: () => [200, { links: [session({ questions: asked.filter((q) => open.includes(q.seq)) })], server_time: T }],
      [`POST ${LISTED}/${L}/questions/13/close`]: () => { open = [12]; return [200, { seq: 13, closed_at: T }]; },
    });
    render(<Listed />);
    const c = await card();
    expect(c.textContent).toContain("It asks you · 3h ago");
    const first = within(c).getByText(/Which branch\?/);
    expect(first.textContent).toBe("Which branch?\nmain or release?");
    expect(first.className).toContain("whitespace-pre-wrap");
    expect(within(c).getAllByText(ANSWER_IN_CHAT)).toHaveLength(2);                 // under each of the two
    expect(within(c).queryByRole("button", { name: /answer/i })).toBeNull();
    expect(c.querySelector("textarea, input, select, [contenteditable]")).toBeNull();
    const dismiss = within(c).getAllByRole("button", { name: "Dismiss question" });
    expect(dismiss).toHaveLength(2);
    for (const b of dismiss) expect(document.getElementById(b.getAttribute("aria-describedby") ?? "")?.textContent).toBe(DISMISS_SAID);
    fireEvent.click(dismiss[1]);                                                  // the second question's
    await waitFor(() => expect(within(c).queryByText("And which region?")).toBeNull());
    expect(calls.filter((x) => x.method === "POST")).toEqual([{ path: `${LISTED}/${L}/questions/13/close`, method: "POST", body: undefined, typed: false }]);
    expect(reads()).toBe(2);                                                    // the list read again
    expect(within(c).getByText(/Which branch\?/)).toBeTruthy();                    // the other is open still
    expect(within(c).getAllByText(ANSWER_IN_CHAT)).toHaveLength(1);
  });

  it("asks it to stop in fixed words, says when it was asked, and withdraws the request", async () => {
    let asked: number | null = null;
    serve({
      [`GET ${LISTED}`]: () => [200, { links: [session({ stop_asked_at: asked })], server_time: T }],
      [`POST ${LISTED}/${L}/stop`]: () => { asked = T - 5; return [200, { stop_asked_at: asked }]; },
      [`DELETE ${LISTED}/${L}/stop`]: () => { asked = null; return [200, { stop_asked_at: null }]; },
    });
    render(<Listed />);
    const c = await card();
    const stop = within(c).getByRole("button", { name: "Ask it to stop" });
    expect(document.getElementById(stop.getAttribute("aria-describedby") ?? "")?.textContent).toBe(STOP_ASKS);
    fireEvent.click(stop);
    expect(await within(c).findByText(`Asked to stop ${clock(T - 5)}: it reads that in the answer to its next call, and is told to go on only on your word in its own chat.`)).toBeTruthy();
    expect(within(c).queryByRole("button", { name: "Ask it to stop" })).toBeNull();
    await press(c, "Withdraw the request");
    expect(await within(c).findByRole("button", { name: "Ask it to stop" })).toBeTruthy();
    expect(c.textContent).not.toContain("Asked to stop");
    expect(calls.filter((x) => x.path === `${LISTED}/${L}/stop`).map((x) => [x.method, x.body])).toEqual([["POST", undefined], ["DELETE", undefined]]);
    expect(reads()).toBe(3);
    // a request that has stood for days says its day too: its clock alone would read as today's
    cleanup();
    serve({ [`GET ${LISTED}`]: listed(session({ stop_asked_at: T - 3 * 86400 })) });
    render(<Listed />);
    expect((await card()).textContent).toContain(`Asked to stop ${dayTime(T - 3 * 86400)}: it reads that in the answer to its next call`);
  });

  it("is removed with two clicks, which says what goes with it", async () => {
    let there = true;
    serve({
      [`GET ${LISTED}`]: () => [200, { links: there ? [session()] : [], server_time: T }],
      [`DELETE ${LISTED}/${L}`]: () => { there = false; return [200, { removed: L }]; },
    });
    render(<Listed />);
    const c = await card();
    expect(within(c).getByText(REMOVE_SAID)).toBeTruthy();
    fireEvent.click(within(c).getByRole("button", { name: "Remove" }));
    expect(calls.filter((x) => x.method === "DELETE")).toEqual([]);                // one click arms it only
    fireEvent.click(within(c).getByRole("button", { name: "Click again to remove it" }));
    await waitFor(() => expect(screen.queryByLabelText("Linked through an app: Billing fix 2")).toBeNull());
    expect(calls.filter((x) => x.method === "DELETE")).toEqual([{ path: `${LISTED}/${L}`, method: "DELETE", body: undefined, typed: false }]);
    expect(screen.queryByRole("region", { name: SECTION })).toBeNull();            // the last one gone, the section goes
  });

  it("lists its posts when asked, newest first, a page at a time, each on one line", async () => {
    serve({
      [`GET ${LISTED}`]: listed(session({ posts: 3, last: post({ seq: 9, at: T - 60, kind: "done", text: "merged" }) })),
      [`GET ${LISTED}/${L}/posts`]: () => [200, {
        posts: [post({ seq: 9, at: T - 60, kind: "done", text: "merged" }), post({ seq: 8, at: T - 120, kind: "question", text: "Which branch?" })], before: 8 }],
      [`GET ${LISTED}/${L}/posts?before=8`]: () => [200, { posts: [post({ seq: 7, at: T - 300, kind: "step", text: "two\nlines" })], before: null }],
    });
    render(<Listed />);
    const c = await card();
    expect(calls.map((x) => x.path)).toEqual([LISTED]);                           // not read before it is asked for
    fireEvent.click(within(c).getByRole("button", { name: "Its posts" }));
    const list = await within(c).findByLabelText("Its posts");
    const lines = () => within(list).getAllByRole("listitem").map((li) => li.textContent);
    expect(lines()).toEqual([`${dayTime(T - 60)} done: merged`, `${dayTime(T - 120)} question: Which branch?`]);
    fireEvent.click(within(c).getByRole("button", { name: "More" }));
    // a post's line break is written out, so one post cannot pass for two
    await waitFor(() => expect(lines()).toEqual([`${dayTime(T - 60)} done: merged`, `${dayTime(T - 120)} question: Which branch?`,
      `${dayTime(T - 300)} step: two\\nlines`]));
    expect(within(c).queryByRole("button", { name: "More" })).toBeNull();          // no page before that one
    fireEvent.click(within(c).getByRole("button", { name: "Hide its posts" }));
    expect(within(c).queryByLabelText("Its posts")).toBeNull();
    expect(within(c).getByRole("button", { name: "Its posts" })).toBeTruthy();
    // one that has posted nothing
    cleanup();
    serve({ [`GET ${LISTED}`]: listed(session()), [`GET ${LISTED}/${L}/posts`]: () => [200, { posts: [], before: null }] });
    render(<Listed />);
    fireEvent.click(within(await card()).getByRole("button", { name: "Its posts" }));
    expect(await screen.findByText("It has posted nothing yet.")).toBeTruthy();
  });

  it("reads one page of its posts at a time, and an older page that comes after they were put away does not open them again", async () => {
    let older: (a: Answer) => void = () => {};
    serve({
      [`GET ${LISTED}`]: listed(session({ posts: 3, last: post({ seq: 9 }) })),
      [`GET ${LISTED}/${L}/posts`]: () => [200, { posts: [post({ seq: 9 }), post({ seq: 8 })], before: 8 }],
      [`GET ${LISTED}/${L}/posts?before=8`]: () => new Promise<Answer>((r) => { older = r; }),
    });
    render(<Listed />);
    const c = await card();
    fireEvent.click(within(c).getByRole("button", { name: "Its posts" }));
    const more = await within(c).findByRole("button", { name: "More" }) as HTMLButtonElement;
    fireEvent.click(more);
    expect(more.disabled).toBe(true);
    fireEvent.click(more);                                                       // pressed again before its page came
    fireEvent.click(within(c).getByRole("button", { name: "Hide its posts" }));
    older([200, { posts: [post({ seq: 7 })], before: null }]);
    await settled();
    expect(calls.filter((x) => x.path === `${LISTED}/${L}/posts?before=8`)).toHaveLength(1);
    expect(within(c).queryByLabelText("Its posts")).toBeNull();
    // opened again, they are read from the newest
    fireEvent.click(within(c).getByRole("button", { name: "Its posts" }));
    expect(within(await within(c).findByLabelText("Its posts")).getAllByRole("listitem")).toHaveLength(2);
  });

  it("says once what such a link is, over however many sessions, and renders nothing with none", async () => {
    serve({ [`GET ${LISTED}`]: listed(session(), session({ id: "d".repeat(32), name: "docs" })) });
    render(<Listed />);
    const section = await screen.findByRole("region", { name: SECTION });
    expect(within(section).getByRole("heading", { name: SECTION })).toBeTruthy();
    expect(within(section).getAllByRole("note").map((n) => n.textContent)).toEqual([WHICH_LINK]);
    expect(screen.getAllByText(WHICH_LINK)).toHaveLength(1);
    expect(within(section).getAllByLabelText(/^Linked through an app: /).map((el) => el.getAttribute("aria-label")))
      .toEqual(["Linked through an app: Billing fix 2", "Linked through an app: docs"]);
    cleanup();
    const none = render(<ConnectorLinks links={[]} reload={async () => {}} />);
    expect(none.container.firstChild).toBeNull();
  });

  it("sends none of the owner's words: no request it makes carries a body, and it has no field to type in", async () => {
    let s: ConnectorLink | null = session({ questions: [{ seq: 12, at: T - 600, text: "Which branch?" }], posts: 3, last: post() });
    serve({
      [`GET ${LISTED}`]: () => [200, { links: s ? [s] : [], server_time: T }],
      [`POST ${LISTED}/${L}/questions/12/close`]: () => { s = { ...s!, questions: [] }; return [200, { seq: 12, closed_at: T }]; },
      [`POST ${LISTED}/${L}/stop`]: () => { s = { ...s!, stop_asked_at: T }; return [200, { stop_asked_at: T }]; },
      [`DELETE ${LISTED}/${L}/stop`]: () => { s = { ...s!, stop_asked_at: null }; return [200, { stop_asked_at: null }]; },
      [`GET ${LISTED}/${L}/posts`]: () => [200, { posts: [post({ seq: 9 }), post({ seq: 8 })], before: 8 }],
      [`GET ${LISTED}/${L}/posts?before=8`]: () => [200, { posts: [post({ seq: 7 })], before: null }],
      [`DELETE ${LISTED}/${L}`]: () => { s = null; return [200, { removed: L }]; },
    });
    render(<Listed />);
    const section = await screen.findByRole("region", { name: SECTION });
    const c = await card();
    const typed = () => section.querySelector("textarea, input, select, [contenteditable]");
    expect(typed()).toBeNull();
    await press(c, "Dismiss question");
    await waitFor(() => expect(within(c).queryByRole("button", { name: "Dismiss question" })).toBeNull());
    await press(c, "Ask it to stop");
    await press(c, "Withdraw the request");
    await within(c).findByRole("button", { name: "Ask it to stop" });
    await press(c, "Its posts");
    await press(c, "More");
    await waitFor(() => expect(within(c).queryByRole("button", { name: "More" })).toBeNull());
    expect(typed()).toBeNull();
    await press(c, "Remove");
    await press(c, "Click again to remove it");
    await waitFor(() => expect(screen.queryByRole("region", { name: SECTION })).toBeNull());
    // every request the card can make went out, and each with nothing in it
    expect([...new Set(calls.map((x) => `${x.method} ${x.path}`))].sort()).toEqual([
      `DELETE ${LISTED}/${L}`, `DELETE ${LISTED}/${L}/stop`, `GET ${LISTED}`, `GET ${LISTED}/${L}/posts`, `GET ${LISTED}/${L}/posts?before=8`,
      `POST ${LISTED}/${L}/questions/12/close`, `POST ${LISTED}/${L}/stop`]);
    for (const x of calls) expect([x.method, x.path, x.body, x.typed]).toEqual([x.method, x.path, undefined, false]);
  });

  it("sends a request of its card's once: its buttons wait while one is under way", async () => {
    let answer: (a: Answer) => void = () => {};
    let open = true;
    serve({
      [`GET ${LISTED}`]: () => [200, { links: [session({ questions: open ? [{ seq: 12, at: T - 600, text: "Which branch?" }] : [] })], server_time: T }],
      [`POST ${LISTED}/${L}/questions/12/close`]: () => new Promise<Answer>((r) => { answer = (a) => { open = false; r(a); }; }),
    });
    render(<Listed />);
    const c = await card();
    const waits = (name: string) => (within(c).getByRole("button", { name }) as HTMLButtonElement).disabled;
    fireEvent.click(within(c).getByRole("button", { name: "Dismiss question" }));
    expect(["Dismiss question", "Ask it to stop", "Remove"].map(waits)).toEqual([true, true, true]);
    fireEvent.click(within(c).getByRole("button", { name: "Dismiss question" }));    // pressed again before the answer
    answer([200, { seq: 12, closed_at: T }]);
    await waitFor(() => expect(within(c).queryByText("Which branch?")).toBeNull());
    expect(calls.filter((x) => x.method === "POST")).toHaveLength(1);
    await waitFor(() => expect(["Ask it to stop", "Remove"].map(waits)).toEqual([false, false]));
  });

  it("says in its card why a request was not taken, and reads the list again where the server says what was asked of is gone", async () => {
    let refusal: Answer = [500, { detail: "The database is busy: try again." }];
    let now = [session({ questions: [{ seq: 12, at: T - 600, text: "Which branch?" }] })];
    serve({
      [`GET ${LISTED}`]: () => [200, { links: now, server_time: T }],
      [`POST ${LISTED}/${L}/questions/12/close`]: () => refusal,
      [`POST ${LISTED}/${L}/stop`]: () => [404, { detail: "No such session." }],
    });
    render(<Listed />);
    const c = await card();
    // a refusal that says nothing of what is listed: said in the card, and the list left as it was read
    await press(c, "Dismiss question");
    expect((await within(c).findByRole("alert")).textContent).toBe("The database is busy: try again.");
    await settled();
    expect(reads()).toBe(1);
    expect(within(c).getByText("Which branch?")).toBeTruthy();
    // no such open question, its session having closed it meanwhile: said, and the list read again, so it goes
    refusal = [404, { detail: "No open question of that session has that number." }];
    now = [session()];
    await press(c, "Dismiss question");
    await waitFor(() => expect(within(c).queryByText("Which branch?")).toBeNull());
    expect(within(c).getByRole("alert").textContent).toBe("No open question of that session has that number.");
    expect(reads()).toBe(2);
    // no such session, it having ended meanwhile: the list read again, and its card goes with it
    now = [];
    await press(c, "Ask it to stop");
    await waitFor(() => expect(screen.queryByRole("region", { name: SECTION })).toBeNull());
    expect(reads()).toBe(3);
  });

  it("says when its posts cannot be read, until a request of its card's is taken, and goes once the server knows no such session", async () => {
    let there = true;
    let asked: number | null = null;
    serve({
      [`GET ${LISTED}`]: () => [200, { links: there ? [session({ stop_asked_at: asked })] : [], server_time: T }],
      [`GET ${LISTED}/${L}/posts`]: () => (there ? [500, { detail: "The database is busy: try again." }] : [404, { detail: "No such session." }]),
      [`POST ${LISTED}/${L}/stop`]: () => { asked = T; return [200, { stop_asked_at: T }]; },
    });
    render(<Listed />);
    const c = await card();
    await press(c, "Its posts");
    expect((await within(c).findByRole("alert")).textContent).toBe("The database is busy: try again.");
    expect(within(c).queryByLabelText("Its posts")).toBeNull();
    await settled();
    expect(reads()).toBe(1);
    // a request of the card's that is taken: what the last one said is no longer said
    await press(c, "Ask it to stop");
    await within(c).findByRole("button", { name: "Withdraw the request" });
    expect(within(c).queryByRole("alert")).toBeNull();
    expect(reads()).toBe(2);
    there = false;
    await press(c, "Its posts");
    await waitFor(() => expect(screen.queryByRole("region", { name: SECTION })).toBeNull());
    expect(reads()).toBe(3);
  });
});

describe("reading the sessions linked through a connected app", () => {
  it("reads them once as it mounts and again when what it is given changes; shows none where the connector is off, and says so where the read failed", async () => {
    let answer: Answer = [200, { links: [session()], server_time: T }];
    serve({ [`GET ${LISTED}`]: () => answer });
    const { rerender } = render(<Listed dep={1} />);
    await card();
    expect(reads()).toBe(1);
    rerender(<Listed dep={1} />);                                                // drawn again with the same: not read again
    await settled();
    expect(reads()).toBe(1);
    answer = [200, { links: [session(), session({ id: "d".repeat(32), name: "docs" })], server_time: T }];
    rerender(<Listed dep={2} />);
    await card("docs");
    expect(reads()).toBe(2);
    // the deployment's connector off (404): none are shown, and nothing is said
    answer = [404, { detail: "Not Found" }];
    rerender(<Listed dep="off" />);
    await waitFor(() => expect(screen.queryByRole("region", { name: SECTION })).toBeNull());
    expect(screen.getByTestId("listed").childElementCount).toBe(0);
    // a read that failed, or answered what is no list, is not a list of none: what was last read stays, and it says so
    for (const failed of [[500, { detail: "Internal Server Error" }], [200, {}]] as Answer[]) {
      answer = [200, { links: [session()], server_time: T }];
      rerender(<Listed dep={`${failed[0]} shown`} />);
      await card();
      expect(screen.queryByText(NOT_READ)).toBeNull();
      answer = failed;
      rerender(<Listed dep={`${failed[0]} failed`} />);
      expect((await screen.findByRole("alert")).textContent).toBe(NOT_READ);
      expect(await card()).toBeTruthy();                                         // as it was last read
    }
    // and where nothing was read yet, it still says the read failed rather than drawing nothing
    cleanup();
    serve({ [`GET ${LISTED}`]: () => [500, { detail: "Internal Server Error" }] });
    render(<Listed />);
    const section = await screen.findByRole("region", { name: SECTION });
    expect(within(section).getByRole("alert").textContent).toBe(NOT_READ);
    expect(within(section).queryByLabelText(/^Linked through an app:/)).toBeNull();
  });

  it("keeps the newest read's answer when an older read's comes after it", async () => {
    let release: (a: Answer) => void = () => {};
    let asked = 0;
    serve({
      [`GET ${LISTED}`]: () => (++asked === 1 ? new Promise<Answer>((r) => { release = r; })
        : [200, { links: [session({ name: "the newer" })], server_time: T }]),
    });
    const { rerender } = render(<Listed dep={1} />);
    await waitFor(() => expect(reads()).toBe(1));
    rerender(<Listed dep={2} />);
    await card("the newer");
    release([200, { links: [session({ name: "the older" })], server_time: T }]);
    await settled();
    expect(screen.queryByLabelText("Linked through an app: the older")).toBeNull();
    expect(screen.getByLabelText("Linked through an app: the newer")).toBeTruthy();
  });

  it("starts no timer of its own: an hour on, it has read once", async () => {
    vi.useFakeTimers();
    serve({ [`GET ${LISTED}`]: listed(session()) });
    render(<Listed />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByLabelText("Linked through an app: Billing fix 2")).toBeTruthy();
    expect(reads()).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(3_600_000); });
    expect(reads()).toBe(1);
  });
});

describe("Linked sessions, with a session linked through a connected app", () => {
  const OWNER = { id: "u", email: "o@example.org", can_sign_in_again: true };
  const keyed = (over: Partial<LinkView> = {}): LinkView => ({
    id: "L1", name: "MacBook Pro", kind: "local", session_ref: null, interval_min: 15, created_at: T - 3600,
    last_seen_at: T - 60, last_heartbeat_at: T - 60, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0,
    checkpoint_at: null, events: 0, last_event: null, commands: [], stopped_at: null, ...over,
  });
  /** The panel's server: the links made with a key (`links`), and the sessions linked through an app (`via`: null for
   *  a deployment whose connector is off, which answers 404). */
  const panel = (links: LinkView[], via: (() => ConnectorLink[]) | null) => serve({
    "GET /api/ade/link/sessions": () => [200, { links, questions: [], public_url: "https://pp.example", connect_hold: 180, move_hold: 180 }],
    "GET /api/ade/link/history": () => [200, { sessions: [] }],
    ...(via ? { [`GET ${LISTED}`]: () => [200, { links: via(), server_time: T }] as Answer } : {}),
  });

  it("does not say no session is linked while one is linked through an app, and says it as before with none", async () => {
    useStore.setState({ user: OWNER });
    panel([], () => [session()]);
    render(<Channel onClose={() => {}} />);
    const section = await screen.findByRole("region", { name: SECTION });
    expect(within(section).getByLabelText("Linked through an app: Billing fix 2")).toBeTruthy();
    expect(screen.queryByText(/No session is linked yet/)).toBeNull();
    expect(screen.queryByRole("button", { name: /Link your first session/ })).toBeNull();
    // none linked either way: as it was, whether the connector lists none or is off on this deployment
    for (const via of [() => [], null]) {
      cleanup();
      useStore.setState({ ...useStore.getInitialState(), user: OWNER });
      panel([], via);
      render(<Channel onClose={() => {}} />);
      expect(await screen.findByText(/No session is linked yet/)).toBeTruthy();
      await waitFor(() => expect(reads()).toBeGreaterThan(0));
      await settled();
      expect(screen.getByRole("button", { name: /Link your first session/ })).toBeTruthy();
      expect(screen.queryByRole("region", { name: SECTION })).toBeNull();
    }
  });

  it("says the read failed where it did, beside whatever the panel says of the sessions linked with a key", async () => {
    useStore.setState({ user: OWNER });
    serve({
      "GET /api/ade/link/sessions": () => [200, { links: [], questions: [], public_url: "https://pp.example", connect_hold: 180, move_hold: 180 }],
      "GET /api/ade/link/history": () => [200, { sessions: [] }],
      [`GET ${LISTED}`]: () => [500, { detail: "Internal Server Error" }],
    });
    render(<Channel onClose={() => {}} />);
    const section = await screen.findByRole("region", { name: SECTION });
    expect(within(section).getByRole("alert").textContent).toBe(NOT_READ);
  });

  it("lists them under the sessions linked with a key, and reads them again on Read again", async () => {
    useStore.setState({ user: OWNER });
    let via = [session()];
    panel([keyed()], () => via);
    render(<Channel onClose={() => {}} />);
    const section = await screen.findByRole("region", { name: SECTION });
    const machine = screen.getByRole("region", { name: "A machine: MacBook Pro" });
    expect(machine.compareDocumentPosition(section) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByText(/No session is linked yet/)).toBeNull();
    via = [session(), session({ id: "d".repeat(32), name: "docs" })];
    await settled();
    expect(screen.queryByLabelText("Linked through an app: docs")).toBeNull();     // not before the list is read again
    fireEvent.click(screen.getByLabelText("Read again"));
    expect(await card("docs")).toBeTruthy();
  });
});
