// End-to-end encryption on the page (the account's keys, unlocking with a passkey or the recovery code, and
// opening what linked sessions sealed). This file is the interface the rest of the page uses: the types, the slots
// every sealed text is bound to, the hook with the tab's state, and the opener. The keys and their changes are in
// e2eKeys.ts, the primitives in e2eCrypto.ts. The keys unlocked live in this tab's memory only, as CryptoKeys that
// cannot be extracted, and are dropped on Lock, after 30 minutes without the person's own input, and at sign-out in
// every tab. The browser keeps, per account, the newest master key it opened (its id, no secret): the keys the server
// serves must lead back to it (e2eKeys.floorOf).
import { createElement, Fragment, useEffect, useMemo, useRef, type ReactNode } from "react";
import { create } from "zustand";
import { APP } from "./api";
import { useStore } from "./store";
import { x25519Here } from "./e2eCrypto";
import * as K from "./e2eKeys";
import { pageChallenge } from "./passkey";

/** Where the page stands: no keys on this account (encryption was never turned on), keys not unlocked in this tab
 *  (whether encryption is on or was turned off since: what was sealed stays sealed), keys unlocked, or a browser or
 *  app that cannot do it (no X25519 in its WebCrypto, or an app, where passkeys are not the server's). */
export type E2EState = "off" | "locked" | "unlocked" | "unsupported";

/** A sealed text opened: the plaintext object, or why it could not be opened (never shown in its place). */
export type Opened = { ok: true; value: Record<string, unknown> } | { ok: false; why: string };

/** Every sealed text begins so; a text that does not is in the clear. */
export const SEALED_PREFIX = "e2e1.";

export function isSealed(text: unknown): text is string {
  return typeof text === "string" && text.startsWith(SEALED_PREFIX);
}

/** The slots: what a sealed text is bound to, rebuilt from what the server serves beside it. Empty fields are empty
 *  text and numbers decimal, so a missing value never becomes "null" or "undefined". */
function field(v: string | number | null | undefined): string {
  return v === null || v === undefined ? "" : String(v);
}

function slot(account: string, linkId: string, parts: (string | number | null | undefined)[]): string {
  return ["proofpane-e2e/1", account, linkId, ...parts.map(field)].join("|");
}

/** An event to the owner: its kind, the mirror's word (owner, session, info) or none, the seq it refers to or none,
 *  and the client id the link gave it. */
export function eventSlot(account: string, linkId: string, kind: string, mirror: string | null | undefined,
                          ref: number | null | undefined, clientId: string): string {
  return slot(account, linkId, ["event", kind, mirror, ref, clientId]);
}

/** What a link's client reports of its own session with a heartbeat: its task list and background tasks, as one. */
export function reportedSlot(account: string, linkId: string): string {
  return slot(account, linkId, ["reported"]);
}

/** A History row: the session's id, and whether its machine withholds it (a withheld row seals its project alone). */
export function historySlot(account: string, linkId: string, sessionId: string, withheld: boolean): string {
  return slot(account, linkId, ["history", sessionId, withheld ? 1 : 0]);
}

/** A message of a session's conversation: its place in the list, the list's length, how many earlier ones were left
 *  out, who said it, and its times as served. */
export function talkSlot(account: string, linkId: string, sessionId: string, index: number, count: number,
                         earlier: number, who: string, at: number | null | undefined,
                         ms: number | null | undefined): string {
  return slot(account, linkId, ["talk", sessionId, index, count, earlier, who, at, ms]);
}

/** A conversation's key, made for one list a post-quantum account's link sends: the
 *  session's id, the list's length and how many earlier ones were left out. */
export function talkKeySlot(account: string, linkId: string, sessionId: string, count: number, earlier: number): string {
  return slot(account, linkId, ["talk-key", sessionId, count, earlier]);
}

