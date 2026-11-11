import { beforeEach, describe, expect, it } from "vitest";
import { isUnread, useSeen } from "./seen";

const at = (minute: number) => `2026-10-02T00:${String(minute).padStart(2, "0")}:00.000Z`;

describe("what the person has seen of each session", () => {
  beforeEach(() => useSeen.setState({ seen: {} }));

  it("takes a session never seen before as seen, so a first visit shows nothing unread", () => {
    useSeen.getState().observe([{ id: "a", updatedAt: at(1), open: false }]);
    expect(isUnread(useSeen.getState().seen, "a", at(1), false)).toBe(false);
  });

  it("is unread when a closed session does something, and read again once it is open", () => {
    const { observe } = useSeen.getState();
    observe([{ id: "a", updatedAt: at(1), open: false }]);
    observe([{ id: "a", updatedAt: at(5), open: false }]);
    expect(isUnread(useSeen.getState().seen, "a", at(5), false)).toBe(true);
    expect(isUnread(useSeen.getState().seen, "a", at(5), true)).toBe(false);     // open: never shown unread
    observe([{ id: "a", updatedAt: at(5), open: true }]);
    expect(isUnread(useSeen.getState().seen, "a", at(5), false)).toBe(false);
  });

  it("keeps a session marked unread until it is chosen again, even while it is open", () => {
    const s = useSeen.getState();
    s.observe([{ id: "a", updatedAt: at(1), open: true }]);
    s.markUnread("a");
    s.observe([{ id: "a", updatedAt: at(2), open: true }]);
    expect(isUnread(useSeen.getState().seen, "a", at(2), true)).toBe(true);
    useSeen.getState().chosen("a");
    useSeen.getState().observe([{ id: "a", updatedAt: at(2), open: true }]);
    expect(isUnread(useSeen.getState().seen, "a", at(2), false)).toBe(false);
  });

  it("does not come back unread when a marked session is chosen and left before the next refresh", () => {
    const s = useSeen.getState();
    s.observe([{ id: "a", updatedAt: at(3), open: false }]);
    s.markUnread("a");
    useSeen.getState().chosen("a", at(3));
    useSeen.getState().observe([{ id: "a", updatedAt: at(3), open: false }]);     // already chosen another
    expect(isUnread(useSeen.getState().seen, "a", at(3), false)).toBe(false);
  });

  it("forgets a session that is gone", () => {
    const s = useSeen.getState();
    s.observe([{ id: "a", updatedAt: at(1), open: false }, { id: "b", updatedAt: at(1), open: false }]);
    useSeen.getState().observe([{ id: "b", updatedAt: at(1), open: false }]);
    expect(Object.keys(useSeen.getState().seen)).toEqual(["b"]);
  });
});
