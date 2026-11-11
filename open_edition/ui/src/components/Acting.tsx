// A press on a card of the
// workspace's page acts on the page at once. A dismissal closes its card, and a decision shows itself taken with the
// card's buttons disabled, while its request goes on; a refusal, or no answer within the press's time (ANSWER_WITHIN
// unless the press gives more), brings the card back as it was with the reason on it, so nothing shows as decided that
// the server did not take. A press is kept for the page, by the item it acts on, not by the card, until the server
// refuses it or, once the server took it, until its list has been read again: two presses before the page draws again,
// or a card drawn again while its press goes, send once; a request that went on after its card came back is still
// answered on it; and once its list is read again, its card is as the server says, whatever the card said. A check
// made before a press acts (the phone's of the person, presence.ts) is made before `act` is called, so a press that
// check refuses changes nothing on the card.
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Check, Loader2 } from "lucide-react";

/** How long a press waits for the server's answer before its card comes back, in milliseconds. */
export const ANSWER_WITHIN = 20_000;

/** What a card that came back for want of an answer in `ms` says: its request goes on, and its answer is said here. */
export function noAnswer(ms: number): string {
  return `The server did not answer in ${Math.round(ms / 1000)} seconds, so this is shown as it was; if it answers, `
    + "this says so.";
}

/** What a press says while an earlier press of its item, whose card came back, still waits for the server. */
export const STILL_GOING = "An earlier press of this is still on its way: its answer is said here when it comes.";

class NoAnswer extends Error {}

/** `sent`, or a refusal saying noAnswer(ms) where it has not settled within `ms`: the request itself is not stopped. */
export function inTime<T>(sent: Promise<T>, ms = ANSWER_WITHIN): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new NoAnswer(noAnswer(ms))), ms); });
  return Promise.race([sent, late]).finally(() => clearTimeout(timer));
}

const reason = (e: unknown) => (e instanceof Error ? e.message : String(e));

function without<V>(record: Readonly<Record<string, V>>, key: string): Readonly<Record<string, V>> {
  if (!(key in record)) return record;
  const rest = { ...record };
  delete rest[key];
  return rest;
}

/** A press kept for the page: what it decided, and whether the server took it; null while its card is back for want of
 *  an answer and its request still goes. */
type Kept = { said: string; sure: boolean } | null;

// The presses kept across the page, by the item each acts on: from the press until the server refuses it, or, once the
// server took it, until its list has been read again.
let going: Readonly<Record<string, Kept>> = {};
const listeners = new Set<() => void>();
function setGoing(key: string, now: Kept | undefined) {
  going = now === undefined ? without(going, key) : { ...going, [key]: now };
  listeners.forEach((listener) => listener());
}
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
const goingNow = () => going;

/** What a press may say of itself, beyond its words and its request. */
export interface Press {
  /** how long it waits for the server's answer before its card comes back (ANSWER_WITHIN) */
  within?: number;
  /** its words once the server took it, where they differ from what it decided ("Starting it", then "Started") */
  done?: string;
  /** what follows a refusal, the request having been answered (the list read again, as before) */
  refused?: () => Promise<unknown>;
}

/** The presses of the cards one component draws, each by the item it acts on. */
export interface Acts {
  /** what a press of the item decided, from this card or another showing the item: from the press, and its done words
   *  once the server took it, until its list has been read again; null where the card stands as it was */
  taken: (key: string) => string | null;
  /** whether the server took the item's press, its list not yet read again */
  sure: (key: string) => boolean;
  /** whether a press of any item whose key begins with `prefix` is kept, its card back or not */
  anyGoing: (prefix: string) => boolean;
  /** why a card's last press came back, or that an earlier press of its item still goes, until it is pressed again */
  failed: (key: string) => string;
  /** A card's press: `said` what it decided, `send` its request, `after` the read of its list again once the server
   *  took it. Nothing is sent while a press of the same item is kept. True when the server took it. */
  act: (key: string, said: string, send: () => Promise<unknown>, after: () => Promise<unknown>, press?: Press) => Promise<boolean>;
}

/** `elsewhere`: what a failure is told to where the card cannot say it, a refusal that comes once the card is no
 *  longer drawn, or a failure of the read of its list again once the server took it (the page's own error, where the
 *  card said one before). */
export function useActs(elsewhere?: (e: unknown) => void): Acts {
  const on = useSyncExternalStore(subscribe, goingNow, goingNow);
  const [failed, setFailed] = useState<Readonly<Record<string, string>>>({});
  const told = useRef(elsewhere);
  told.current = elsewhere;
  const drawn = useRef(true);
  useEffect(() => { drawn.current = true; return () => { drawn.current = false; }; }, []);
  const act = useCallback(async (key: string, said: string, send: () => Promise<unknown>, after: () => Promise<unknown>,
                                 press: Press = {}) => {
    if (key in going) {
      // its card shows it taken, or came back while it still goes: only the second is said
      if (going[key] === null) setFailed((f) => ({ ...f, [key]: STILL_GOING }));
      return false;
    }
    setGoing(key, { said, sure: false });
    setFailed((f) => without(f, key));
    const refused = (e: unknown) => {
      setGoing(key, undefined);
      setFailed((f) => ({ ...f, [key]: reason(e) }));
      if (!drawn.current) told.current?.(e);
      if (press.refused) void press.refused().catch(() => {});
    };
    const sent = send();
    try {
      await inTime(sent, press.within);
    } catch (e) {
      if (!(e instanceof NoAnswer)) { refused(e); return false; }
      // no answer in time: the card comes back as it was, saying so, while the request goes on; its answer is said
      setGoing(key, null);
      setFailed((f) => ({ ...f, [key]: e.message }));
      try { await sent; } catch (late) { refused(late); return false; }
    }
    // taken: said done until its list has been read again, whose word its card then shows, whether or not that read
    // went (one that failed is said as the page's error)
    setFailed((f) => without(f, key));
    setGoing(key, { said: press.done ?? said, sure: true });
    try { await after(); } catch (e) { told.current?.(e); } finally { setGoing(key, undefined); }
    return true;
  }, []);
  return useMemo(() => ({
    taken: (key: string) => on[key]?.said ?? null,
    sure: (key: string) => on[key]?.sure === true,
    anyGoing: (prefix: string) => Object.keys(on).some((k) => k.startsWith(prefix)),
    failed: (key: string) => failed[key] ?? "",
    act,
  }), [on, failed, act]);
}

/** One card's presses of one item (useActs with its key). */
export function useAct(key: string, elsewhere?: (e: unknown) => void): {
  taken: string | null; sure: boolean; failed: string;
  act: (said: string, send: () => Promise<unknown>, after: () => Promise<unknown>, press?: Press) => Promise<boolean>;
} {
  const acts = useActs(elsewhere);
  return { taken: acts.taken(key), sure: acts.sure(key), failed: acts.failed(key),
           act: (said, send, after, press) => acts.act(key, said, send, after, press) };
}

/** A card's decision as the card shows it from the press: its words, with a moving indicator until the server took it. */
export function TakenLine({ said, sure }: { said: string; sure: boolean }) {
  return (
    <div role="status" className="mt-1.5 flex items-center gap-1.5 text-[12px] text-muted">
      {sure ? <Check size={12} className="flex-none text-done" /> : <Loader2 size={12} className="flex-none animate-spin" />}
      <span>{said}{sure ? "" : " \u00b7 sending"}</span>
    </div>
  );
}

/** Why a card's press came back, on the card. */
export function CameBack({ why }: { why: string }) {
  return why ? <div role="alert" className="mt-1.5 text-[12px] text-fail">{why}</div> : null;
}