export interface E2E {
  state: E2EState;
  /** One touch of an enrolled passkey or security key. Call it first thing in a click's handler: the browser's prompt
   *  comes with nothing awaited before it. */
  unlock(): Promise<void>;
  /** The keys opened from a touch made already, which gave its PRF outputs with a signature (K.signAndTouch: one press
   *  that approves and unlocks). */
  unlockFrom(t: K.Touch): Promise<void>;
  /** The recovery code, as it was shown (spaces and case do not matter). */
  unlockWithCode(code: string): Promise<void>;
  /** Drop the keys and every opened text. */
  lock(): void;
  /** Whether a text in the clear on a sealed path is one sent in the clear (onOf): encryption is on now (its newest
   *  statement is e2e-key), or the keys served are older than this browser saw of the account (K.rolledBack), whatever
   *  they say. The page marks by it both a text sent in the clear and the owner's commands kept in the clear. */
  on?: boolean;
  /** Why the keys were locked last, when it was not the person's Lock (the idle lock, a sign-out, a removed key). */
  why?: string;
  /** Rises at every lock and every unlock (not when an unlocked tab only follows a change made elsewhere): a text
   *  opened under another generation is dropped, even when a lock and an unlock both fall between two renders. */
  generation: number;
}

// ---- the tab's state ---------------------------------------------------------------------------------------------

/** Keys this tab made, waiting on their hold: what unlocks them; `from`, the master key the server said was current
 *  when the tab began to wait (null before any), to tell other keys taking effect from a turning-on cancelled; and
 *  `chains`, whether they lead back to the keys this tab holds (the upgrade), which they are then checked against as a
 *  tab following a change is (keys from turning on, or starting again, are this tab's own and lead back to nothing). */
export type Waiting = K.Via & { from: string | null; chains: boolean };

export const IDLE_MS = 30 * 60 * 1000;
export const POLL_MS = 60 * 1000;
export const CHANNEL = "proofpane-e2e";
export const IDLE_WORDS = "Locked after 30 minutes without your input.";
export const SIGNED_OUT_WORDS = "Locked at sign-out.";

interface E2EStore {
  /** null until this browser's X25519 has been tried */
  supported: boolean | null;
  account: string | null;
  view: K.E2EView | null;
  ring: K.Ring | null;
  why: string;
  /** whether `why` is a view's verdict of being older than this browser saw (K.rolledBack, or its refusal K.OlderView),
   *  recorded as the reason is set: such a reason is judged again from each view read (judgedAgain), and no other is */
  verdict: boolean;
  /** rises at every lock and every unlock (E2E.generation) */
  generation: number;
  /** the parts of the view the ring was last opened from, to open it again only when they change */
  opened: string;
  /** what unlocks keys this tab made (turning encryption on) that wait on their hold: opened, as an unlock is, once
   *  the server serves them; dropped as the keys are */
  waiting: Waiting | null;
  /** Read the server's view for `account` and, unlocked, follow what changed with the key held (no touch): when the keys
   *  or the statements served changed since the ring was opened, or the ring is below this browser's floor. */
  load(account?: string): Promise<void>;
  /** The keys this tab holds checked again at once, when a change was refused as made from keys older than this
   *  browser's floor (BEHIND), not at the next read: the view read again and followed, the floor's master key among
   *  those it must lead back to; locked, saying why, when it does not. (Another tab raising the floor, a `storage`
   *  event, reads it again so too.) */
  recheck(): Promise<void>;
  unlock(): Promise<void>;
  unlockFrom(t: K.Touch): Promise<void>;
  unlockWithCode(code: string): Promise<void>;
  /** `verdict`: `why` is a view's verdict of being older (the store's own locks say so; any other lock's reason is not) */
  lock(why?: string, verdict?: boolean): void;
  /** Keys unlocked another way (turning encryption on): held as if unlocked here. */
  adopt(ring: K.Ring): void;
  /** Keys this tab made, waiting on their hold: unlocked here once they take effect, with no touch. `chains`: they lead
   *  back to the keys held (the upgrade). */
  awaitKeys(via: K.Via, chains?: boolean): void;
  /** This browser removed a passkey from the account (Account, while encryption is off): remembered, so it leaves the
   *  next master key whatever the server lists later (K.rememberRemoved). */
  removed(passkeyId: string): void;
  /** Signed out, or another account: nothing of the last account is kept. */
  reset(account: string | null, why?: string): void;
}

