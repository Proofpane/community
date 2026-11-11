import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SessionRecord } from "../api";
import { useStore } from "../store";
import { ChatPane } from "./ChatPane";
import { linkOut, OutLink, ownOrigins } from "./OutLink";
import { MarkdownView } from "./Preview";

// Links in words this page did not write: a session's, a chat's, a Markdown file's. One into this
// page could open its device pane (#device=CODE) or app-consent pane (#connect=ID) with a code the words chose.

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); useStore.setState(useStore.getInitialState()); });

const OWN = ["https://pp.example"];
const DEVICE = "#device=ABCD-EFGH";
const CONNECT = `#connect=${"0123456789abcdef".repeat(2)}`;

describe("where a link in words the page did not write opens", () => {
  it("opens an http or https address on another site, as the browser reads it", () => {
    expect(linkOut("https://example.org/run", OWN)).toBe("https://example.org/run");
    expect(linkOut("http://example.org/a b", OWN)).toBe("http://example.org/a%20b");
    expect(linkOut("https://pp.example:8443/app/", OWN)).toBe("https://pp.example:8443/app/");     // another port: another site
  });

  it("opens nothing else: a fragment, a relative address, another scheme, or this page's own site", () => {
    for (const href of [DEVICE, CONNECT, `app/${DEVICE}`, `/app/${DEVICE}`, `//evil.example/app/${DEVICE}`, "mailto:a@example.org",
      "javascript:alert(1)", "ftp://example.org/x", "", undefined, null, `https://pp.example/app/${DEVICE}`,
      `HTTPS://PP.EXAMPLE:443/app/${CONNECT}`, "https://pp.example"]) {
      expect(linkOut(href, OWN), String(href)).toBeNull();
    }
  });

  it("counts as this page's own its own site, its server's and the address its server gives for itself", () => {
    expect(ownOrigins(false)).toEqual([location.origin]);
    expect(linkOut(`${location.origin}/app/${DEVICE}`)).toBeNull();
    localStorage.setItem("pp-open-server", "https://srv.example");
    useStore.setState({ links: { links: [], questions: [], public_url: "https://pub.example/app/", asks: [] } });
    expect(ownOrigins(false)).toEqual([location.origin, "https://srv.example", "https://pub.example"]);
    expect(linkOut(`https://srv.example/${DEVICE}`)).toBeNull();
    expect(linkOut(`https://pub.example/app/${DEVICE}`)).toBeNull();
    expect(linkOut("https://example.org/")).toBe("https://example.org/");
    useStore.setState({ links: { links: [], questions: [], public_url: "not an address", asks: [] } });
    expect(ownOrigins(false)).toEqual([location.origin, "https://srv.example"]);
  });

  it("counts in the desktop app each place's server too", () => {
    localStorage.setItem("pp-open-places", JSON.stringify([
      { id: "local", server: "http://127.0.0.1:8765", label: "This Mac" }, { id: "h1", server: "https://hosted.example", label: "Hosted" }]));
    expect(ownOrigins(true)).toEqual([location.origin, "http://127.0.0.1:8765", "https://hosted.example"]);
    expect(ownOrigins(false)).toEqual([location.origin]);
  });
});

describe("a link shown", () => {
  it("to another site, opens beside the page", () => {
    render(<OutLink href="https://example.org/a run">the run</OutLink>);
    const a = screen.getByRole("link", { name: "the run" });
    // the address it opens is the one it checked, as the browser reads it
    expect([a.getAttribute("href"), a.getAttribute("target"), a.getAttribute("rel")]).toEqual(["https://example.org/a%20run", "_blank", "noopener noreferrer"]);
  });

  it("anywhere else, is its words and where it pointed, with no link", () => {
    const { container } = render(<OutLink href={DEVICE}>Review the change</OutLink>);
    expect(screen.queryByRole("link")).toBeNull();
    expect(container.textContent).toBe(`Review the change [link not opened: ${DEVICE}]`);
    cleanup();
    const shown = render(<OutLink href={DEVICE}>{DEVICE}</OutLink>);                                  // words that are its address
    expect(shown.container.textContent).toBe(`${DEVICE} [link not opened]`);
    cleanup();
    const long = render(<OutLink href={`#${"a".repeat(150)}`}>more</OutLink>);
    expect(long.container.textContent).toBe(`more [link not opened: #${"a".repeat(99)}…]`);
    cleanup();
    expect(render(<OutLink>words</OutLink>).container.textContent).toBe("words [link not opened]");
  });
});

describe("in a chat", () => {
  const ID = "0123456789ab";
  const record = { id: ID, repo: "app", program: "chat", task: "a task", base: "abc", branch: null, created_at: 1, finished: null,
    running: true, exit_code: null, ended_at: null, chat: null } as SessionRecord;

  it("opens only a link to another site, in what it wrote and in what it is writing", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "no route" }), { status: 404 })));
    useStore.setState({
      loadChat: async () => {},
      chat: { [ID]: [{ seq: 1, kind: "text", text: `[Review the change](${DEVICE}) and [the docs](https://example.org/docs)` }] },
      live: { [ID]: { blocks: [{ block: 0, text: `[allow the report bot](${location.origin}/app/${CONNECT})`, lost: false }], thinking: false, tool: null } },
    });
    const { container } = render(<ChatPane record={record} />);
    expect(screen.getByRole("link", { name: "the docs" }).getAttribute("target")).toBe("_blank");
    expect(screen.queryByRole("link", { name: "Review the change" })).toBeNull();
    expect(container.textContent).toContain(`Review the change [link not opened: ${DEVICE}]`);
    await vi.waitFor(() => expect(container.textContent).toContain("allow the report bot [link not opened:"));
    expect(screen.queryByRole("link", { name: "allow the report bot" })).toBeNull();
  });
});

describe("in a Markdown file", () => {
  it("opens a link in the worktree there and one to another site beside the page; any other is text", () => {
    const opened = vi.fn();
    const text = `[a file](docs/x.md) [the docs](https://example.org/docs) [up](#install) [the device](${location.origin}/app/${DEVICE})`;
    const { container } = render(<MarkdownView session="s" path="README.md" text={text} onOpen={opened} />);
    fireEvent.click(screen.getByRole("link", { name: "a file" }));
    expect(opened).toHaveBeenCalledWith("docs/x.md");
    expect(screen.getByRole("link", { name: "the docs" }).getAttribute("target")).toBe("_blank");
    expect(screen.queryByRole("link", { name: "up" })).toBeNull();
    expect(screen.queryByRole("link", { name: "the device" })).toBeNull();
    // a heading in the file is named, as before; the link into the page says where it pointed
    expect(container.textContent).toContain(`the docs up the device [link not opened: ${location.origin}/app/${DEVICE}]`);
  });
});
