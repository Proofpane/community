import { useEffect, useState } from "react";
import { Check, Loader2, Lock, Trash2 } from "lucide-react";
import { Api, type StorageChoiceName, type StorageView } from "../api";
import { confirmWays, type ConfirmWays } from "../passkey";
import { dayTime } from "../when";
import { ConfirmIt } from "./AccountDialog";
import { usd } from "./bits";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** What the button that keeps a choice says: where the person's work will live. */
export const KEEP: Record<StorageChoiceName, string> = {
  repository: "Keep my work in my repository",
  machine: "Keep my work on my own machine",
  hosted: "Keep my work in a workspace this deployment runs for me",
};

/** The question an account that is not an admin, nor the desktop app's own, is asked once it chooses its own machine:
 *  what end-to-end encryption seals and each thing it leaves readable; who serves the page that opens what it seals,
 *  and what older snapshots keep; and what is lost with both the passkey and the recovery code. */
export const E2E_QUESTION = "Turn on end-to-end encryption?";
export const E2E_SEALS = "With it on, three kinds of what your linked sessions send are kept here only sealed, as "
  + "envelopes this deployment never opens: their messages to you, the titles, projects, branches and repositories in "
  + "their lists of sessions, and their conversations' messages.";
export const E2E_DOES_NOT = "It leaves readable: what a session sends another session or all of them; each message's "
  + "kind, who it is to, what it answers and when; a mirrored note's session and mirror word; a message a session sent "
  + "you before you turned it on that sealing in place leaves; the two fixed texts a session posts, \"working on it\" "
  + "and its acknowledgement of a stop; in a list of sessions, each one's id, where it ran, whether it is live, its start "
  + "and last times and whether it is registered or withheld, and the list's withheld ids, its machine's id and its "
  + "pins; who said each message of a conversation, and when; your own instructions and answers; checkpoints; your "
  + "links' names and whether they are present; the commands held for your approval, with their folders and sessions; "
  + "and a moved session's title, its repository's address, branch, head and folder, its failure reason, and its "
  + "transcript and the git bundle of its work.";
export const E2E_SERVED = "The page that opens what it seals is served by this deployment, so those words are kept from "
  + "its operator only while it serves that page unchanged; and what your sessions sent you before you turned it on, "
  + "sealed here as you do, stays readable in its older snapshots.";
export const E2E_LOST = "If you lose both your passkey and your recovery code, the sealed words are lost for good: no one "
  + "can open them again.";

/** What deleting a workspace left behind at once takes with it, and what it does not: the
 *  sign-ins go with the workspace's own volume; the record this deployment deletes stays in its own volume's older
 *  snapshots. */
export const DELETE_TAKES = "Deleting it now destroys its machine and its volume at once, and your programs' sign-ins on "
  + "that volume go with it. The record of it this deployment deletes stays in older snapshots of this deployment's own "
  + "volume until Fly drops them. What this deployment keeps of your agents, tasks, Manager and finishes stays.";

/** Where the account's new work lives, in the account's dialog: the three
 *  choices with their sentences, each one this deployment does not offer the account shown and said why; picking
 *  another names what a move leaves where the work is now, and nothing is kept until the person confirms it. Then, for
 *  an account the server says to ask, the question to turn end-to-end encryption on, whose "Not now" the server keeps
 *  and whose "Turn it on" opens the Encryption section, where turning it on stays the person's own act. Under the fly
 *  provider it shows the account's month against its share of the cap, the day and time a workspace it
 *  left is kept until, with its delete at once, which takes the person's own word made for that act (ConfirmIt), and when and
 *  why one was deleted. */