/** What the ring is opened from, to open it again when it changes: the keys, the statements (a change made elsewhere
 *  states a key the ring must hold before it makes one), and whether the server serves them post-quantum (keys served
 *  classical where this browser saw them post-quantum are refused, the same keys too: K.rolledBack). */
const keysOf = (v: K.E2EView) => JSON.stringify([v.mk_id, v.grants, v.chain, v.roster, v.content_keys, v.credentials, v.recovery, v.statements, v.pq]);
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** A lock's reason that was a view's verdict of being older than this browser saw (no keys, classical where it saw them
 *  post-quantum, fewer statements: K.rolledBack), judged again from `view`, read now: that view's verdict where it gives
 *  one. Else none, unless the reason is the words for keys put back and an unlock could still give them: the
 *  view names a master key, and this browser's floor (K.floorOf) names others, which an unlock walks the chain back to
 *  and which the page cannot walk without a key (a server put back to an older master key, whose statements a link sent
 *  back in). That reason stays until an unlock (null). The classical words are a view's alone, never the chain's. */
function judgedAgain(account: string, view: K.E2EView, why: string): { why: string; verdict: boolean } | null {
  const older = K.rolledBack(account, view);
  if (older) return { why: older, verdict: true };
  const floor = K.floorOf(account);
  if (why === K.ROLLED_BACK && view.mk_id && floor.length && !floor.includes(view.mk_id)) return null;
  return { why: "", verdict: false };
}

