// Account's Signers: the programs on the
// owner's own machines that sign the owner's steps there (ade_link.py sign), each paired with a ticket made here with
// the account's confirmation, listed with when it was paired and last seen, and revoked here; and every request parked
// for a signer that has not ended, with where the server says it was parked from, the code beside those this page
// parked, and Cancel all. What a signer is told and does is the server's (ade/signers.py) and the client's.

import { useCallback, useEffect, useState } from "react";
import { Laptop, Trash2, X } from "lucide-react";
import { confirmWays } from "../passkey";
import { visible } from "../textRules";
import { dayTime } from "../when";
import { codeParkedHere, SecondScreen, type ParkedBrief, type SignerTicket, type SignerView } from "../secondScreen";
import { forgetSigners } from "./SignOnMac";
import { ConfirmIt, CopyText, type Change } from "./AccountDialog";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** What a parked request is, in a few words. */
const kindWords: Record<string, string> = {
  instruction: "An instruction", answer: "An answer", resume: "A resume", move: "A move", "e2e-key": "A new encryption key",
  "e2e-off": "Turning encryption off",
};

export function SignersSection() {
  const [signers, setSigners] = useState<SignerView[] | null>(null);
  const [waiting, setWaiting] = useState<ParkedBrief[]>([]);
  const [asking, setAsking] = useState<Change | null>(null);
  const [ticket, setTicket] = useState<SignerTicket | null>(null);
  const [error, setError] = useState("");           // what a step asked here met
  const [unread, setUnread] = useState(false);      // the lists could not be read: said, as no alert
  const load = useCallback(async () => {
    forgetSigners();                                 // one press reads the signers again: one may be paired or revoked since
    try {
      const [s, w] = await Promise.all([SecondScreen.signers(), SecondScreen.waiting()]);
      if (!Array.isArray(s?.signers) || !Array.isArray(w?.requests)) throw new Error("not the lists the page reads");
      setSigners(s.signers); setWaiting(w.requests); setUnread(false);
    } catch { setUnread(true); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const begin = async () => {
    setError(""); setTicket(null);
    try {
      setAsking({ what: "signer", label: "Pairing a Mac as a signer", action: "Make a ticket", subject: "signer-ticket", ways: await confirmWays() });
    } catch (e) { setError(message(e)); }
  };
  const revoke = async (id: string) => {
    try { await SecondScreen.revoke(id); await load(); } catch (e) { setError(message(e)); }
  };
  const cancelAll = async () => {
    try { await SecondScreen.cancelAll(); await load(); } catch (e) { setError(message(e)); }
  };
  return (
    <div className="text-[12.5px]">
      {unread && <div className="text-muted">The signers could not be read from the server just now.</div>}
      {signers !== null && signers.length === 0 && <div className="text-muted">No Mac is paired as a signer.</div>}
      {(signers ?? []).map((s) => (
        <div key={s.id} className="flex flex-wrap items-center gap-2 py-0.5">
          <Laptop size={13} className="text-muted" />
          <span>{visible(s.name)}</span>
          <span className="text-[11.5px] text-muted">paired {dayTime(s.created_at)}{s.last_seen_at ? `, last seen ${dayTime(s.last_seen_at)}` : ""}</span>
          <button className="btn btn-sm !h-6" aria-label={`Revoke ${visible(s.name)}`} onClick={() => void revoke(s.id)}><Trash2 size={12} /> Revoke</button>
        </div>
      ))}
      <div className="mt-2">
        <button className="btn btn-sm" onClick={() => void begin()}><Laptop size={12} /> Pair a Mac</button>
      </div>
      {asking && (
        <ConfirmIt key="signer" change={asking} onCancel={() => setAsking(null)}
          onConfirmed={async (confirmation) => { setTicket(await SecondScreen.newTicket(confirmation)); setAsking(null); await load(); }} />
      )}
      {ticket && (
        <div className="mt-2 rounded border border-line p-2" role="status">
          <div>On the Mac your security key is plugged into, at a terminal of your own, run this within ten minutes. It pairs once.</div>
          <code className="mt-1 block break-all font-mono text-[11.5px]">{ticket.command}</code>
          <div className="mt-1"><CopyText label="Copy the command" text={ticket.command} /></div>
        </div>
      )}
      {waiting.length > 0 && (
        <div className="mt-3" aria-label="Requests waiting for a signer">
          <div className="text-muted">Requests waiting for a signer, as the server lists them:</div>
          {waiting.map((r) => {
            const code = codeParkedHere(r.id);
            return (
              <div key={r.id} className="flex flex-wrap items-center gap-2 py-0.5 text-[12px]">
                <span>{kindWords[r.purpose] ?? "A step"}</span>
                <span className="text-muted">{r.state === "signed" ? "signed, not yet sent" : "waiting"}, parked from {visible(r.parked_from)} (the server's word)</span>
                {code ? <span className="font-mono">{code}</span> : <span className="text-wait">not parked by this page</span>}
              </div>
            );
          })}
          <button className="btn btn-sm mt-1" onClick={() => void cancelAll()}><X size={12} /> Cancel all</button>
        </div>
      )}
      {error && <div role="alert" className="mt-2 text-fail">{error}</div>}
    </div>
  );
}