export function StorageChoice({ onEncryption }: { onEncryption: () => void }) {
  const [view, setView] = useState<StorageView | null>(null);
  const [picked, setPicked] = useState<StorageChoiceName | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState<ConfirmWays | null>(null);
  useEffect(() => {
    let live = true;
    Api.storage().then((v) => { if (live) setView(v); }).catch((e) => { if (live) setError(message(e)); });
    return () => { live = false; };
  }, []);
  const run = async (fn: () => Promise<StorageView>) => {
    setBusy(true); setError("");
    try { setView(await fn()); setPicked(null); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };
  if (!view) {
    return error ? <div role="alert" className="text-[12.5px] text-fail">{error}</div>
      : <div className="text-[12.5px] text-muted"><Loader2 size={13} className="mr-1 inline animate-spin" /> Reading…</div>;
  }
  const shown = picked ?? view.choice;
  const moving = picked !== null && picked !== view.choice;
  const leaves = moving ? view.left_behind[picked] ?? [] : [];
  const label = (c: StorageChoiceName) => view.choices.find((o) => o.choice === c)?.label ?? c;
  const ws = view.workspace;
  const askDelete = async () => {
    setError("");
    try { setDeleting(await confirmWays()); } catch (e) { setError(message(e)); }
  };
  return (
    <>
      <section className="mb-6">
        <h3 className="mb-1 text-[13px] font-semibold">Where your work lives</h3>
        <p className="mb-3 text-[12px] text-muted">
          Where your new work lives, and so whether this deployment starts a workspace for you: it starts one only where
          you keep your work in it. Changing this moves nothing; before it is kept, this says what stays where your
          work lives now, and what becomes of it.
        </p>
        {ws?.share && (
          <p className="mb-3 text-[12px] text-fg-dim">
            This month ({ws.share.month}) your workspace has used {usd(ws.share.spent_usd)} of your {usd(ws.share.share_usd)} share
            of this deployment's monthly cap: a start that would pass it is refused, and a running workspace is stopped
            before it does.
          </p>
        )}
        <div role="radiogroup" aria-label="Where your work lives" className="flex flex-col gap-2">
          {view.choices.map((c) => (
            <label key={c.choice} className={`flex gap-2 rounded-md border p-2 ${shown === c.choice ? "border-accent" : "border-line"} ${c.offered ? "cursor-pointer" : "opacity-70"}`}>
              <input
                type="radio"
                name="storage-choice"
                className="mt-0.5"
                checked={shown === c.choice}
                disabled={!c.offered || busy}
                onChange={() => { setPicked(c.choice); setError(""); }}
              />
              <span className="min-w-0">
                <span className="block text-[12.5px] font-medium">
                  {c.label}
                  {view.choice === c.choice && (
                    <span className="ml-1.5 text-[11.5px] font-normal text-muted">
                      {view.chosen ? "(your choice)" : view.choice === "hosted" ? "(not chosen: you have a workspace here)"
                        : "(the default for an account that has not chosen)"}
                    </span>
                  )}
                </span>
                <span className="block text-[12px] text-fg-dim">{c.sentence}</span>
                {!c.offered && c.why && <span className="mt-0.5 block text-[12px] text-muted">Not offered to you here: <span>{c.why}</span></span>}
              </span>
            </label>
          ))}
        </div>
        {moving && (
          <div className="mt-3 rounded-md border border-line p-2 text-[12px]">
            <div className="font-medium">Moving to: {label(picked)}</div>
            {leaves.length
              ? <ul className="mt-1 list-disc pl-4 text-fg-dim">{leaves.map((line) => <li key={line}>{line}</li>)}</ul>
              : <div className="mt-1 text-fg-dim">Nothing stays where your work lives now.</div>}
            <div className="mt-2 flex flex-wrap gap-1.5">
              <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void run(() => Api.chooseStorage(picked))}>
                {busy ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />} {KEEP[picked]}
              </button>
              <button className="btn btn-sm" disabled={busy} onClick={() => { setPicked(null); setError(""); }}>Cancel</button>
            </div>
          </div>
        )}
        {ws?.kept_until != null && (
          <div className="mt-3 rounded-md border border-line p-2 text-[12px]">
            <div>
              Your workspace is kept until {dayTime(ws.kept_until)}, {ws.keep_days} days from when this deployment began
              keeping it: choose a workspace this deployment runs for you again before then and it stays yours, its next
              start starting the same machine and volume. After that this deployment destroys its machine and its
              volume, with everything on it.
            </div>
            <div className="mt-1 text-fg-dim">{DELETE_TAKES}</div>
            {deleting ? (
              <ConfirmIt change={{ what: "workspace-delete", label: "Deleting your workspace", action: "Delete it", subject: "workspace-delete", ways: deleting }}
                onConfirmed={async (confirmation) => { setView(await Api.deleteWorkspace(confirmation)); setDeleting(null); }}
                onCancel={() => setDeleting(null)} />
            ) : (
              <button className="btn btn-sm btn-danger mt-2" disabled={busy} onClick={() => void askDelete()}>
                <Trash2 size={12} /> Delete the workspace now
              </button>
            )}
          </div>
        )}
        {ws?.removed && (
          <div className="mt-3 rounded-md border border-line p-2 text-[12px] text-fg-dim">
            Your workspace was deleted on {dayTime(ws.removed.removed_at)}: <span>{ws.removed.why}</span> What this deployment
            keeps of your agents, tasks, Manager and finishes stays.
          </div>
        )}
        {error && <div role="alert" className="mt-2 text-[12px] text-fail">{error}</div>}
      </section>
      {view.e2e.ask && (
        <section className="mb-6 rounded-md border border-line p-3">
          <h3 className="mb-1 flex items-center gap-1.5 text-[13px] font-semibold"><Lock size={13} /> {E2E_QUESTION}</h3>
          <p className="text-[12px] text-fg-dim">{E2E_SEALS}</p>
          <p className="mt-1 text-[12px] text-fg-dim">{E2E_DOES_NOT}</p>
          <p className="mt-1 text-[12px] text-fg-dim">{E2E_SERVED}</p>
          <p className="mt-1 text-[12px] text-wait">{E2E_LOST}</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <button className="btn btn-sm btn-primary" disabled={busy} onClick={onEncryption}><Lock size={12} /> Turn it on</button>
            <button className="btn btn-sm" disabled={busy} onClick={() => void run(Api.storageNotNow)}>Not now</button>
          </div>
          <p className="mt-1.5 text-[11.5px] text-muted">Not now is kept, so this is not asked again; Encryption, in your account, turns it on whenever you want.</p>
        </section>
      )}
    </>
  );
}
