import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RiskTextView } from "../api";
import { useStore } from "../store";
import { closedHere, forgetPage, RiskTerms, settleFirstVisit, untilClosedHere } from "./RiskTerms";

const DAY = 24 * 3600;

// three texts as GET /api/ade/risks gives them; their words are this test's, not the design's
const relayText = (words = "Several of your accounts in turn can go past one account's limits."): RiskTextView => ({
  point: 2, title: "The relay among your accounts",
  design: [{ case: null, text: words }],
  quotations: [{ id: "ordinary-use", case: null, source: "Legal and compliance", section: null, address: "https://docs.example/legal",
                 source_date: null, read_on: "2026-10-10", words: "A clause of the provider's, word for word.", unread: null }],
  unread: false, digest: "a".repeat(64),
});
const crossingText: RiskTextView = {
  point: 1, title: "A crossing between organisations",
  design: [
    { case: "crossing", text: "The relay took this task onto an organisation's seats." },
    { case: "back_home", text: "This task started on {home_group} and goes back there." },
    { case: "onto_seat", text: "The organisation's own settings apply on its seats." },
  ],
  quotations: [
    { id: "responsibility", case: null, source: "Commercial Terms of Service", section: "D.5", address: "https://terms.example/commercial",
      source_date: "Effective June 17, 2025", read_on: "2026-10-10", words: "The customer answers for its account.", unread: null },
    { id: "data-use-consumer", case: null, source: "Consumer Terms of Service", section: null, address: "https://terms.example/consumer",
      source_date: null, read_on: null, words: null, unread: "Not read for this release: whether this work may train models." },
    { id: "seat", case: "onto_seat", source: "Seat terms", section: "4", address: "https://terms.example/seat",
      source_date: null, read_on: "2026-10-11", words: "Words only a seat shows.", unread: null },
  ],
  unread: true, digest: "b".repeat(64),
};
const teammateText: RiskTextView = {
  point: 13, title: "Accepting a teammate's task",
  design: [
    { case: "offer", text: "If you accept, {owner}'s record keeps your user; {operator} holds it." },
    { case: "custody", text: "If you accept custody, {owner}'s custody record names you." },
  ],
  // a section named in words, as the Privacy Act's principles are named
  quotations: [{ id: "ipp-3", case: null, source: "Privacy Act 2020 (New Zealand)", section: "information privacy principle 3",
                 address: "https://legislation.example/act", source_date: null, read_on: null, words: null,
                 unread: "Not read for this release: what the principle asks." }],
  unread: true, digest: "c".repeat(64),
};
// risk point 16's: what the page's own read of an account by itself waits on
const readText: RiskTextView = {
  point: 16, title: "Reading who an account is, by itself",
  design: [{ case: null, text: "This page starts an account's Claude Code once by itself to read who it is." }],
  quotations: [{ id: "automated-use", case: null, source: "Consumer Terms of Service", section: "3", address: "https://terms.example/consumer",
                 source_date: "Effective October 8, 2025", read_on: "2026-10-10", words: "Not through automated means.", unread: null }],
  unread: false, digest: "d".repeat(64),
};
const RELAY = "Terms: The relay among your accounts";
const CROSSING = "Terms: A crossing between organisations";
const READ = "Terms: Reading who an account is, by itself";

let served: RiskTextView[];
let down: boolean;
let calls: { method: string; url: string; body: unknown }[];

beforeEach(() => {
  served = [relayText(), crossingText, teammateText, readText];
  down = false;
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ method: init.method || "GET", url: String(url), body: init.body });
    if (down) return new Response(JSON.stringify({ detail: "The app is starting." }), { status: 503 });
    return new Response(JSON.stringify({ texts: served }), { status: 200 });
  }));
  useStore.setState({ user: { id: "u1", email: "owner@example.org" } });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  forgetPage();
  localStorage.clear();
  useStore.setState(useStore.getInitialState());
});

/** Every call the page made was a read of the texts: nothing posted, nothing of what was shown sent. */
const onlyReadTexts = () => calls.length > 0 && calls.every((c) => c.method === "GET" && c.url === "/api/ade/risks" && c.body === undefined);
/** Whatever the page has under way (a read's answer, the effects it brings) done. */
const settled = () => act(() => new Promise<void>((done) => setTimeout(done, 0)));

