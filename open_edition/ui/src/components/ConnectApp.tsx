import { useEffect, useState } from "react";
import { CircleAlert, KeyRound, Loader2 } from "lucide-react";
import { Api, ApiError, type ConnectRequest } from "../api";
import { confirmWays, type ConfirmWays } from "../passkey";
import { ConfirmIt } from "./AccountDialog";
import { AppName } from "./AppName";
import { Modal } from "./Modal";
import { clock } from "../when";

// Connecting an app through the Claude app connector (the server's oauth.py): /oauth/authorize keeps the request and
// opens the app at #connect=ID, since the sign-in is a token in the page and the server cannot see who is signed in.
// Here the owner reads what asks and what it may do, and allows it, with the account's confirmation, or refuses it;
// then the page leaves for the app's own address with the code or the refusal.

/** Where the page goes once the request is answered: the app's address, as the server gave it. */
export const leaving = { to: (url: string) => { window.location.assign(url); } };

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** A wait in words: "3 minutes", "45 seconds". */
export function waitFor(seconds: number): string {
  return seconds >= 60 && seconds % 60 === 0 ? `${seconds / 60} minute${seconds === 60 ? "" : "s"}` : `${seconds} second${seconds === 1 ? "" : "s"}`;
}

export const ACCOUNT_OFF = "This account does not let apps connect: turn that on in Account, Connected apps, then connect again from the app.";
export const ENDED = "This request has ended: it was answered, ten minutes passed, or the ADE had too many waiting. Start connecting again in the app.";
/** A request made from another network than this browser's: an address someone forwarded, perhaps. */
export const ELSEWHERE = (from: string | null, here: string | null) =>
  `This request was made from ${from ?? "another address"}, another network than this browser's${here ? ` (${here})` : ""}.`;
/** A request this browser did not make: it does not hold the cookie the server set in the browser that did, as
 *  a #connect address someone forwarded does not, whatever its network. A request moved here by hand from
 *  the browser that made it, or a browser that kept no cookie, is told of the same way. */
export const OTHER_BROWSER = "This request was started in another browser, not this one. If you did not just move it here yourself from the browser where you clicked Connect, refuse.";
/** Said with every request: whoever starts connecting their app and sends the owner the address gets a request the
 *  owner's own browser makes, on the owner's own network, which nothing else on this pane can tell from the owner's. */
export const CLICKED = "If you did not just click Connect in Claude, in this browser, or in a Claude app on this device that opened it, refuse.";
/** How the request arrived, when the browser did not say it came as a navigation from a page of the app's own site (the
 *  server's request_view: from_app_page). */
export function cameHow(r: ConnectRequest): string {
  if (r.from_app_page) return "";
  const site = r.returns_to;
  switch (r.fetch_site) {
    case "cross-site":
      return r.came_from ? `It came from a page of ${r.came_from}, not of ${site}, where its sign-in returns.` : `It came from a page of another site, which the browser did not name, not one it says is ${site}'s.`;
    case "none": return `It was opened directly, not from a page of ${site}: by an app, a bookmark, or an address pasted or sent to you.`;
    case "same-site":
    case "same-origin": return `It was opened from a page of this ADE's own site, not from ${site}.`;
    default: return `This browser did not say where it came from, so nothing shows it came from ${site}.`;
  }
}
export const READS = "See your sessions as the ADE lists them, with where each ran; their conversations as the ADE keeps them; what waits for you, with the full text of the commands your sessions wait to run and their folders, and other apps' proposals without their words; and its own proposals. Not the title, conversation or waiting commands of a session its machine withholds, a conversation kept end-to-end encrypted, your workspace's files or terminals, or anything shaped like a secret.";
export const PROPOSES = "Propose instructions, stops, resumes, answers to questions and answers to held commands. A proposal does nothing until you sign or accept it here; the app can never send, stop, resume, answer, approve, move or link anything itself.";

/** The consent pane: the request read with this account's sign-in, Allow (with the account's confirmation) or
 *  Refuse, then the app's own address. */
