import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { HookRequest } from "../api";
import { clock, day, dayTime } from "../when";
import { HeldFrom } from "./Approvals";

// A held command says which session asked for it (the hook token's name, and the session's
// id and folder as its hook's word), when it asked, and until when it waits for an answer, so the owner knows what an
// approval is for.

afterEach(cleanup);

const NOW = Date.UTC(2026, 9, 10, 12, 0, 0);
const TODAY = new Date(NOW);
const at = (seconds: number) => Math.floor(NOW / 1000) + seconds;
const held = (over: Partial<HookRequest> = {}): HookRequest => ({
  id: "r1", command: "git push origin main", tool: "Bash", session: "6f1c2d3e-aaaa", cwd: "/work/app", state: "pending",
  created_at: at(-300), until: at(1800), token_name: "Mac session", ...over,
});
const line = () => screen.getByText(/^A held command/);
// characters built from their code points, so this file holds none that does not show as itself
const RLO = String.fromCodePoint(0x202e), ZWSP = String.fromCodePoint(0x200b), ALEF = String.fromCodePoint(0x5d0);

describe("a held command's card", () => {
  it("names the hook token, then the session and folder as its hook says them", () => {
    render(<HeldFrom q={held()} now={NOW} />);
    expect(line().textContent).toBe("A held command from “Mac session”; its hook says session 6f1c2d3e-aaaa, folder /work/app");
  });

  it("isolates each name, so a right-to-left letter in one cannot reorder the line", () => {
    render(<HeldFrom q={held({ session: `abc${ALEF}-123` })} now={NOW} />);
    expect([...line().querySelectorAll("bdi")].map((b) => b.textContent)).toEqual(["Mac session", `abc${ALEF}-123`, "/work/app"]);
  });

  it("says when it asked and until when it waits for an answer, each with its full time in the title", () => {
    render(<HeldFrom q={held()} now={NOW} />);
    const said = screen.getByText(/asked 5m ago/).parentElement as HTMLElement;
    // the clock alone on the day of `now` in the device's zone, else the day too (the next test)
    const until = day(at(1800), undefined, TODAY) === day(TODAY, undefined, TODAY) ? clock(at(1800)) : dayTime(at(1800), undefined, TODAY);
    expect(said.textContent).toBe(`asked 5m ago; waits for an answer until ${until}`);
    const [asked, upTo] = [...said.querySelectorAll("span")];
    expect(asked.getAttribute("title")).toMatch(/2026/);
    expect(upTo.getAttribute("title")).toMatch(/2026/);
  });

  it("gives the day too when it waits past that day, its year by `now`, whatever today's date", () => {
    const late = at(30 * 3600);                     // another day in every time zone
    render(<HeldFrom q={held({ until: late })} now={NOW} />);
    const said = screen.getByText(/asked 5m ago/).parentElement as HTMLElement;
    expect(said.textContent).toBe(`asked 5m ago; waits for an answer until ${dayTime(late, undefined, TODAY)}`);
    // the same year as `now` is not written, so this holds in any year the tests run
    expect(said.textContent).toMatch(/until \d{1,2} [A-Z][a-z]{2}, \d{2}:\d{2}$/);
  });

  it("says only until when it waits when it does not say when it asked", () => {
    render(<HeldFrom q={held({ created_at: "" })} now={NOW} />);
    expect(screen.getByText(/aits for an answer/).parentElement?.textContent).toMatch(/^Waits for an answer until /);
  });

  it("writes out a character a session sent that is not shown as itself", () => {
    render(<HeldFrom q={held({ session: `abc${RLO}def`, token_name: `Mac${ZWSP}session`, cwd: `/work${RLO}/app` })} now={NOW} />);
    const said = line().textContent ?? "";
    expect(said).toContain("session abc[U+202E]def");
    expect(said).toContain("“Mac[U+200B]session”");
    expect(said).toContain("folder /work[U+202E]/app");
    expect(said.includes(RLO) || said.includes(ZWSP)).toBe(false);
  });

  it("says the hook's word alone when it has no token name, and the folder alone when that is all", () => {
    render(<HeldFrom q={held({ token_name: undefined })} now={NOW} />);
    expect(line().textContent).toBe("A held command; its hook says session 6f1c2d3e-aaaa, folder /work/app");
    cleanup();
    render(<HeldFrom q={held({ token_name: undefined, session: null })} now={NOW} />);
    expect(line().textContent).toBe("A held command; its hook says folder /work/app");
  });

  it("says only what it has: no token name, session, folder or times", () => {
    render(<HeldFrom q={held({ token_name: undefined, session: null, cwd: null, until: undefined, created_at: "" })} now={NOW} />);
    expect(line().textContent).toBe("A held command");
    expect(screen.queryByText(/asked|waits/i)).toBeNull();
  });

  it("draws nothing stray for a time of 0", () => {
    const { container } = render(<HeldFrom q={held({ until: 0, created_at: "" })} now={NOW} />);
    expect(container.textContent).toBe("A held command from “Mac session”; its hook says session 6f1c2d3e-aaaa, folder /work/app");
  });
});