/** A form with a risk point's text, as the component that owns a control renders it: beside the control, opened with
 *  the form. */
function Form({ onMake }: { onMake: (title: string) => void }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  return (
    <div className="relative">
      <button onClick={() => setOpen(true)}>New task</button>
      {open && (
        <>
          <input aria-label="Its title" value={title} onChange={(e) => setTitle(e.target.value)} />
          <button onClick={() => onMake(title)}>Make the task</button>
        </>
      )}
      <RiskTerms point={2} kind="control" open={open} />
    </div>
  );
}

describe("the terms at a risk point", () => {
  it("opens a control's text over it each time the control opens, every time", async () => {
    const at = (open: boolean) => <RiskTerms point={2} kind="control" open={open} />;
    const { rerender } = render(at(false));
    expect(screen.queryByRole("dialog")).toBeNull();
    rerender(at(true));
    const terms = await screen.findByRole("dialog", { name: RELAY });
    expect(terms.getAttribute("aria-modal")).toBe("false");
    expect(within(terms).getByText(/Several of your accounts in turn/)).toBeTruthy();
    rerender(at(false));
    expect(screen.queryByRole("dialog")).toBeNull();                      // it closes with the control
    rerender(at(true));
    const again = screen.getByRole("dialog", { name: RELAY });            // and opens with it again
    fireEvent.click(within(again).getByRole("button", { name: "Close the terms" }));
    expect(screen.queryByRole("dialog")).toBeNull();                      // closed by the person this time
    rerender(at(false));
    rerender(at(true));
    expect(screen.getByRole("dialog", { name: RELAY })).toBeTruthy();     // it still opens the next time
    cleanup();
    const { container } = render(at(true));                               // a control open as it mounts: at once
    const mounted = screen.getByRole("dialog", { name: RELAY });
    // layered over the page, and nothing else rendered: no backdrop
    expect(mounted.classList.contains("absolute")).toBe(true);
    expect(Array.from(container.children)).toEqual([mounted]);
  });

  it("lets the control work while its text is open, asks for no press, takes no focus and posts nothing", async () => {
    const made = vi.fn();
    render(<Form onMake={made} />);
    expect(calls).toEqual([]);                                            // a closed control asks the app nothing
    const opener = screen.getByRole("button", { name: "New task" });
    opener.focus();
    fireEvent.click(opener);
    const terms = screen.getByRole("dialog");
    expect(document.activeElement).toBe(opener);                          // opening it moved no focus
    const title = screen.getByRole("textbox", { name: "Its title" });
    title.focus();
    fireEvent.change(title, { target: { value: "Fix the build" } });
    await within(terms).findByText(/Several of your accounts in turn/);
    expect(document.activeElement).toBe(title);                           // nor did its words, when they came
    const make = screen.getByRole("button", { name: "Make the task" });
    expect(make.closest("[inert], [aria-hidden='true']")).toBeNull();
    fireEvent.click(make);
    expect(made).toHaveBeenCalledWith("Fix the build");                   // the control works, its text open
    expect(screen.getByRole("dialog")).toBe(terms);
    // nothing in it to press but its Close: no "I have read this", nothing to tick
    expect(within(terms).getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual(["Close the terms"]);
    expect(within(terms).queryAllByRole("checkbox")).toEqual([]);
    expect(onlyReadTexts()).toBe(true);
  });

  it("reads the texts once for the page and again as a text opens: words changed on the server show the next time, the last ones meanwhile", async () => {
    const at = (open: boolean) => <><RiskTerms point={1} kind="control" open /><RiskTerms point={2} kind="control" open={open} /></>;
    const { rerender } = render(at(true));
    await screen.findByText(/Several of your accounts in turn/);
    expect(calls).toHaveLength(1);                                        // two texts opening together ask once
    rerender(at(false));
    served = [relayText("The words as the server has them now."), crossingText, teammateText];
    rerender(at(true));
    expect(screen.getByText(/Several of your accounts in turn/)).toBeTruthy();   // the page's copy, at once
    await screen.findByText("The words as the server has them now.");     // read again as it opened
    expect(screen.queryByText(/Several of your accounts in turn/)).toBeNull();
    expect(calls).toHaveLength(2);
  });

  it("opens a control's text again, while the control stays open, when a case arrives that it has not shown, every time", async () => {
    // risk point 13 at Accept into...: opened with no agent chosen, the "other_group" part arrives with an agent of
    // another group, after the person may have closed the text
    served = [{ ...teammateText, design: [...teammateText.design, { case: "other_group", text: "This agent is in another group than the task's." },
                                          { case: "adopted_check", text: "A check you adopt runs as your own." }] }];
    const at = (cases: string[], open = true) => <RiskTerms point={13} kind="control" open={open} cases={cases} owner="Ana" />;
    const terms = () => screen.queryByRole("dialog", { name: "Terms: Accepting a teammate's task" });
    const { rerender } = render(at(["offer"]));
    // titled "Risk point 13" until the texts are read
    await screen.findByRole("dialog", { name: "Terms: Accepting a teammate's task" });
    expect(within(terms()!).getByText("If you accept, Ana's record keeps your user; {operator} holds it.")).toBeTruthy();
    expect(within(terms()!).queryByText(/another group/)).toBeNull();
    fireEvent.click(within(terms()!).getByRole("button", { name: "Close the terms" }));
    expect(terms()).toBeNull();
    const reads = calls.length;
    rerender(at(["offer", "other_group"]));                                // an agent of another group chosen
    expect(within(terms()!).getByText("This agent is in another group than the task's.")).toBeTruthy();
    await settled();
    expect(calls.length).toBe(reads + 1);                                  // read again, as an opening is
    fireEvent.click(within(terms()!).getByRole("button", { name: "Close the terms" }));
    rerender(at(["other_group", "offer"]));                                // the same cases in another order: nothing new
    expect(terms()).toBeNull();
    rerender(at(["offer"]));                                               // a part that goes: it stays closed
    expect(terms()).toBeNull();
    rerender(at(["offer", "other_group"]));                                // and comes to apply again: it opens again
    expect(terms()).not.toBeNull();
    expect(within(terms()!).queryByText("A check you adopt runs as your own.")).toBeNull();
    fireEvent.click(within(terms()!).getByRole("button", { name: "Close the terms" }));
    rerender(at(["offer", "other_group", "adopted_check"]));               // a check adopted, the text closed: any case
    expect(within(terms()!).getByText("A check you adopt runs as your own.")).toBeTruthy();   // opens it, not one alone
    rerender(at(["offer", "adopted_check"]));                              // open still, a part going: it stays open
    expect(terms()).not.toBeNull();
    fireEvent.click(within(terms()!).getByRole("button", { name: "Close the terms" }));
    rerender(at(["offer", "other_group"]));                                // one goes as another comes, closed: the
    expect(within(terms()!).getByText("This agent is in another group than the task's.")).toBeTruthy();   // one that came opens it
    fireEvent.click(within(terms()!).getByRole("button", { name: "Close the terms" }));
    rerender(at(["offer"], false));                                        // the control closed: no case opens it
    rerender(at(["offer", "other_group"], false));
    expect(terms()).toBeNull();
    expect(onlyReadTexts()).toBe(true);
  });

  it("shows the parts its cases name, its placeholders filled, and each quotation with its source, address and dates", async () => {
    render(<RiskTerms point={1} kind="control" open cases={["back_home"]} homeGroup="your personal plans" />);
    const terms = await screen.findByRole("dialog", { name: CROSSING });
    expect(within(terms).getByText("This task started on your personal plans and goes back there.")).toBeTruthy();
    expect(within(terms).queryByText(/onto an organisation's seats/)).toBeNull();          // another case's part
    expect(within(terms).queryByText(/own settings apply/)).toBeNull();
    expect(within(terms).queryByText(/Words only a seat shows/)).toBeNull();               // another case's quotation
    expect(within(terms).getAllByText("This design's words, not a quotation")).toHaveLength(1);
    // a clause word for word in quotation marks, with its source, its address and its dates
    const clause = within(terms).getByText("“The customer answers for its account.”").closest("figure")!;
    expect(clause.textContent).toContain("Commercial Terms of Service, section D.5");
    expect(clause.textContent).toContain("Effective June 17, 2025");
    expect(clause.textContent).toMatch(/read on 10 Oct/);
    const link = within(clause).getByRole("link", { name: "https://terms.example/commercial" });
    expect([link.getAttribute("href"), link.getAttribute("target"), link.getAttribute("rel")])
      .toEqual(["https://terms.example/commercial", "_blank", "noreferrer noopener"]);
    // a clause not read yet says so in place of its words; a source with no date is said to give none
    const unread = within(terms).getByText("Not read for this release: whether this work may train models.").closest("figure")!;
    expect(unread.textContent).toContain("The page gives no date");
    expect(unread.textContent).not.toContain("“");
    cleanup();

    render(<RiskTerms point={13} kind="control" open cases={["offer"]} owner="Lena" />);
    const offer = screen.getByRole("dialog", { name: "Terms: Accepting a teammate's task" });
    // {owner} filled from the page, {operator} left as the route gave it
    expect(within(offer).getByText("If you accept, Lena's record keeps your user; {operator} holds it.")).toBeTruthy();
    expect(within(offer).queryByText(/custody record/)).toBeNull();
    // a section in words is named as it is; one in numbers or a code as a section (above)
    const principle = within(offer).getByText("Not read for this release: what the principle asks.").closest("figure")!;
    expect(principle.textContent).toContain("Privacy Act 2020 (New Zealand), information privacy principle 3:");
    expect(principle.textContent).not.toContain("section information");
    cleanup();
    render(<RiskTerms point={13} kind="control" open cases={["custody"]} />);
    expect(screen.getByText("If you accept custody, the task's owner's custody record names you.")).toBeTruthy();
    cleanup();
    render(<RiskTerms point={1} kind="control" open cases={["back_home"]} />);
    expect(screen.getByText("This task started on its home group and goes back there.")).toBeTruthy();
    cleanup();

    render(<RiskTerms point={1} kind="control" open cases={["crossing", "onto_seat"]} />);
    const seat = screen.getByRole("dialog", { name: CROSSING });
    expect(within(seat).getByText("The relay took this task onto an organisation's seats.")).toBeTruthy();
    expect(within(seat).getByText("The organisation's own settings apply on its seats.")).toBeTruthy();
    expect(within(seat).getByText("“Words only a seat shows.”")).toBeTruthy();
    expect(within(seat).queryByText(/goes back there/)).toBeNull();
    expect(within(seat).getAllByText("This design's words, not a quotation")).toHaveLength(2);
    cleanup();
    render(<RiskTerms point={1} kind="control" open />);                // no case named: only what every case shows
    const plain = screen.getByRole("dialog", { name: CROSSING });
    expect(within(plain).queryAllByText("This design's words, not a quotation")).toEqual([]);
    expect(within(plain).getAllByRole("figure")).toHaveLength(2);
  });

  it("opens what the product did by itself once in this browser, by account, its line keeping a link to it", async () => {
    const line = (event: string) => <RiskTerms point={1} kind="event" event={event} cases={["crossing"]} />;
    let page = render(<>{line("task:t1:7")}{line("task:t1:8")}</>);
    // the page open as the events came: each event's text at once
    await vi.waitFor(() => expect(screen.getAllByRole("dialog", { name: CROSSING })).toHaveLength(2));
    expect(screen.getAllByText("The relay took this task onto an organisation's seats.")).toHaveLength(2);
    page.unmount();
    forgetPage();                                                         // the owner's next visit: a new page
    page = render(<>{line("task:t1:7")}{line("task:t1:8")}</>);
    const links = await screen.findAllByRole("button", { name: "The terms: A crossing between organisations" });
    await settled();
    expect(screen.queryByRole("dialog")).toBeNull();                      // neither opens again
    fireEvent.click(links[0]);                                            // its line's link opens it, on demand
    expect(screen.getAllByRole("dialog", { name: CROSSING })).toHaveLength(1);
    page.unmount();
    forgetPage();
    // kept by account: another account signed in on this browser has opened neither
    useStore.setState({ user: { id: "u2", email: "teammate@example.org" } });
    render(line("task:t1:7"));
    await screen.findByRole("dialog", { name: CROSSING });
    expect(localStorage.getItem("pp-risk-shown:u1")).toContain("task:t1:7");
    expect(localStorage.getItem("pp-risk-shown:u2")).toContain("task:t1:7");
    expect(onlyReadTexts()).toBe(true);
  });

  it("opens a second browser's events of the last 7 days once, none older, and sends nothing of them", async () => {
    const now = Date.now() / 1000;
    const events = [{ event: "task:t1:3", at: now - 3 * DAY }, { event: "task:t1:2", at: now - 10 * DAY }];
    const lines = () => (
      <>
        <RiskTerms point={2} kind="event" event="task:t1:3" />
        <RiskTerms point={1} kind="event" event="task:t1:2" cases={["crossing"]} />
      </>
    );
    expect(localStorage.length).toBe(0);                                  // its storage holds nothing
    settleFirstVisit("u1", events, now);                                  // as the component listing the lines does
    let page = render(lines());
    await screen.findByRole("dialog", { name: RELAY });                   // the 3-day-old event's text
    await settled();
    expect(screen.queryByRole("dialog", { name: CROSSING })).toBeNull(); // not the 10-day-old one's
    page.unmount();
    forgetPage();
    settleFirstVisit("u1", events, now + 3600);                           // the next visit, an hour on
    page = render(lines());
    await screen.findByRole("button", { name: "The terms: The relay among your accounts" });
    await settled();
    expect(screen.queryByRole("dialog")).toBeNull();                      // neither, now
    // the app was only asked for the texts: nothing of what was shown reached it
    expect(onlyReadTexts()).toBe(true);
    expect(JSON.stringify(calls)).not.toContain("t1");
  });

  it("opens an event older than 7 days where this browser has opened one before, and takes it as opened where it has opened none", async () => {
    const now = Date.now() / 1000;
    // a browser that has opened none, at a later visit too: the 10-day-old move is taken as opened, never shown
    settleFirstVisit("u1", [], now - 11 * DAY);                           // a first look 11 days ago, nothing to open
    forgetPage();
    settleFirstVisit("u1", [{ event: "task:t1:5", at: now - 10 * DAY }], now);
    let page = render(<RiskTerms point={2} kind="event" event="task:t1:5" />);
    await screen.findByRole("button", { name: "The terms: The relay among your accounts" });
    await settled();
    expect(screen.queryByRole("dialog")).toBeNull();
    page.unmount();
    // a browser that has opened one: an older event it has not opened still opens, however long the owner was away
    localStorage.clear();
    forgetPage();
    page = render(<RiskTerms point={2} kind="event" event="task:t1:4" />);
    await screen.findByRole("dialog", { name: RELAY });                    // opened, and so kept as opened
    page.unmount();
    forgetPage();
    settleFirstVisit("u1", [{ event: "task:t1:6", at: now - 10 * DAY }], now);
    render(<RiskTerms point={2} kind="event" event="task:t1:6" />);
    await screen.findByRole("dialog", { name: RELAY });
  });

  it("answers alike for two lists on one page: neither list's old event opens because the other took its own as opened", async () => {
    const now = Date.now() / 1000;
    settleFirstVisit("u1", [{ event: "task:t1:2", at: now - 10 * DAY }], now);   // one task's section
    settleFirstVisit("u1", [{ event: "task:t2:2", at: now - 10 * DAY }], now);   // another's, on the same page
    render(<><RiskTerms point={2} kind="event" event="task:t1:2" /><RiskTerms point={1} kind="event" event="task:t2:2" cases={["crossing"]} /></>);
    await screen.findAllByRole("button", { name: /^The terms: / });
    await settled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("takes as opened an event 8 days old at a visit that opens the last 7 days alone", async () => {
    const now = Date.now() / 1000;
    settleFirstVisit("u1", [{ event: "task:t1:7", at: now - 8 * DAY }, { event: "task:t1:8", at: now - 6 * DAY }], now);
    render(<><RiskTerms point={1} kind="event" event="task:t1:7" cases={["crossing"]} /><RiskTerms point={2} kind="event" event="task:t1:8" /></>);
    await screen.findByRole("dialog", { name: RELAY });                    // 6 days old: opened
    await settled();
    expect(screen.queryByRole("dialog", { name: CROSSING })).toBeNull();  // 8 days old: not
  });

  it("holds an event taken as opened as opened: the next visit is no first visit, and an older event it lists opens", async () => {
    const now = Date.now() / 1000;
    settleFirstVisit("u1", [{ event: "task:t1:2", at: now - 10 * DAY }], now - DAY);   // a first visit a day ago, showing none
    forgetPage();
    settleFirstVisit("u1", [{ event: "task:t1:2", at: now - 10 * DAY }, { event: "task:t2:5", at: now - 9 * DAY }], now);
    render(<><RiskTerms point={1} kind="event" event="task:t1:2" cases={["crossing"]} /><RiskTerms point={2} kind="event" event="task:t2:5" /></>);
    await screen.findByRole("dialog", { name: RELAY });                    // listed now, 9 days old: it opens
    await settled();
    expect(screen.queryByRole("dialog", { name: CROSSING })).toBeNull();  // the one taken as opened: not
  });

  it("opens an old event listed later on a page where an event has been opened since its first look: the browser holds one now", async () => {
    const now = Date.now() / 1000;
    settleFirstVisit("u1", [{ event: "task:t1:3", at: now - 3 * DAY }], now);      // the page's first look: nothing opened yet
    const page = render(<RiskTerms point={2} kind="event" event="task:t1:3" />);
    await screen.findByRole("dialog", { name: RELAY });                              // opened, on this page
    page.unmount();
    settleFirstVisit("u1", [{ event: "task:t2:4", at: now - 8 * DAY }], now);       // a move listed later, on the same page
    render(<RiskTerms point={1} kind="event" event="task:t2:4" cases={["crossing"]} />);
    await screen.findByRole("dialog", { name: CROSSING });
  });

  it("says while the route fails that the terms could not be read just now, the control working as ever", async () => {
    down = true;
    const made = vi.fn();
    render(<Form onMake={made} />);
    fireEvent.click(screen.getByRole("button", { name: "New task" }));
    const terms = screen.getByRole("dialog", { name: "Terms: Risk point 2" });
    await within(terms).findByText("The terms at this point could not be read just now.");
    fireEvent.click(screen.getByRole("button", { name: "Make the task" }));
    expect(made).toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBe(terms);
  });

  it("keeps an event's text for a later visit while it cannot be read, marking nothing", async () => {
    down = true;
    const page = render(<RiskTerms point={2} kind="event" event="task:t1:9" />);
    await vi.waitFor(() => expect(calls).toHaveLength(1));
    await settled();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(localStorage.getItem("pp-risk-shown:u1")).toBeNull();
    page.unmount();
    forgetPage();
    down = false;
    render(<RiskTerms point={2} kind="event" event="task:t1:9" />);      // the next visit, the route answering
    await screen.findByRole("dialog", { name: RELAY });
  });

  it("opens an event once a page, and none older than 7 days, where the browser's storage throws", async () => {
    const refuse = () => { throw new DOMException("The storage is turned off.", "SecurityError"); };
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(refuse);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(refuse);
    const now = Date.now() / 1000;
    const events = [{ event: "task:t1:3", at: now - 3 * DAY }, { event: "task:t1:2", at: now - 10 * DAY }];
    const lines = () => (
      <>
        <RiskTerms point={2} kind="event" event="task:t1:3" />
        <RiskTerms point={1} kind="event" event="task:t1:2" />
      </>
    );
    settleFirstVisit("u1", events, now);
    let page = render(lines());
    await screen.findByRole("dialog", { name: RELAY });
    await settled();
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    page.unmount();
    page = render(lines());                                               // its lines shown again, on the same page
    await settled();
    expect(screen.queryByRole("dialog")).toBeNull();
    page.unmount();
    forgetPage();                                                         // a new page: this browser kept nothing
    settleFirstVisit("u1", events, now + 60);
    render(lines());
    await screen.findByRole("dialog", { name: RELAY });
    await settled();
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });

  it("closes on Escape as what is on top, and stays under a dialog that holds the keyboard", async () => {
    const at = (over: boolean) => (
      <>
        <RiskTerms point={2} kind="control" open />
        {over && <div role="dialog" aria-modal="true" aria-label="A dialog over it" />}
      </>
    );
    const { rerender } = render(at(true));
    await screen.findByRole("dialog", { name: RELAY });
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.getByRole("dialog", { name: RELAY })).toBeTruthy();    // that dialog's Escape, not this text's
    rerender(at(false));
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps a click on its words from the label it sits in, its links and its Close working as ever", async () => {
    const turned = vi.fn();
    render(<label className="relative"><input type="checkbox" onChange={turned} /> Cross organisations <RiskTerms point={1} kind="control" open /></label>);
    const terms = await screen.findByRole("dialog", { name: CROSSING });
    expect(fireEvent.click(within(terms).getByText("“The customer answers for its account.”"))).toBe(false);
    expect(turned).not.toHaveBeenCalled();                                // the switch was not turned
    // a link is followed: its click reaches the window with nothing prevented (where this test stops it)
    let followed: boolean | null = null;
    const stop = (e: MouseEvent) => { followed = !e.defaultPrevented; e.preventDefault(); };
    window.addEventListener("click", stop);
    fireEvent.click(within(terms).getByRole("link", { name: "https://terms.example/commercial" }));
    window.removeEventListener("click", stop);
    expect(followed).toBe(true);
    fireEvent.click(within(terms).getByRole("button", { name: "Close the terms" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(turned).not.toHaveBeenCalled();
  });
});

// Kind "before": a text the page's own act waits on, risk point 16's (the accounts page's read of an account by
// itself), which opens as that act waits and whose Close, once its words have shown, this browser keeps for the
// signed-in account, apart from the events' record; Connections' tests play the read itself.
describe("a text the page's own act waits on (kind \"before\", risk point 16's)", () => {
  it("opens as its act waits, and keeps the owner's Close in this browser alone, for that account and point, which lets the act go on", async () => {
    let went = false;
    void untilClosedHere(16, "u1").then(() => { went = true; });
    render(<RiskTerms point={16} kind="before" open />);
    const terms = await screen.findByRole("dialog", { name: READ });
    await within(terms).findByText(/once by itself to read who it is/);
    expect(terms.getAttribute("aria-modal")).toBe("false");
    expect(within(terms).getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual(["Close the terms"]);
    await settled();
    expect(went).toBe(false);                                             // open: what waits on it waits
    expect(closedHere(16, "u1")).toBe(false);
    fireEvent.click(within(terms).getByRole("button", { name: "Close the terms" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await settled();
    expect(went).toBe(true);
    expect(closedHere(16, "u1")).toBe(true);
    // that account's and that point's alone, and no event's: the events' record holds nothing of it
    expect(closedHere(16, "u2")).toBe(false);
    expect(closedHere(2, "u1")).toBe(false);
    expect(closedHere(16, "")).toBe(false);
    expect(localStorage.getItem("pp-risk-shown:u1")).toBeNull();
    // kept by this browser: the next page keeps it, and what waits on it there goes on at once
    forgetPage();
    expect(closedHere(16, "u1")).toBe(true);
    await expect(untilClosedHere(16, "u1")).resolves.toBeUndefined();
    // a browser whose storage was cleared keeps none
    localStorage.clear();
    expect(closedHere(16, "u1")).toBe(false);
    // the app was asked for the texts and nothing else
    expect(onlyReadTexts()).toBe(true);
  });

  it("keeps nothing of a Close before its words could be read, and Escape, once they have shown, closes it as its Close does", async () => {
    down = true;
    let went = false;
    void untilClosedHere(16, "u1").then(() => { went = true; });
    const page = render(<RiskTerms point={16} kind="before" open />);
    const unread = screen.getByRole("dialog", { name: "Terms: Risk point 16" });
    await within(unread).findByText("The terms at this point could not be read just now.");
    fireEvent.click(within(unread).getByRole("button", { name: "Close the terms" }));
    await settled();
    expect(closedHere(16, "u1")).toBe(false);
    expect(went).toBe(false);
    page.unmount();
    down = false;
    render(<RiskTerms point={16} kind="before" open />);                 // opened again, the route answering
    await within(await screen.findByRole("dialog", { name: READ })).findByText(/once by itself/);
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    await settled();
    expect(went).toBe(true);
    expect(closedHere(16, "u1")).toBe(true);
  });

  it("keeps a close for the page alone where the browser's storage throws, and the next page opens it again", async () => {
    const refuse = () => { throw new DOMException("The storage is turned off.", "SecurityError"); };
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(refuse);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(refuse);
    let went = false;
    void untilClosedHere(16, "u1").then(() => { went = true; });
    render(<RiskTerms point={16} kind="before" open />);
    const terms = await screen.findByRole("dialog", { name: READ });
    await within(terms).findByText(/once by itself/);
    fireEvent.click(within(terms).getByRole("button", { name: "Close the terms" }));
    await settled();
    expect(went).toBe(true);
    expect(closedHere(16, "u1")).toBe(true);                             // this page keeps it
    forgetPage();
    expect(closedHere(16, "u1")).toBe(false);                            // the next one does not
  });
});
