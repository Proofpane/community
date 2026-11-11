import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { LinkView } from "../api";
import { heardThrough, LinkCard } from "./Channel";

afterEach(() => cleanup());

// The card says how a session is heard, so that "silent" keeps its meaning.
// Through its hook: heard each time its hook runs. Through its client alone: a cloud session runs no hook of the ADE's,
// and is heard while its client's background heartbeat runs, which ends with its session; or, with no such heartbeat,
// only when its session calls its client, when not being heard does not mean the session stopped.
const HOOK = "through its hook";
const KEEPER = "through its client alone, which keeps it heard while its session runs";
const CLIENT = "through its client alone, when its session calls it";

const link = (over: Partial<LinkView> & Record<string, unknown> = {}): LinkView => ({
  id: "L1", name: "integrator", kind: "cloud", session_ref: null, interval_min: 30, created_at: 1, last_seen_at: null,
  last_heartbeat_at: null, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0, checkpoint_at: null,
  events: 0, last_event: null, commands: [], stopped_at: null, ...over,
} as LinkView);

const card = (over: Partial<LinkView> & Record<string, unknown>) =>
  render(<LinkCard l={link({ last_heard_at: Math.floor(Date.now() / 1000) - 120, ...over })} questions={[]}
    onTicket={() => {}} />);

const said = () => [HOOK, KEEPER, CLIENT].filter((words) => screen.queryByText(words) !== null);

describe("a link's card says how it is heard", () => {
  it("through its hook, for a link whose hook was heard", () => {
    card({ kind: "local", interval_min: 15, heard_through: "hook" });
    expect(said()).toEqual([HOOK]);
  });

  it("through its client alone while its session runs, for a cloud link its client's heartbeat keeps heard", () => {
    card({ heard_through: "keeper" });
    expect(said()).toEqual([KEEPER]);
  });

  it("through its client alone when its session calls it, with no heartbeat of its client's", () => {
    card({ heard_through: "client" });
    expect(said()).toEqual([CLIENT]);
  });

  it("and beside its silence, so that the reader knows what the silence means", () => {
    card({ heard_through: "hook", kind: "local", interval_min: 15, silent: true });
    expect(said()).toEqual([HOOK]);
    expect(screen.queryByText("silent for more than two of its 15-minute intervals")).not.toBeNull();
  });

  // a keeper's last beat heard, silent since: its session ended, or its container restarted and
  // it has not called its client since, which starts the keeper again; the words say both
  it("says what a silence after its client's heartbeat can mean", () => {
    card({ heard_through: "keeper", silent: true });
    expect(said()).toEqual([KEEPER]);
    expect(screen.queryByText("silent for more than two of its 30-minute intervals: its session ended, or its "
      + "container restarted and it has not called its client since")).not.toBeNull();
  });

  // nothing said for a link not heard from yet, and no words for a
  // word the page holds none of, whatever an object holds under that name
  it("says nothing of it for a link not heard from yet", () => {
    card({ last_heard_at: null, last_heartbeat_at: null, heard_through: "hook" });
    expect(said()).toEqual([]);
  });

  it("holds words only for the three it knows", () => {
    for (const word of ["constructor", "toString", "__proto__"]) {
      expect(heardThrough({ heard_through: word } as unknown as Pick<LinkView, "heard_through">)).toBeNull();
    }
  });

  it("says nothing of it where the server says nothing, or something this page does not know", () => {
    card({});
    expect(said()).toEqual([]);
    cleanup();
    card({ heard_through: "anything else" } as unknown as Partial<LinkView>);   // a word of a later server's
    expect(said()).toEqual([]);
  });
});
