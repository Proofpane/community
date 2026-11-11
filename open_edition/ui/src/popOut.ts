// A pop-out's address: one panel of the control view's map in a window of its
// own, opened on the page with a query that names it. Kept apart from sessionMap.ts so the page's first chunk, which
// reads it to know whether it is a pop-out (root.tsx), carries none of the map's own code.

/** A panel of the map in a window of its own: the map, one session's map, or one agent. */
export type PopOut = { view: "map" } | { view: "session"; session: string } | { view: "agent"; session: string; agent: string };
/** A workspace session's id: twelve lower-case hex digits. */
export const SESSION_ID = /^[0-9a-f]{12}$/;
/** An agent's id as a pop-out takes it: what a window of the desktop app may be named by (its lib.rs, popout_of). */
export const AGENT_ID = /^[A-Za-z0-9_-]{1,64}$/;

/** The address's query that opens a pop-out on the page, as `popOutOf` reads it back. */
export function popOutQuery(p: PopOut): string {
  const q = new URLSearchParams({ pop: p.view });
  if (p.view !== "map") q.set("session", p.session);
  if (p.view === "agent") q.set("agent", p.agent);
  return `?${q.toString()}`;
}

/** The pop-out an address's query asks for; null for none, or for one that names what is not a session or an agent. */
export function popOutOf(search: string): PopOut | null {
  const q = new URLSearchParams(search);
  const view = q.get("pop");
  const session = q.get("session") || "";
  const agent = q.get("agent") || "";
  if (view === "map") return { view };
  if (view === "session" && SESSION_ID.test(session)) return { view, session };
  if (view === "agent" && SESSION_ID.test(session) && AGENT_ID.test(agent)) return { view, session, agent };
  return null;
}