export const useE2EStore = create<E2EStore>((set, get) => {
  /** The reads of the keys begun (readKeys: load's, its read once more, an unlock's), and the number of the read whose
   *  view the store holds (written). A read's view is set unless a read begun after it has set one first (read), so a
   *  read that comes back after a later one, held on its way back while a change was made elsewhere, never puts an
   *  older view back; a read that fails sets nothing and holds no other back. A reset (a sign-out, another account)
   *  counts as a read begun whose view is none, so no read begun before it sets one after it. */
  let reads = 0;
  let written = 0;
  /** One read of the keys, numbered as it begins: its number, and what the server answered (null where it failed). */
  const readKeys = async (): Promise<{ n: number; view: K.E2EView | null }> => {
    const n = ++reads;
    return { n, view: await K.E2EApi.view().catch(() => null) };
  };
  /** The view read now by read `n` (load's, its read once more, an unlock's: fresh), set unless a read begun after `n`
   *  has set one already, or a reset came after it: a lock's reason that was a view's verdict of being older is judged
   *  again from it (judgedAgain), whichever read it is, so what the pane says, and whether it asks trust, follow the view
   *  as it is and the floor as it is now. Whether it was set. */
  const read = (account: string, n: number, view: K.E2EView): boolean => {
    if (n <= written) return false;
    written = n;
    const { why, verdict } = get();
    const again = why && verdict ? judgedAgain(account, view, why) : null;
    set({ view, ...(again ?? {}) });
    return true;
  };
  /** The keys the unlock opens, read once the touch or the code is in: a change made elsewhere since the view was last
   *  read (a rotation, a revocation) is opened, not an older state of the keys. Where this read is not set (a read begun
   *  after it set a view first) or fails, the view the store holds stands for it (not `had`: a read answered while the
   *  touch was given may have set a newer), so a view that was not set is never opened; where the store holds none (a
   *  sign-out and a sign-in again since), `had`, which the unlock's hold then puts away. Where the account changed
   *  meanwhile (a sign-out, another account), `had` too, and nothing read now is set or handed on. A view a read sets
   *  while the keys are being opened is followed once they are held (caughtUp). */
  const fresh = async (account: string, had: K.E2EView): Promise<K.E2EView> => {
    const { n, view } = await readKeys();
    if (get().account !== account) return had;
    if (view && read(account, n, view)) return view;
    return get().view ?? had;
  };
  /** Once an unlock has held the keys it opened: where a read set a view of other keys while they were being opened
   *  (its load found no keys held to follow from), the account's keys are read again and followed from those held. */
  const caughtUp = async () => {
    const { ring, view } = get();
    if (ring && view && keysOf(view) !== get().opened) await get().load();
  };
  /** Why the keys are not held, from `e` (following a change, or opening keys this tab made, failed): its words,
   *  recorded as a view's verdict of being older where it is one (K.OlderView); the tab locked where it holds keys. */
  const refused = (e: unknown) => {
    const verdict = e instanceof K.OlderView;
    if (get().ring) get().lock(message(e), verdict);
    else set({ why: message(e), verdict });
  };
  /** The ring held, unless the tab locked or signed out since `generation`: an unlock (no ring before) is a new
   *  generation, following a change with the keys held is not. This browser's floor is raised to its master key. */
  const hold = (account: string, generation: number, view: K.E2EView, ring: K.Ring) => {
    if (get().account !== account || get().generation !== generation) {
      drop(ring);
      return;
    }
    const before = get().ring;
    // what unlocks the tab changed (the keys it made took effect in place of those it held): a new generation, as an
    // unlock is, so every text is opened again with the keys it holds now; following a change with the same key is not
    set({ ring, why: "", verdict: false, opened: keysOf(view), generation: before && before.via === ring.via ? generation : generation + 1 });
    if (before && before !== ring) K.forgetRing(before, before.via !== ring.via);
    void K.raiseFloor(account, ring);
  };
  /** A ring opened but not held (the tab locked, signed out or moved on meanwhile): its keys put away, and what unlocks
   *  it unless the ring the tab holds, or the keys it waits on, still use it. */
  const drop = (ring: K.Ring) => {
    const { ring: held, waiting } = get();
    if (held === ring) return;
    K.forgetRing(ring, held?.via !== ring.via && waiting !== ring.via);
  };
  const waitingKey = (w: Waiting) => (w.kind === "credential" ? w.ck : w.rk);
  /** The keys this tab waited on no longer waited for (taken effect and held, cancelled, refused or replaced): what
   *  unlocks them put away, unless the ring held now is unlocked by it. */
  const dropWaiting = () => {
    const { waiting, ring } = get();
    if (!waiting) return;
    if (waiting !== ring?.via) K.forgetPriv(waitingKey(waiting));
    set({ waiting: null });
  };
  /** Every post-quantum key the tab held put away (zeroed at each lock path), but those `keep` still holds. */
  const forgetHeld = (keep: K.Ring | null = null) => {
    const { ring, waiting } = get();
    if (ring && ring !== keep) K.forgetRing(ring, ring.via !== keep?.via);
    if (waiting && waiting !== ring?.via && waiting !== keep?.via) K.forgetPriv(waitingKey(waiting));
  };
  /** The keys opened from a touch's PRF outputs, unlock's own or one made with a signature (unlockFrom), from the keys
   *  read once the touch is in (fresh), held where no lock or other account came since `generation` was read. */
  const openFrom = async (t: K.Touch, view: K.E2EView, account: string, generation: number) => {
    const now = await fresh(account, view);
    // the touch's output for the salt's hash, where it gave one, opens a record its first output does not (K.unlockAllowed)
    hold(account, generation, now, await K.unlockWithPrf(now, account, t.credentialId, t.prf, K.floorOf(account), t.second));
    await caughtUp();
  };
  return {
    supported: null,
    account: null,
    view: null,
    ring: null,
    why: "",
    verdict: false,
    generation: 0,
    opened: "",
    waiting: null,

    async load(account = get().account ?? undefined) {
      if (!account) return;
      if (account !== get().account) get().reset(account);
      const { n, view } = await readKeys();
      if (!view) return;                          // the next poll tries again
      if (get().account !== account) return;
      // a lock's reason that was a view's verdict of being older is judged again from this view (read); a reason no view
      // gives (keys that do not lead back to those held, a key removed) stays until an unlock. Where a read begun after
      // this one (another load, an unlock's) has set a view first, that read answers for the keys: this one sets
      // nothing, and opens, follows and locks nothing
      if (!read(account, n, view)) return;
      const waiting = get().waiting;
      // keys this tab made that took effect are opened with what it made, whether or not it holds older keys: they are
      // not followed but replace those (a new generation, hold). The upgrade's lead back to the keys held, and to this
      // browser's floor; keys from turning on or starting again are this tab's own, checked as an unlock checks them.
      if (waiting && view.mk_id === waiting.anchor) {
        const generation = get().generation;
        const held = get().ring;
        try {
          hold(account, generation, view, await K.openRing(view, account, waiting, waiting.chains && held ? [[held.master.id], K.floorOf(account)] : []));
        } catch (e) {
          if (get().waiting === waiting) refused(e);
        }
        if (get().waiting === waiting) dropWaiting();
        return;
      }
      if (waiting && !get().ring) {
        if (view.pending?.change === "turn-on") {
          // a turning-on waits: this tab's, or another put in its place, which the pane says (K.turnOnInPlace); the
          // tab waits on, to say what came of it
        } else if (view.mk_id !== waiting.from) {
          dropWaiting();                          // other keys took effect, not those this tab made
          set({ why: K.KEYS_MADE_ELSEWHERE, verdict: false });
        } else {
          dropWaiting();                          // cancelled, or refused as its hold ended: a touch unlocks, as ever
        }
        return;
      }
      // with keys held, nothing waits any more and the keys this tab made did not take effect (the upgrade cancelled,
      // refused as its hold ended, or replaced by a change that took effect since): what would have unlocked them goes
      if (waiting && !view.pending) dropWaiting();
      // followed when what it was opened from changed, and also when the server serves exactly that but this browser's
      // floor rose past the ring (another tab followed a revocation): the floor is among what it must lead back to
      const ring = get().ring;
      if (!ring || (keysOf(view) === get().opened && K.atFloor(ring))) return;
      const generation = get().generation;
      /** The keys followed to from `seen`, read `m`: held, and `seen` set as the view, while the tab holds those it
       *  followed from and no read begun after `m` has set a view; else put away, since another read moved the tab on
       *  or answers for the keys (its own follow is then the one held). */
      const followed = (next: K.Ring, seen: K.E2EView, m: number) => {
        if (get().ring !== ring || written > m) return drop(next);
        if (seen !== view) read(account, m, seen);
        hold(account, generation, seen, next);
      };
      try {
        followed(await K.follow(ring, view, K.floorOf(account)), view, n);
      } catch (e) {
        // read once more before locking: what was read may have been taken while a change was being made
        const { n: m, view: again } = await readKeys();
        if (again && get().ring === ring && keysOf(again) !== keysOf(view)) {
          try {
            followed(await K.follow(ring, again, K.floorOf(account)), again, m);
            return;
          } catch {
            // as the first read: locked
          }
        }
        // locked, unless a read begun after this load's has set a view since: that read answers for the keys
        if (get().ring === ring && written === n) refused(e);
      }
    },

    async recheck() {
      if (get().ring) await get().load();
    },

    async unlock() {
      const { view, account, generation } = get();
      if (!view || !account) throw new Error(K.STILL_LOADING);
      // the touch comes first, with nothing awaited before it (K.prfTouch)
      const allow = K.unlockAllowed(view);
      if (!allow.length) throw new Error("No passkey of this account unlocks encryption: use the recovery code.");
      // a second output not given is no refusal: the record is then tried with the first alone
      const t = await K.prfTouch({ challenge: pageChallenge(), allow, secondOptional: true, keys: K.securityKeyIds(view) });
      await openFrom(t, view, account, generation);
    },

    async unlockFrom(t: K.Touch) {
      const { view, account, generation } = get();
      if (!view || !account) {
        t.prf.fill(0);
        t.second?.fill(0);
        throw new Error(K.STILL_LOADING);
      }
      await openFrom(t, view, account, generation);
    },

    async unlockWithCode(code: string) {
      const { view, account, generation } = get();
      if (!view || !account) throw new Error(K.STILL_LOADING);
      const now = await fresh(account, view);
      hold(account, generation, now, await K.unlockWithCode(now, account, code, K.floorOf(account)));
      await caughtUp();
    },

    lock(why = "", verdict = false) {
      forgetHeld();
      set({ ring: null, waiting: null, why, verdict, generation: get().generation + 1, opened: "" });
    },

    adopt(ring: K.Ring) {
      // the next read opens it again from what it reads
      forgetHeld(ring);
      set({ ring, waiting: null, why: "", verdict: false, opened: "", generation: get().generation + 1 });
      void K.raiseFloor(ring.account, ring);
    },

    awaitKeys(via: K.Via, chains = false) {
      dropWaiting();                              // keys it waited on before are waited on no more
      set({ waiting: { ...via, from: get().view?.mk_id ?? null, chains } });
    },

    removed(passkeyId: string) {
      const account = get().account;
      if (account) K.rememberRemoved(account, get().view, passkeyId);
    },

    reset(account: string | null, why = "") {
      forgetHeld();
      K.forgetUnlockHashes();                     // the last account's salts' hashes too (K.primeUnlock)
      written = ++reads;                          // a read begun before it answers for another account, or for none
      set({ account, view: null, ring: null, waiting: null, why, verdict: false, generation: get().generation + 1, opened: "" });
    },
  };
});

