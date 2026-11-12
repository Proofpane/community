// The signing window on a link's card: opened with one touch of the owner's passkey for 5 or 15 minutes, in which this
// tab signs their instructions to that one session without their passkey; or the window open on it, here or in another
// tab or on another device, with End now.
import { useEffect, useRef, useState } from "react";
import { KeyRound, Loader2, Timer } from "lucide-react";
import type { LinkView } from "../api";
import { useStore } from "../store";
import { clock } from "../when";
import { pinCheck, PinNotice, type PinWarning } from "./Channel";
import {
  CLOCK_WORDS, endWindow, openWindow, preparedStale, prepareWindow, windowOf, WINDOW_FEATURE, WINDOW_LIVES,
  dropWindow, type PreparedWindow, type WindowLife, type WindowState,
} from "../signWindow";

/** Where the last length chosen on this device is kept: the next window offers it first. */
const LIFE_KEY = "pp-sign-window-life";

export const HIGH_SECURITY_WORDS = "High security is on: every command takes your hardware key.";
export const NO_BROWSER_WORDS = "This browser cannot sign here.";
export const noClientWords = (name: string) =>
  `${name}'s client has not said it takes signing windows, so each instruction to it takes your passkey. An older `
  + "client says it once updated (copy the newest ade_link.py to ~/.proofpane-ade/ on its machine); a client that has "
  + "not pinned your passkeys says it once it pins them (ade_link.py pin); a client pinned for the second screen never "
  + "says it.";

/** What the owner reads before their passkey is asked to open a window. */
export const windowWords = (name: string, minutes: number) =>
  `For the next ${minutes} minutes, instructions you send to ${name} from this tab are signed by this tab without your `
  + `passkey. Whoever controls this page in that time can instruct ${name} without your touch. When the window ends, or `
  + `when you end it, instructions ${name} has not taken yet are dropped. Answers to its questions and to its held `
  + "commands, resumes, moves, proposals, and everything while high security is on still take your passkey. You can end "
  + "the window at any time, from any page of your account.";

/** "End now", with how many instructions the session has not taken that it drops. */
export const endNowLabel = (untaken: number) =>
  `End now (drops the ${untaken} not yet taken)`;

function lastLife(): WindowLife {
  try {
    const kept = Number(localStorage.getItem(LIFE_KEY));
    return (WINDOW_LIVES as readonly number[]).includes(kept) ? kept as WindowLife : 300;
  } catch {
    return 300;
  }
}

const left = (end: number) => {
  const s = Math.max(0, end - Math.floor(Date.now() / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

/** The window part of a link's card: `signs`, whether this browser signs here (signsHere). */
export function SigningWindow({ l, signs }: { l: LinkView; signs: boolean }) {
  const refresh = useStore((s) => s.refreshLinks);
  const [, tick] = useState(0);
  const [mine, setMine] = useState<WindowState | null>(() => windowOf(l.id));
  const there = l.delegation ?? null;
  // the links list said this tab's window is open: once it no longer does, the window ended there
  const listed = useRef(false);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!mine && !there) return;
    const t = setInterval(() => { setMine(windowOf(l.id)); tick((n) => n + 1); }, 1000);
    return () => clearInterval(t);
  }, [l.id, mine, there]);
  useEffect(() => {
    if (!mine) { listed.current = false; return; }
    if (there?.digest === mine.digest) listed.current = true;
    else if (there || listed.current) { dropWindow(l.id); setMine(null); }    // replaced, or ended at the server
  }, [l.id, mine, there]);
  const end = async (digest: string) => {
    setError("");
    try {
      await endWindow(l.id, digest);
    } catch (e) {
      setError(`This tab can no longer sign under the window, but the server did not hear its end (${(e as Error).message}): `
        + "end it again, or it ends by itself.");
    }
    setMine(windowOf(l.id));
    await refresh();
  };
  if (mine) {
    const untaken = there?.digest === mine.digest ? there.untaken : null;
    return (
      <div role="status" aria-label={`Signing window for ${l.name}`} className="mt-2 rounded-md border border-wait/50 bg-wait/[0.04] p-2 text-[12px]">
        <div><Timer size={12} className="inline" /> A signing window is open for {l.name}: {left(mine.end)} left by this device's clock, until {clock(mine.end)}.
          {" "}{mine.n} {mine.n === 1 ? "instruction" : "instructions"} sent in it{untaken === null ? "" : `, ${untaken} not taken yet`}.</div>
        <button className="btn btn-sm btn-danger mt-1.5" onClick={() => void end(mine.digest)}>{endNowLabel(untaken ?? 0)}</button>
        {error && <div role="alert" className="mt-1 text-[12px] text-fail">{error}</div>}
      </div>
    );
  }
  if (there) {
    return (
      <div role="status" aria-label={`Signing window for ${l.name}`} className="mt-2 rounded-md border border-wait/50 bg-wait/[0.04] p-2 text-[12px]">
        <div><Timer size={12} className="inline" /> A signing window is open for {l.name} in another tab or on another device, until {clock(there.expires_at)} by the server's clock:
          {" "}{there.last_n} sent in it, {there.untaken} not taken yet. This tab cannot sign under it.</div>
        <button className="btn btn-sm btn-danger mt-1.5" onClick={() => void end(there.digest)}>{endNowLabel(there.untaken)}</button>
        {error && <div role="alert" className="mt-1 text-[12px] text-fail">{error}</div>}
      </div>
    );
  }
  const reason = !signs ? NO_BROWSER_WORDS : !(l.features ?? []).includes(WINDOW_FEATURE) ? noClientWords(l.name) : null;
  if (!opening) {
    return (
      <div className="mt-2">
        <button className="btn btn-sm" disabled={reason !== null} onClick={() => { setError(""); setOpening(true); }}><Timer size={12} /> Open a signing window</button>
        {reason && <div className="mt-1 text-[11px] text-muted">{reason}</div>}
      </div>
    );
  }
  return <Opener l={l} onDone={async (w) => { setMine(w); setOpening(false); await refresh(); }} onCancel={() => setOpening(false)} />;
}

