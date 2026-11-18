import { afterEach, describe, expect, it, vi } from "vitest";
import { terminalLink } from "./api";

// The terminal's link says whether what it was given went out: the page's Stop says "Esc not sent" when it did not
// (WS-BUTTONS, part one). A stand-in websocket, connecting until the case opens it.
class Socket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static last: Socket | null = null;
  readyState = 0;
  binaryType = "";
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: unknown) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor() { Socket.last = this; }
  send(d: unknown) { this.sent.push(d); }
  close() {}
}

afterEach(() => {
  vi.unstubAllGlobals();
  Socket.last = null;
});

describe("the terminal's link", () => {
  it("says false when it sent nothing, its connection not open, and true when it sent", () => {
    vi.stubGlobal("WebSocket", Socket);
    const conn = terminalLink("0123456789ab").attach({ write() {}, status() {} });
    const ws = Socket.last!;
    expect(conn.send("\x1b")).toBe(false);
    expect(ws.sent).toEqual([]);
    ws.readyState = Socket.OPEN;
    expect(conn.send("\x1b")).toBe(true);
    expect(ws.sent.map((d) => Array.from(d as Uint8Array))).toEqual([[27]]);
    ws.readyState = Socket.CLOSED;
    expect(conn.send("\x1b")).toBe(false);
    conn.close();
  });
});