/** The tab's state, from the store: an app cannot do it; nor a browser without X25519. */
export function stateOf(s: Pick<E2EStore, "supported" | "ring" | "view">): E2EState {
  if (APP || s.supported === false) return "unsupported";
  if (s.ring) return "unlocked";
  return K.hasKeys(s.view) ? "locked" : "off";
}

/** Whether encryption is on for what the page marks (E2E.on): as the view says (its newest statement is e2e-key), or
 *  where the view is older than this browser saw of the account (K.rolledBack: no keys where it saw them, classical
 *  where it saw them post-quantum, fewer statements), whatever the view says. A server put back to a copy from before
 *  encryption says it is off; a text in the clear on a sealed path is still one sent in the clear, and marked. What this
 *  browser kept of the account decides it, as it decides the Encryption pane's warning (K.rolledBack, read now). */
export function onOf(s: Pick<E2EStore, "account" | "view">): boolean {
  if (!s.view) return false;
  return !!s.view.on || (!!s.account && K.rolledBack(s.account, s.view) !== null);
}

/** The page's encryption state and its actions. */
export function useE2E(): E2E {
  const state = useE2EStore(stateOf);
  const on = useE2EStore(onOf);
  const why = useE2EStore((s) => s.why);
  const generation = useE2EStore((s) => s.generation);
  return useMemo(() => ({
    state, on, why, generation,
    unlock: () => useE2EStore.getState().unlock(),
    unlockFrom: (t: K.Touch) => useE2EStore.getState().unlockFrom(t),
    unlockWithCode: (code: string) => useE2EStore.getState().unlockWithCode(code),
    lock: () => useE2EStore.getState().lock(),
  }), [state, on, why, generation]);
}

