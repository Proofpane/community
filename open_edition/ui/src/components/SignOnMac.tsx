// Sign on my Mac: a step reviewed here is parked for a
// signer, a program on the owner's own Mac (ade_link.py sign); its request code, which this page computes from the
// payload it composed, is shown with the time left; once a signer hands the assertion back, and the payload the server
// holds is byte for byte the one parked, the signed step goes to the caller, which sends it to the route it sends a
// browser's assertion to. Cancel deletes the request, with any assertion it holds.

import { useEffect, useRef, useState } from "react";
import { Laptop, Loader2, X } from "lucide-react";
import { api } from "../api";
import { useOffline, useStore } from "../store";
import type { Prepared, Signed } from "../passkey";
import {
  collectFromMac, MARKED_MEANS, MARKED_WORDS, parkOnMac, POLL_EVERY_MS, SecondScreen, signedWords, type KnownPasskey,
  type OnMac, type SignedFacts,
} from "../secondScreen";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The account's passkeys as the server lists them (Account): the names and tiers the words beside a stored step use,
 *  read where `wanted`. Empty while they are read, or where they cannot be. */
export function useKnownPasskeys(wanted = true): KnownPasskey[] {
  const [keys, setKeys] = useState<KnownPasskey[]>([]);
  useEffect(() => {
    if (!wanted) return;
    let live = true;
    api<{ passkeys: KnownPasskey[] }>("/api/account/passkeys").then((r) => { if (live) setKeys(Array.isArray(r?.passkeys) ? r.passkeys : []); },
      () => { /* nothing said of the passkey */ });
    return () => { live = false; };
  }, [wanted]);
  return keys;
}

/** What signed a stored step, in the words the page may say (signedWords): what the marker means is a click away from
 *  the words that name it, and never apart from them. */
export function SignedBy({ facts, keys }: { facts: SignedFacts; keys: KnownPasskey[] }) {
  const [means, setMeans] = useState(false);
  const words = signedWords(facts, keys);
  if (!words) return null;
  return (
    <div className="text-[11px] text-faint">
      {words.line}
      {words.marked && (
        <>
          {words.line ? ", " : ""}{MARKED_WORDS}{" "}
          <button className="underline" aria-expanded={means} onClick={() => setMeans(!means)}>what this means</button>
          {means && <div>{MARKED_MEANS}</div>}
        </>
      )}
    </div>
  );
}

/** Whether the account has a signer paired, as the server lists them, asked once a step is reviewed (`wanted`): only
 *  then is Sign on my Mac offered. Where the list cannot be read, it is not. */
export function useSignerPaired(wanted: boolean): boolean {
  const [paired, setPaired] = useState(false);
  useEffect(() => {
    if (!wanted) return;
    let live = true;
    SecondScreen.signers().then((s) => { if (live) setPaired(Array.isArray(s?.signers) && s.signers.length > 0); }, () => { /* not offered */ });
    return () => { live = false; };
  }, [wanted]);
  return paired;
}

/** How long the list of signers may take before one press counts it unread, how soon one that could not be read is
 *  read again, and how long one read serves every card of the page. */
const SIGNERS_WAIT = 10_000;
const SIGNERS_AGAIN = 15_000;
const SIGNERS_FRESH = 60_000;

/** The page's read of the account's signers, shared by its cards: whether one is paired, null where the list could not
 *  be read (then the read is forgotten, so the next one asks again). */
let signersRead: { account: string; at: number; paired: Promise<boolean | null> } | null = null;

function readSigners(): Promise<boolean | null> {
  const account = useStore.getState().user?.id ?? "";
  if (signersRead && signersRead.account === account && Date.now() - signersRead.at < SIGNERS_FRESH) return signersRead.paired;
  const read = { account, at: Date.now(), paired: Promise.resolve<boolean | null>(null) };
  read.paired = SecondScreen.signers().then((s) => Array.isArray(s?.signers) && s.signers.length > 0, () => {
    if (signersRead === read) signersRead = null;
    return null;
  });
  signersRead = read;
  return read.paired;
}

/** Forgets the page's read of the signers: the Signers list read (a signer paired or revoked perhaps since), or a
 *  test's next case. */
export function forgetSigners() { signersRead = null; }

