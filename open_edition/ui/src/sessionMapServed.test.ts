import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import examples from "./sessionMapExamples.json";
import { callsOf, mapOf } from "./sessionMap";

// What the app's two map routes answer with the real workspace agent behind them: the server's test
// (open_edition/tests/test_workspace_map_served.py) keeps it in tests/fixtures/session-map-served.json, and fails when
// the agent no longer serves it; this test reads it as the page reads the routes (mapOf, callsOf), and fails when a
// field the agent serves does not arrive with its value and its type. A field renamed on either side fails one or the
// other. Its session ids and times are fixed and each list kept to three items; the file says so.
type Json = Record<string, unknown>;
const SERVED: { map: Json & { sessions: Json[] }; session_maps: Json[] } = JSON.parse(
  readFileSync(join(__dirname, "..", "..", "tests", "fixtures", "session-map-served.json"), "utf8"));
/** A time no answer gives, for the page to take where an answer gives none. */
const NOW = 1;

// the examples' fields, each object's (with agents_more, which the workspace's agent serves beyond them)
const SESSION_FIELDS = Object.keys(examples.map.sessions[0]);
const AGENT_FIELDS = Object.keys(examples.map.sessions[0].agents[0]);
const ONE_FIELDS = Object.keys(examples.session_map);
const HOLDER_FIELDS = Object.keys(examples.session_map.agents[0]);
const CALL_FIELDS = Object.keys(examples.session_map.main.calls[0]);
const TURN_FIELDS = Object.keys(examples.session_map.turns[0]);

const as = (v: unknown) => v as Json;
const listed = (v: unknown) => v as Json[];
const without = <T>(value: T, drop: (copy: T) => void): T => {
  const copy = structuredClone(value);
  drop(copy);
  return copy;
};

/** Each field of `fields` in `kept` as in `served`, of its type: the name with it, so a failure says which. */
function same(kept: unknown, served: unknown, fields: string[], skip: string[] = []) {
  for (const f of fields.filter((x) => !skip.includes(x))) expect([f, as(kept)[f]]).toStrictEqual([f, as(served)[f]]);
}

describe("the map as the real agent serves it, read as the page reads it", () => {
  it("has each case the page must read", () => {
    const sessions = SERVED.map.sessions;
    const agents = sessions.flatMap((s) => listed(s.agents));
    const calls = SERVED.session_maps.flatMap((m) => [as(m.main), ...listed(m.agents)]).flatMap((h) => listed(h.calls));
    expect(sessions.length).toBe(3);
    expect(sessions.some((s) => typeof s.agents_more === "number" && s.agents_more > 0)).toBe(true);
    expect(agents.some((a) => typeof a.effort === "number")).toBe(true);
    expect(agents.some((a) => a.status === null) && agents.some((a) => a.name === null)).toBe(true);
    expect(calls.some((c) => c.target === null) && calls.some((c) => c.failed === true)).toBe(true);
    expect(SERVED.session_maps.some((m) => m.cut === true)).toBe(true);
  });

  it("keeps each field of the map with its value and its type", () => {
    const kept = mapOf(SERVED.map, NOW);
    expect(kept.at).toBe(SERVED.map.at);
    expect(kept.sessions.map((s) => s.id)).toEqual(SERVED.map.sessions.map((s) => s.id));
    SERVED.map.sessions.forEach((s, i) => {
      same(kept.sessions[i], s, SESSION_FIELDS, ["agents"]);
      expect(kept.sessions[i].agents.map((a) => a.id)).toEqual(listed(s.agents).map((a) => a.id));
      listed(s.agents).forEach((a, j) => same(kept.sessions[i].agents[j], a, AGENT_FIELDS));
      // and nothing more than the served fields: one the page keeps under another name fails here
      expect(Object.keys(kept.sessions[i]).sort()).toEqual([...SESSION_FIELDS].sort());
      kept.sessions[i].agents.forEach((a) => expect(Object.keys(a).sort()).toEqual([...AGENT_FIELDS].sort()));
    });
  });

  it("keeps each field of a session's calls and turns with its value and its type", () => {
    for (const served of SERVED.session_maps) {
      const kept = callsOf(served, NOW);
      same(kept, served, ONE_FIELDS, ["main", "agents", "turns"]);
      const holders = [as(served.main), ...listed(served.agents)];
      const keptHolders = [kept.main, ...kept.agents] as unknown[];
      expect(keptHolders.length).toBe(holders.length);
      holders.forEach((h, i) => {
        same(keptHolders[i], h, HOLDER_FIELDS.filter((f) => i > 0 || f !== "id"), ["calls"]);
        listed(h.calls).forEach((c, j) => same(listed(as(keptHolders[i]).calls)[j], c, CALL_FIELDS));
        expect(listed(as(keptHolders[i]).calls).length).toBe(listed(h.calls).length);
      });
      expect(kept.turns.length).toBe(listed(served.turns).length);
      listed(served.turns).forEach((t, j) => same(kept.turns[j], t, TURN_FIELDS));
    }
  });

  // the file holds, for each field, a value the page would not have without it: so a field the page read under another
  // name, as one renamed in the page would be, fails the tests above rather than passing on a default
  it("reads each field: without it, what the page keeps differs", () => {
    const whole = mapOf(SERVED.map, NOW);
    expect(mapOf(without(SERVED.map, (m) => { delete (m as Json).at; }), NOW)).not.toStrictEqual(whole);
    for (const f of SESSION_FIELDS) {
      const gone = without(SERVED.map, (m) => m.sessions.forEach((s) => { delete s[f]; }));
      expect([f, mapOf(gone, NOW)]).not.toStrictEqual([f, whole]);
    }
    for (const f of AGENT_FIELDS) {
      const gone = without(SERVED.map, (m) => m.sessions.forEach((s) => listed(s.agents).forEach((a) => { delete a[f]; })));
      expect([f, mapOf(gone, NOW)]).not.toStrictEqual([f, whole]);
    }
    const all = SERVED.session_maps.map((m) => callsOf(m, NOW));
    const each = (drop: (m: Json) => void) => SERVED.session_maps.map((m) => callsOf(without(m, drop), NOW));
    for (const f of ONE_FIELDS) expect([f, each((m) => { delete m[f]; })]).not.toStrictEqual([f, all]);
    for (const f of HOLDER_FIELDS) {
      expect([f, each((m) => [as(m.main), ...listed(m.agents)].forEach((h) => { delete h[f]; }))]).not.toStrictEqual([f, all]);
    }
    for (const f of CALL_FIELDS) {
      const drop = (m: Json) => [as(m.main), ...listed(m.agents)].forEach((h) => listed(h.calls).forEach((c) => { delete c[f]; }));
      expect([f, each(drop)]).not.toStrictEqual([f, all]);
    }
    for (const f of TURN_FIELDS) {
      expect([f, each((m) => listed(m.turns).forEach((t) => { delete t[f]; }))]).not.toStrictEqual([f, all]);
    }
  });
});