/** Open a sealed text bound to `slot`, with the content keys unlocked in this tab. Nothing opened is given out once the
 *  tab has locked (or locked and unlocked again) meanwhile. */
export async function openSealed(envelope: string, slotText: string, talk?: K.TalkRef): Promise<Opened> {
  const { ring, generation } = useE2EStore.getState();
  if (!ring) return { ok: false, why: K.LOCKED };
  // a conversation's message through what its conversation holds (K.openTalkText); any other text by its form
  const open = (r: K.Ring) => (talk ? K.openTalkText(r, talk, envelope, slotText) : K.openWith(r, envelope, slotText));
  let opened = await open(ring);
  // the tab followed a change meanwhile and put away the keys it was opened with: once more, with those it holds now
  const now = useE2EStore.getState();
  if (!opened.ok && now.ring && now.ring !== ring) opened = await open(now.ring);
  if (useE2EStore.getState().generation !== generation) return { ok: false, why: K.LOCKED };
  return opened;
}

// ---- the provider: what keeps the state true for the tab ---------------------------------------------------------

/** Whether an event is the person's own input (the browser's, never a page's script): only that keeps the keys
 *  unlocked. Kept on an object so a test can stand in for the browser's isTrusted. */
export const input = {
  isPerson: (e: Event) => e.isTrusted,
  events: ["pointerdown", "keydown", "wheel", "touchstart"] as const,
};