/** useSignerPaired as one press on Approve reads it (LinkInbox's PromptCard): null until the list comes back, then
 *  whether a signer is paired, and true where the list cannot be read or has not come within SIGNERS_WAIT; read once
 *  for the page's cards, not while the page is offline, again once it is back, and again SIGNERS_AGAIN after a read
 *  that failed, which the page forgot (so the cards read it again once, together). One press signs in the browser, which a session pinned to sign on the Mac refuses, so it is offered
 *  only where no signer is known to be paired: elsewhere Approve opens the review, which offers Sign on my Mac where it
 *  can. */
export function useSignerKnown(wanted: boolean): boolean | null {
  const offline = useOffline();
  const [paired, setPaired] = useState<boolean | null>(null);
  const [again, setAgain] = useState(0);
  useEffect(() => {
    if (!wanted || offline) return;
    let live = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const late = setTimeout(() => { if (live) setPaired((p) => p ?? true); }, SIGNERS_WAIT);
    void readSigners().then((p) => {
      clearTimeout(late);
      if (!live) return;
      setPaired(p ?? true);
      if (p === null) retry = setTimeout(() => setAgain((n) => n + 1), SIGNERS_AGAIN);
    });
    return () => { live = false; clearTimeout(late); clearTimeout(retry); };
  }, [wanted, offline, again]);
  return paired;
}

export function SignOnMac({ prepared, title, onSigned, onCancel }: {
  prepared: Prepared;
  /** a move's title, parked beside its payload as the page sends it beside the signature: the ADE's word on the Mac */
  title?: string;
  /** the step, signed: the caller sends it on, and says what came of it */
  onSigned: (signed: Signed) => Promise<void>;
  onCancel: () => void;
}) {
  const [parked, setParked] = useState<OnMac | null>(null);
  const [error, setError] = useState("");
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));
  const over = useRef(false);           // signed, failed, ended or cancelled: nothing more is asked or sent
  const signedTo = useRef(onSigned);    // the caller's latest, without starting the polling again for it
  signedTo.current = onSigned;

  useEffect(() => {
    let live = true;
    parkOnMac(prepared.payload, title).then((p) => { if (live) setParked(p); }, (e) => { if (live) { over.current = true; setError(message(e)); } });
    return () => { live = false; };
  }, [prepared, title]);

  useEffect(() => {
    if (!parked) return;
    const tick = async () => {
      const t = Math.floor(Date.now() / 1000);
      setNow(t);
      if (over.current) return;
      if (t >= parked.expires_at) {
        over.current = true;
        setError("This request's time ended before a signer signed it: review it again.");
        return;
      }
      try {
        const signed = await collectFromMac(prepared.payload, parked);
        if (signed && !over.current) {
          over.current = true;
          await signedTo.current(signed);
        }
      } catch (e) { over.current = true; setError(message(e)); }
    };
    const timer = setInterval(() => void tick(), POLL_EVERY_MS);
    return () => clearInterval(timer);
  }, [parked, prepared]);

  const cancel = async () => {
    over.current = true;
    if (parked) { try { await SecondScreen.cancel(parked.id); } catch { /* ended or cancelled already */ } }
    onCancel();
  };
  const left = parked ? Math.max(parked.expires_at - now, 0) : null;
  return (
    <div className="mt-1.5 rounded border border-line p-2 text-[12px]" role="group" aria-label="Sign on my Mac">
      {!parked && !error && <div className="flex items-center gap-1.5 text-muted"><Loader2 size={12} className="animate-spin" /> Parking it for your Mac</div>}
      {parked && (
        <>
          <div className="flex flex-wrap items-center gap-1.5">
            <Laptop size={13} className="text-muted" />
            <span>Request</span>
            <span className="font-mono text-[14px] text-fg" aria-label="Request code">{parked.code}</span>
          </div>
          <div className="mt-1">On your Mac, at a terminal of your own, run <code className="font-mono">ade_link.py sign</code> and type <span className="font-mono">{parked.code.slice(0, 4)}</span> when it asks for the code. Read the words it shows before you touch your key: it decodes them from the bytes your key signs.</div>
          {left !== null && !error && <div className="mt-1 text-[11px] text-faint">{left} seconds left</div>}
        </>
      )}
      {error && <div role="alert" className="mt-1 text-fail">{error}</div>}
      <button className="btn btn-sm mt-1.5" onClick={() => void cancel()}><X size={12} /> Cancel</button>
    </div>
  );
}