export function ConnectApp({ id, onClose, onApps }: { id: string; onClose: () => void; onApps?: () => void }) {
  const [req, setReq] = useState<ConnectRequest | null>(null);
  const [ended, setEnded] = useState(false);
  const [error, setError] = useState("");
  const [propose, setPropose] = useState(true);
  const [ways, setWays] = useState<ConfirmWays | null>(null);
  const [busy, setBusy] = useState(false);
  const [going, setGoing] = useState("");
  useEffect(() => {
    let live = true;
    Api.connectRequest(id).then((r) => {
      if (!live) return;
      setReq(r);
      setPropose(r.scopes.includes("ade.propose"));
    }).catch((e) => {
      if (!live) return;
      if (e instanceof ApiError && e.status === 404) setEnded(true);
      else setError(message(e));
    });
    return () => { live = false; };
  }, [id]);
  const go = (url: string) => {
    let host = url;
    try { host = new URL(url).host; } catch { /* said as it came */ }
    setGoing(host);
    leaving.to(url);
  };
  const askAllow = async () => {
    setBusy(true); setError("");
    try { setWays(await confirmWays()); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };
  const refuse = async () => {
    setBusy(true); setError("");
    try { go((await Api.refuseConnect(id)).redirect); } catch (e) { setError(message(e)); setBusy(false); }
  };
  const asked = req?.scopes.includes("ade.propose") ?? false;
  return (
    <Modal title="Connect an app" onClose={onClose} width={560}>
      {ended ? (
        <p className="text-[12.5px]">{ENDED}</p>
      ) : going ? (
        <p className="text-[12.5px]">Going back to {going}.</p>
      ) : !req ? (
        error ? <p role="alert" className="text-[12.5px] text-fail">{error}</p> : <Loader2 size={16} className="animate-spin text-muted" />
      ) : (
        <div className="space-y-3 text-[12.5px]">
          <p>An app asks to use your ADE, as <span className="font-medium">{req.account}</span>.</p>
          <p>
            It calls itself &ldquo;<AppName name={req.client_name} className="font-medium" />&rdquo;: its own word.{" "}
            {req.described_at ? <>It is described at <span className="font-medium">{req.described_at}</span>. </> : null}
            Its sign-in returns to <span className="font-medium">{req.returns_to}</span>.
          </p>
          {req.loopback && (
            <p role="note" className="rounded-md border border-wait/50 bg-wait/[0.05] p-2 text-wait">
              It runs on a computer, which receives its sign-in at {req.returns_to}{req.port ? `:${req.port}` : ""}: any program on that
              computer can ask in its name. Allow it only if you started connecting in Claude Code yourself, just now.
            </p>
          )}
          {req.same_network === false && (
            <p aria-label="Asked from another network" className="rounded-md border border-wait/50 bg-wait/[0.05] p-2 text-wait">
              {ELSEWHERE(req.asked_from, req.viewer)}
            </p>
          )}
          {req.same_browser === false && (
            <p aria-label="Started in another browser" className="rounded-md border border-wait/50 bg-wait/[0.05] p-2 text-wait">{OTHER_BROWSER}</p>
          )}
          {cameHow(req) && <p aria-label="How the request came" className="rounded-md border border-wait/50 bg-wait/[0.05] p-2 text-wait">{cameHow(req)}</p>}
          <p aria-label="Did you start it" className={req.same_network === false || req.same_browser === false || cameHow(req) ? "font-medium text-wait" : "text-muted"}>{CLICKED}</p>
          {!req.account_on ? (
            <div role="alert" className="rounded-md border border-fail/50 bg-fail/[0.05] p-2">
              <div className="flex gap-2"><CircleAlert size={14} className="mt-0.5 flex-none text-fail" /> {ACCOUNT_OFF}</div>
              <div className="mt-2 flex gap-2">
                {onApps && <button className="btn btn-sm" onClick={onApps}>Open Connected apps</button>}
                <button className="btn btn-sm" disabled={busy} onClick={() => void refuse()}>Refuse</button>
              </div>
            </div>
          ) : (
            <>
              <div>
                <div className="mb-1 font-medium">What it may do</div>
                <ul className="list-disc space-y-1 pl-5">
                  <li><span className="font-medium">Read.</span> {READS}</li>
                  {asked && (
                    <li>
                      <label className="flex items-start gap-2">
                        {/* fixed while the confirmation is asked, which is made for the choice shown (connector-allow:ID:propose|read) */}
                        <input type="checkbox" className="mt-0.5" checked={propose} disabled={!!ways} onChange={(e) => setPropose(e.target.checked)} />
                        <span><span className="font-medium">Propose.</span> {PROPOSES}</span>
                      </label>
                    </li>
                  )}
                </ul>
              </div>
              <p>What it reads goes to that app and its servers.</p>
              <p>
                {req.hold_seconds > 0
                  ? `Once you allow it, the connection takes effect after ${waitFor(req.hold_seconds)}, and you can cancel it in Connected apps until then. `
                  : "Once you allow it, the connection takes effect at once. "}
                It lasts until you revoke it, or {req.lasts_days} days. It asked at {clock(req.asked_at)}.
              </p>
              {!ways && (
                <div className="flex gap-2">
                  <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void askAllow()}><KeyRound size={12} /> Allow</button>
                  <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => void refuse()}>Refuse</button>
                </div>
              )}
              {ways && (
                <ConfirmIt change={{ what: "connect", label: "Connecting this app", action: "Allow", subject: `connector-allow:${id}:${propose ? "propose" : "read"}`, ways }}
                  onConfirmed={async (confirmation) => go((await Api.allowConnect(id, propose, req.account_id, confirmation)).redirect)}
                  onCancel={() => setWays(null)} />
              )}
            </>
          )}
          {error && <p role="alert" className="text-fail">{error}</p>}
        </div>
      )}
    </Modal>
  );
}