/** At the page's root: reads the account's keys when signed in and every minute after (so an unlocked tab follows a
 *  rotation or a revocation made elsewhere), locks after IDLE_MS without the person's own input, and locks at sign-out
 *  in this tab and, through a BroadcastChannel, in every other. In an app it does nothing: passkeys are the browser's. */
export function E2EProvider({ children }: { children?: ReactNode }) {
  const userId = useStore((s) => s.user?.id ?? null);
  const unlocked = useE2EStore((s) => !!s.ring);
  const channel = useRef<BroadcastChannel | null>(null);
  const signedIn = useRef<string | null>(null);

  useEffect(() => {
    if (APP) return;
    void x25519Here().then((ok) => useE2EStore.setState({ supported: ok }));
    if (typeof BroadcastChannel === "undefined") return;
    const ch = new BroadcastChannel(CHANNEL);
    ch.onmessage = (m: MessageEvent) => {
      if (m.data && (m.data as { type?: unknown }).type === "lock") useE2EStore.getState().lock(SIGNED_OUT_WORDS);
    };
    channel.current = ch;
    return () => { ch.close(); channel.current = null; };
  }, []);

  useEffect(() => {
    if (APP) return;
    const before = signedIn.current;
    signedIn.current = userId;
    if (!userId) {
      // only a sign-out locks the other tabs: a page that opens signed out says nothing
      if (before) {
        useE2EStore.getState().reset(null, SIGNED_OUT_WORDS);
        channel.current?.postMessage({ type: "lock" });
      }
      return;
    }
    void useE2EStore.getState().load(userId);
    const poll = () => { if (document.visibilityState === "visible") void useE2EStore.getState().load(userId); };
    const t = setInterval(poll, POLL_MS);
    window.addEventListener("focus", poll);
    // another tab raised this browser's floor (it followed a revocation, or opened newer keys): this tab's keys are
    // checked against it at once, not at its next read, so no change is made from keys a passkey removed since opens
    const key = K.floorKey(userId);
    const floor = (e: StorageEvent) => { if (e.key === key || e.key === null) void useE2EStore.getState().load(userId); };
    window.addEventListener("storage", floor);
    return () => { clearInterval(t); window.removeEventListener("focus", poll); window.removeEventListener("storage", floor); };
  }, [userId]);

  useEffect(() => {
    if (APP || !unlocked) return;
    let last = Date.now();
    const mark = (e: Event) => { if (input.isPerson(e)) last = Date.now(); };
    const check = () => { if (Date.now() - last >= IDLE_MS) useE2EStore.getState().lock(IDLE_WORDS); };
    for (const name of input.events) window.addEventListener(name, mark, { capture: true, passive: true });
    const t = setInterval(check, 15000);
    document.addEventListener("visibilitychange", check);
    return () => {
      for (const name of input.events) window.removeEventListener(name, mark, { capture: true });
      clearInterval(t);
      document.removeEventListener("visibilitychange", check);
    };
  }, [unlocked]);

  return createElement(Fragment, null, children);
}