/** Choose 5 or 15 minutes, read what it means, and open: the window is prepared as the length is chosen (its key made,
 *  its nonce asked, its delegation composed), so the click on Open reaches the passkey's prompt with nothing awaited. */
function Opener({ l, onDone, onCancel }: { l: LinkView; onDone: (w: WindowState) => Promise<void>; onCancel: () => void }) {
  const [life, setLife] = useState<WindowLife>(lastLife);
  const [prepared, setPrepared] = useState<PreparedWindow | null>(null);
  const [warning, setWarning] = useState<PinWarning | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const asked = useRef(0);
  // `note`: what is said while it is made again (a stale preparation's), kept until the next press
  const prepare = async (length: WindowLife, note = "") => {
    const mineAsk = ++asked.current;
    setPrepared(null); setError(note);
    try {
      // with it, which passkeys the session refuses, said before the touch as a review says it: a window one of them
      // opens sends nothing the session takes
      const [p, w] = await Promise.all([prepareWindow(l.id, l.fingerprint ?? "", length), pinCheck(l.id)]);
      if (mineAsk !== asked.current) return;               // a length changed since: that one's preparation counts
      if (p.prepared.allow.length) { setError(HIGH_SECURITY_WORDS); return; }
      setWarning(w);
      setPrepared(p);
    } catch (e) { if (mineAsk === asked.current) setError((e as Error).message || CLOCK_WORDS); }
  };
  useEffect(() => { void prepare(life); }, [life]);       // eslint-disable-line react-hooks/exhaustive-deps
  const open = async () => {
    if (!prepared || busy) return;
    if (preparedStale(prepared)) {
      void prepare(life, "Its preparation was more than half a minute old: it was made again so that the window lasts "
        + "its whole length. Open it now.");
      return;
    }
    setBusy(true); setError("");
    try {
      const w = await openWindow(prepared);                  // the prompt first, nothing awaited before it
      try { localStorage.setItem(LIFE_KEY, String(life)); } catch { /* this device only */ }
      await onDone(w);
    } catch (e) {
      setError((e as Error).message);
      void prepare(life);                                    // a nonce is spent or refused: a fresh one for the next try
    } finally { setBusy(false); }
  };
  return (
    <div role="group" aria-label={`Opening a signing window for ${l.name}`} className="mt-2 rounded-md border border-line p-2 text-[12px]">
      <select aria-label="Window length" className="input h-7 w-auto text-[12px]" value={life}
        onChange={(e) => setLife(Number(e.target.value) as WindowLife)}>
        {WINDOW_LIVES.map((s) => <option key={s} value={s}>{s / 60} minutes</option>)}
      </select>
      <div className="mt-1.5 text-fg-dim">{windowWords(l.name, life / 60)}</div>
      {prepared && warning && <PinNotice w={warning} name={l.name} underWindow />}
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        <button className="btn btn-sm btn-primary" disabled={!prepared || busy} onClick={() => void open()}>
          {busy || (!prepared && !error) ? <Loader2 size={12} className="animate-spin" /> : <KeyRound size={12} />} Open the window with your passkey
        </button>
        <button className="btn btn-sm" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
      {error && <div role="alert" className="mt-1 text-[12px] text-fail">{error}</div>}
    </div>
  );
}
