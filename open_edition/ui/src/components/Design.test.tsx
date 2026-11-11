import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { agentInbox, designMessage, PickDialog, shotBlob, type DesignPick } from "./Design";
import type { SessionRecord } from "../api";

afterEach(cleanup);

const pick: DesignPick = {
  url: "http://localhost:3000/settings",
  title: "Settings",
  selector: "main#app > section.card > button#save",
  html: '<button id="save" class="primary">Save</button>',
  text: "Save",
  rect: { x: 24, y: 512, width: 120, height: 40 },
  viewport: { width: 1280, height: 800 },
  styles: { "background-color": "rgb(37, 99, 235)", padding: "8px 16px" },
};

const rec = (over: Partial<SessionRecord>): SessionRecord =>
  ({ id: "0123456789ab", program: "claude", running: true, task: "Build the settings page\nwith a form", ...over }) as SessionRecord;

describe("an element picked in the preview", () => {
  it("goes to the agent with the person's words first, then where and what the element is", () => {
    const text = designMessage("  Make it full width on a phone  ", pick);
    expect(text.split("\n")).toEqual([
      "Make it full width on a phone",
      "",
      'The element, picked in the running app at http://localhost:3000/settings ("Settings"):',
      "- CSS path: main#app > section.card > button#save",
      "- box: 120x40 at (24, 512) in a 1280x800 window",
      '- its text: "Save"',
      "- computed styles: background-color: rgb(37, 99, 235); padding: 8px 16px",
      "- its HTML:",
      '<button id="save" class="primary">Save</button>',
    ]);
    const withPicture = designMessage("Bigger", pick, ".proofpane-uploads/picked-1.png").split("\n");
    expect(withPicture[5]).toBe("- a picture of it, as the preview showed it: .proofpane-uploads/picked-1.png");
    const bare = designMessage("Bigger", { ...pick, title: "", text: "", styles: {} });
    expect(bare).not.toContain("its text");
    expect(bare).not.toContain("computed styles");
    expect(bare).toContain("at http://localhost:3000/settings:");
  });

  it("goes where the review of changes goes: the chat, or a running Claude Code or Codex terminal", () => {
    expect(agentInbox(rec({ program: "chat", running: false }))).toBe("chat");
    expect(agentInbox(rec({ program: "claude" }))).toBe("terminal");
    expect(agentInbox(rec({ program: "codex" }))).toBe("terminal");
    expect(agentInbox(rec({ program: "claude", running: false }))).toBeNull();
    expect(agentInbox(rec({ program: "shell" }))).toBeNull();
  });

  it("cannot be sent without words, nor to a session whose agent is not running, but can be copied", () => {
    const onClose = vi.fn();
    render(<PickDialog event={{ session: "0123456789ab", page: pick.url, pick }} rec={rec({ running: false })} onClose={onClose} />);
    const send = screen.getByRole("button", { name: /Send to/ }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    expect(screen.getByText(/is not running, so there is nothing to send to/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("What should change"), { target: { value: "Bigger" } });
    expect(send.disabled).toBe(true);
    expect(screen.getByText("main#app > section.card > button#save")).toBeTruthy();
    expect(send.textContent).toContain('"Build the settings page"');
  });

  it("is ready to send once there are words and a running agent", () => {
    render(<PickDialog event={{ session: "0123456789ab", page: pick.url, pick }} rec={rec({})} onClose={() => {}} />);
    const send = screen.getByRole("button", { name: /Send to/ }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("What should change"), { target: { value: "Bigger" } });
    expect(send.disabled).toBe(false);
    expect(screen.getByText(/Typed into the agent's terminal/)).toBeTruthy();
  });
});

describe("the picture of a picked element", () => {
  it("is the PNG the shell sent, shown in the dialog", async () => {
    const png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";
    const blob = shotBlob(png);
    expect(blob.type).toBe("image/png");
    expect(new Uint8Array(await blob.arrayBuffer()).slice(0, 4)).toEqual(new Uint8Array([0x89, 0x50, 0x4e, 0x47]));
    const created = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:picture");
    render(<PickDialog event={{ session: "0123456789ab", page: pick.url, pick, shot: png }} rec={rec({})} onClose={() => {}} />);
    expect((screen.getByAltText("The element, as the preview showed it") as HTMLImageElement).src).toBe("blob:picture");
    created.mockRestore();
  });
});

describe("the preview, in the desktop build", () => {
  afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.resetModules(); localStorage.clear(); });

  it("offers what the session's app listens on, its first port by default, and opens the one chosen", async () => {
    vi.stubEnv("MODE", "desktop");
    vi.resetModules();
    const places = await import("../places");
    places.savePlace({ id: places.LOCAL, server: "http://127.0.0.1:5000", label: "This Mac" });
    places.setPlaceToken(places.LOCAL, "a.b", false);
    const fetched: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      fetched.push(url);
      return new Response(JSON.stringify({ ports: [{ port: 5173, address: "127.0.0.1", pid: 7, command: "node" },
                                                   { port: 8000, address: "*", pid: 9, command: "Python" }] }), { status: 200 });
    }));
    const invoked: [string, unknown][] = [];
    (window as unknown as { __TAURI_INTERNALS__: unknown }).__TAURI_INTERNALS__ = {
      invoke: async (cmd: string, args: unknown) => { invoked.push([cmd, args]); return null; },
    };
    const { PreviewButton } = await import("./Design");
    render(<PreviewButton rec={{ id: "0123456789ab" }} />);
    fireEvent.click(screen.getByTitle(/Preview the app this session serves/));
    await screen.findByText(":5173 node");
    expect(fetched).toEqual(["http://127.0.0.1:5000/api/ade/workspace/sessions/0123456789ab/ports"]);
    const input = screen.getByLabelText("The app, on this Mac") as HTMLInputElement;
    expect(input.value).toBe("http://localhost:5173");
    fireEvent.click(screen.getByText(":8000 Python"));
    expect(input.value).toBe("http://localhost:8000");
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    await vi.waitFor(() => expect(invoked).toEqual([["open_preview", { session: "0123456789ab", url: "http://localhost:8000" }]]));
    // the address is remembered for this session, and then offered before any port
    expect(localStorage.getItem("pp-preview@0123456789ab")).toBe("http://localhost:8000");
  });
});
