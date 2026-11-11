// Account, Encryption: the account's end-to-end encryption keys (e2eKeys.ts) as the owner sees and changes them. Turning
// it on, the recovery code first; unlocking in this tab; the passkeys that unlock it; the key links seal to and who
// signed it; each link's key and pins; and each change, signed or confirmed with a touch, which waits on a hold with a
// notice to the account's devices. Every touch is the person's own click, with nothing awaited before the prompt.
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Check, KeyRound, Loader2, Lock, LockOpen, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";
import { APP, api, server } from "../api";
import { hostLabel } from "../places";
import { useStore } from "../store";
import { clock, dayTime } from "../when";
import { stateOf, talkKeySlot, talkSlot, useE2EStore } from "../e2e";
import * as K from "../e2eKeys";
import { isSecurityKey, kindName, kindWords } from "../passkey";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** A passkey as the Account page lists it, for its fingerprint (what a link pins). */
interface AccountKey { id: string; name: string; fingerprint?: string }

function Section({ title, children, note }: { title: string; children: ReactNode; note?: ReactNode }) {
  return (
    <section className="mb-6 last:mb-0">
      <h3 className="mb-1 text-[13px] font-semibold">{title}</h3>
      {note && <p className="mb-3 text-[12px] text-muted">{note}</p>}
      {children}
    </section>
  );
}

/** A change refused as made from keys older than this browser's floor (BEHIND): the tab reads its keys again at once
 *  and follows them, or locks saying why (useE2EStore.recheck). */
function behind(e: unknown) {
  if (message(e) === K.BEHIND) void useE2EStore.getState().recheck();
}

/** A step's busy state and its error; `run` calls its function at once, so a touch in it comes first in a click. */
function useStep() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try { await fn(); } catch (e) { setError(message(e)); behind(e); } finally { setBusy(false); }
  };
  return { busy, error, setError, run };
}

const Spin = () => <Loader2 size={12} className="animate-spin" />;
const Err = ({ text }: { text: string }) => (text ? <div role="alert" className="mt-2 text-[12.5px] text-fail">{text}</div> : null);

/** A change's flow, made from the keys as they are and prepared at once (its statement around a nonce, or the server's
 *  confirmation challenge), so its click reaches the browser's prompt with nothing awaited. Made again whenever `key`
 *  changes (keysKey: the keys this tab holds, or what the server lists of them), and until then no flow is given, so a
 *  click never sends a change made from keys since replaced; with `fresh`, made again every four minutes until it is
 *  used, since a nonce lasts five. */
function usePrepared<T extends { prepare(): Promise<void>; dispose?(): void }>(make: () => T, setError: (s: string) => void, fresh = false, key = ""): T | null {
  const maker = useRef(make);
  maker.current = make;
  const [flow, setFlow] = useState<T | null>(null);
  useEffect(() => {
    let live = true;
    // the flow shown is dropped (dispose, where it holds what it was given or made since) once another replaces it, and
    // as this closes; one not shown holds nothing yet (its preparation makes no secret)
    let shown: T | null = null;
    setFlow(null);
    setError("");
    const once = () => {
      const f = maker.current();
      f.prepare().then(() => {
        if (!live) return;
        shown?.dispose?.();
        shown = f;
        setFlow(f);
      }, (e) => { if (live) { setError(message(e)); behind(e); } });
    };
    once();
    const t = fresh ? setInterval(once, 240000) : undefined;
    return () => { live = false; clearInterval(t); shown?.dispose?.(); };
  }, [key, setError, fresh]);
  return flow;
}

/** What a change is made from: the keys this tab holds, and what the server lists of the account's keys, passkeys,
 *  recovery code and statements, and of the change that waits, who may cancel it among them; and whether the server can
 *  use ML-KEM, so a screen open as it loses it is prepared again, and refused before any touch for post-quantum keys
 *  (usePrepared's key). */
const keysKey = (ring: K.Ring | null, view: K.E2EView) => JSON.stringify([ring?.master.id ?? null, ring?.rosterSealed ?? null, ring?.rosterWhy ?? null,
  ring ? [...ring.keys.keys()] : null, ring?.unreached ?? null, ring?.unopened ?? null, view.mk_id, view.roster, view.content_keys.map((k) => [k.key_id, k.mk_id]),
  view.credentials.map((c) => [c.passkey_id, c.credential_id, c.public_key]), view.passkeys.map((p) => [p.passkey_id, p.credential_id, p.tier]),
  view.recovery ? [view.recovery.public_key, view.recovery.anchor] : null, view.statements.length, view.statements.at(-1)?.key_id ?? null,
  view.high_security, view.rekey_due, pendingKey(view.pending), view.mlkem]);

/** What cancelling the change that waits is made from: the change, and who may cancel it. */
const pendingKey = (p: K.E2EPending | null) => (p ? [p.id, p.change, p.cancel_by, p.cancel_by_code] : null);

/** A passkey's name, by its id, as the account lists it: one gone from the account is named as such. */
const nameOf = (view: K.E2EView, passkeyId: string) => view.passkeys.find((p) => p.passkey_id === passkeyId)?.name ?? "a passkey removed since";
/** Words that begin a sentence, with a capital. */
const sentence = (words: string) => words.charAt(0).toUpperCase() + words.slice(1);

/** What unlocks encryption after a change that makes a new master key: the passkeys that stay, and the recovery code. */
function keptWords(view: K.E2EView, kept: string[], newCode = false): string {
  const code = `${newCode ? "the new" : "the"} recovery code`;
  return kept.length ? `After it, these unlock encryption: ${kept.map((id) => nameOf(view, id)).join(", ")}, and ${code}.`
    : `After it, only ${code} unlocks encryption.`;
}

/** What a new master key leaves as it is (abandon): the content keys the server lists under a master key that does not
 *  lead back to the tab's, which it neither trusts nor opens, and those under one that does which do not open, so it does
 *  not wrap them again. "" when it leaves none. */
function abandonWords(ring: K.Ring): string {
  const off = ring.unreached.length;
  const shut = ring.unopened.length;
  const n = off + shut;
  if (!n) return "";
  const one = n === 1;
  const why = [
    off ? `the server lists ${off === n ? (one ? "it" : "them") : off} under a master key that does not lead back to yours` : "",
    shut ? `${shut === n ? (one ? "it does" : "they do") : `${shut} ${shut === 1 ? "does" : "do"}`} not open` : "",
  ].filter(Boolean).join(", and ");
  return `It leaves ${one ? "a content key" : `${n} content keys`} as ${one ? "it is" : "they are"}: ${why}, so ${one ? "it is" : "they are"} not wrapped again, and what was sealed to ${one ? "it" : "them"} stays unopened here.`;
}

/** The person's word that only the recovery code is to unlock encryption after a change that leaves no passkey. */
function OnlyCode({ checked, onChange }: { checked: boolean; onChange: (on: boolean) => void }) {
  return (
    <label className="mb-2 flex items-start gap-2 text-[12px] text-fg-dim">
      <input type="checkbox" className="mt-0.5" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>Only the recovery code is to unlock encryption after it: go ahead.</span>
    </label>
  );
}

/** How many devices a change's notice was sent toward, as the server counted them (`told`; how many it reached is not
 *  known; -1: it could not be sent), in the words a hold's card on the Sessions page gives (Channel's toldWords);
 *  nothing when the server gave no whole number. */
export function noticeWords(told: unknown): string {
  if (typeof told !== "number" || !Number.isInteger(told)) return "";
  return told > 0
    ? `Its notice was sent toward ${told} ${told === 1 ? "device" : "devices"} with notifications on.`
    : told < 0 ? "Its notice could not be sent. Only this page shows it."
      : "No device of this account has notifications on: only this page shows it.";
}

/** What a change waits for, in words, and how many devices its notice was sent toward. */
export function holdWords(answer: K.Answer | null | undefined): string {
  const at = answer?.hold?.takes_effect_at;
  if (!at) return "Done.";
  const told = noticeWords(answer?.told);
  return `It takes effect at ${at - Date.now() / 1000 > 3600 ? dayTime(at) : clock(at)}, unless you cancel it first.${told ? ` ${told}` : ""}`;
}

/** A hold's length in words, or null when the server did not say it. */
export function lengthWords(seconds: number | undefined): string | null {
  if (seconds === undefined || seconds < 0) return null;
  const unit = (n: number, one: string, many: string) => (n === 1 ? one : `${n} ${many}`);
  if (seconds >= 86400 && seconds % 86400 === 0) return unit(seconds / 86400, "a day", "days");
  if (seconds >= 3600 && seconds % 3600 === 0) return unit(seconds / 3600, "an hour", "hours");
  if (seconds >= 60 && seconds % 60 === 0) return unit(seconds / 60, "a minute", "minutes");
  return unit(seconds, "a second", "seconds");
}

/** Before a change is signed: how long it waits, on which of the view's holds, before it takes effect. */
export function waitWords(view: K.E2EView, hold: "change" | "long" | "recovery"): string {
  const length = lengthWords(view.holds?.[hold]);
  return `It waits ${length ?? "on its hold"} before it takes effect, and can be cancelled until then.`;
}

/** The recovery code's key for a change sent with its proof: this tab's, when it was unlocked with the code, else the
 *  code typed (CodeField), asked again once the code in force is another. */
function useRecoveryKey(view: K.E2EView, ring: K.Ring | null) {
  const [typed, setTyped] = useState<K.Priv | null>(null);
  const inForce = view.recovery?.public_key;
  // the key typed is put away when another replaces it, when the code in force changes, when the tab locks, and as
  // this closes
  useEffect(() => () => K.forgetPriv(typed), [typed]);
  useEffect(() => { setTyped(null); }, [inForce]);
  const locked = !ring;
  useEffect(() => { if (locked) setTyped(null); }, [locked]);
  return { rk: ring?.via.kind === "recovery" ? ring.via.rk : typed, setKey: setTyped };
}

/** The recovery code typed for a change sent with its proof, checked to be the account's before any touch; or what is
 *  given already. */
function CodeField({ view, account, ring, rk, onKey, run, busy, label }: { view: K.E2EView; account: string; ring: K.Ring | null; rk: K.Priv | null;
  onKey: (k: K.Priv) => void; run: (fn: () => Promise<void>) => Promise<void>; busy: boolean; label: string }) {
  const [typed, setTyped] = useState("");
  if (ring?.via.kind === "recovery") return <div className="mt-1 text-done" role="status">It goes with the recovery code this tab was unlocked with.</div>;
  if (rk) return <div className="mt-1 text-done" role="status">That is the recovery code: it goes with its proof.</div>;
  return (
    <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); void run(async () => { onKey(await K.codeKey(view, account, typed)); setTyped(""); }); }}>
      <input aria-label={label} className="input h-7 w-56 font-mono uppercase" placeholder="The recovery code" autoComplete="off" autoCapitalize="characters" spellCheck={false}
        value={typed} onChange={(e) => setTyped(e.target.value)} />
      <button className="btn btn-sm" disabled={busy || !typed.trim()}>Use the code</button>
    </form>
  );
}

/** What going with the recovery code's proof does, in words: how long it waits, and that only the code cancels it then. */
const codeWait = (view: K.E2EView) => `it waits ${lengthWords(view.holds?.recovery) ?? "on its hold"}, and only the code cancels it then`;
const codeWaitWords = (view: K.E2EView) => `With the recovery code ${codeWait(view)}.`;

const CHANGES: Record<string, string> = {
  "turn-on": "Turning encryption on", rotate: "A new content key", revoke: "Replacing the keys", credentials: "A passkey added to encryption",
  off: "Turning encryption off", anchors: "Renewing anchors", passkey: "A passkey added to your account", upload: "An upload of statements from a linked session",
};

/** The change that waits, named: an upgrade is a revocation, and the fold a turn-on, each named as what it is. */
export const changeName = (view: K.E2EView, pending: K.E2EPending) =>
  K.upgradeWaits(view) ? "The upgrade to post-quantum keys" : K.foldWaits(view) ? "Turning encryption on again with post-quantum keys"
    : CHANGES[pending.change] ?? "A change to encryption";

/** Where turning it on would be offered while turning it on waits on its hold (the change that waits is shown below). */
const WAITING_ON = "Turning it on waits on its hold, below: cancel it there if it is not yours.";

/** Said beside the warning over no keys where this browser saw the account's (K.rolledBack), above Turn on, which is
 *  offered there as to a new account: an owner who put the server back themselves, from a backup made before encryption
 *  was turned on, turns it on again, since linked sessions send in the clear until it is on. Keys made so may then be
 *  refused as they take effect where this browser saw more statements than the new ones hold; Trust the server's keys,
 *  offered with that refusal, is the owner's word that they did it. */
export const RESTORED_TURN_ON = "If you restored the server yourself, from a backup made before encryption was turned on, turn encryption on again below: until it is on, your linked sessions send in the clear.";

/** Where turning it on would be offered while another turning-on waits in the place of the one this tab made (any
 *  passkey that may cancel a first turning-on may take its place). */
export function inPlaceWords(view: K.E2EView, pending: K.E2EPending): string {
  // a turning-on is signed with a passkey, never with the recovery code alone
  const one = pending.made_by ? `one made with ${nameOf(view, pending.made_by)}` : "another";
  return `The turning-on this tab made no longer waits: ${one} waits in its place. The recovery code this tab showed will not unlock it, and the keys this tab made are gone. If you did not make it, cancel it below before it takes effect, then turn encryption on again.`;
}

/** The same where the turning-on this tab made was the fold (K.Fold), whose code may be the one the person has: its
 *  keys are gone, and a new code it showed unlocks nothing. */
export function foldInPlaceWords(view: K.E2EView, pending: K.E2EPending): string {
  const one = pending.made_by ? `one made with ${nameOf(view, pending.made_by)}` : "another";
  return `Turning it on again with post-quantum keys, which this tab signed, no longer waits: ${one} waits in its place. The keys this tab made are gone, and a new recovery code it showed unlocks nothing. If you did not make it, cancel it below before it takes effect.`;
}

/** The account's encryption: in a browser only, since passkeys are the server's website's. */
export function EncryptionPane({ onAccount }: { onAccount?: () => void } = {}) {
  const user = useStore((s) => s.user);
  const view = useE2EStore((s) => s.view);
  const ring = useE2EStore((s) => s.ring);
  const why = useE2EStore((s) => s.why);
  const state = useE2EStore(stateOf);
  // the account's passkeys, with their fingerprints, that a link's pins are matched to: null while they are read,
  // "failed" when they could not be, so no pin is then taken for a key the account no longer has
  const [keys, setKeys] = useState<AccountKey[] | "failed" | null>(null);
  // drawn again when this browser's floor changes at the person's word (Trust the server's keys): what is said and
  // offered over keys older than this browser saw (K.rolledBack) follows it, which the store does not hold
  const [, setFloorChanged] = useState(0);
  const userId = user?.id;
  useEffect(() => {
    if (!userId || APP) return;
    void useE2EStore.getState().load(userId);
    setKeys(null);
    api<{ passkeys: AccountKey[] }>("/api/account/passkeys").then((r) => setKeys(r.passkeys), () => setKeys("failed"));
  }, [userId]);
  if (!user) return null;
  if (APP) {
    return (
      <Section title="End-to-end encryption">
        <div className="text-[12.5px] text-muted">It is turned on and unlocked in a browser at {hostLabel(server())}: passkeys belong to the server's website. This app shows what is encrypted as encrypted.</div>
      </Section>
    );
  }
  if (state === "unsupported") {
    return (
      <Section title="End-to-end encryption">
        <div className="text-[12.5px] text-muted">This browser cannot do it: its WebCrypto has no X25519. Use another browser, or keep the recovery code at hand.</div>
      </Section>
    );
  }
  const refresh = async () => { await useE2EStore.getState().load(user.id); };
  return (
    <>
      <Status view={view} ring={ring} why={why} state={state} account={user.id} onDone={refresh} onTrusted={() => setFloorChanged((n) => n + 1)} onAccount={onAccount} />
      {view?.pending && <Pending key={view.pending.id} pending={view.pending} view={view} ring={ring} account={user.id} onDone={refresh} />}
      {view && K.hasKeys(view) && (
        <>
          <Keys view={view} ring={ring} onDone={refresh} />
          <PostQuantum view={view} ring={ring} onDone={refresh} />
          {view.on && <Stated view={view} />}
          {view.on && <Links view={view} keys={keys} />}
          {view.on && !K.rolledBack(user.id, view) && <TurnOff view={view} account={user.id} onDone={refresh} />}
        </>
      )}
    </>
  );
}

// ---- post-quantum keys: what the account holds, the upgrade ----------------

export const PQ_WORDS = "Post-quantum keys: what is sealed now is sealed with X25519 and ML-KEM-768 together, so a future quantum computer cannot open it from a copy of the database.";
export const CLASSICAL_WORDS = "Classical keys: what is sealed is sealed with X25519 alone, so a future quantum computer could open it from a copy of the database.";
/** Where the upgrade is offered, in words (never offered where it cannot be made). */
export const UPGRADE_BELOW = "Upgrade them below.";
/** Where an upgrade waits that this tab did not sign: it is shown, with what cancels it, where the change waits. */
export const UPGRADE_WAITS_ABOVE = "An upgrade to post-quantum keys waits, above: the keys do not change until it takes effect.";
/** Where the fold waits (K.foldWaits), which turning it on says above, with what cancels it where the change waits. */
export const FOLD_WAITS_ABOVE = "Turning it on again with post-quantum keys waits, above: the keys do not change until it takes effect.";
/** Where the fold is offered: encryption off, a new master key due, and ML-KEM on the
 *  server; it is offered above, where turning it on is, once unlocked. */
export const FOLD_ABOVE = "Encryption is off, and turning it on again makes a new master key: turn it on again with post-quantum keys, above, to upgrade them in the same change.";
export const FOLD_UNLOCK = "Encryption is off, and turning it on again makes a new master key: unlock, then turn it on again with post-quantum keys, above, to upgrade them in the same change.";
/** What the manual says post-quantum protection covers, shortened. */
export const PQ_SCOPE = "It protects against recording now and decrypting later. Texts sealed before the upgrade stay protected by X25519 alone wherever a copy of them exists: in the database until they are sealed again, and in every copy, snapshot or backup made before, always.";

/** What the count of texts stored classical holds that re-sealing leaves: a question a link sealed, which the server
 *  serves to re-seal only once it is closed for good (its answer is signed over its text as stored), but counts. */
export const OPEN_QUESTIONS_WORDS = "A question a linked session sealed is sealed again only once it is closed for good, by an answer its session has taken, by a done or a note of a linked session that refers to it, or by a note of yours closing it, so until then it is counted here after re-sealing too.";

/** How many texts sealed to a classical key the server says it still stores, in words; null when it says none or does
 *  not say. */
export function classicalTextsWords(view: K.E2EView): string | null {
  const n = K.classicalTextCount(view);
  if (!n) return null;
  return `The server still stores ${n} ${n === 1 ? "text" : "texts"} sealed to a classical key: ${n === 1 ? "it stays" : "they stay"} so until sealed again or deleted. ${OPEN_QUESTIONS_WORDS}`;
}

/** The links that will hold their texts after the upgrade until updated, in words; null when none. Those that said they
 *  cannot seal to a post-quantum key (`seals` without form 2: their cryptography library cannot use ML-KEM) apart from
 *  those that have not said what they can seal (`seals` null): a link of this release says it each time it reads the
 *  statements, so one may on its next read, and a link from before this release never does. `fold`:
 *  said of the fold (K.Fold), which turns encryption on, so links send in the clear until it takes effect. */
export function notPqWords(view: K.E2EView, fold = false): string | null {
  const links = K.linksNotPq(view);
  if (!links.length) return null;
  const change = fold ? "turning it on again" : "the upgrade";
  const names = (ls: K.E2ELink[]) => ls.map((l) => `“${l.name}”`).join(", ");
  const cannot = links.filter((l) => Array.isArray(l.seals));
  const unsaid = links.filter((l) => !Array.isArray(l.seals));
  const one = (ls: K.E2ELink[]) => ls.length === 1;
  const words: string[] = [];
  if (cannot.length) {
    words.push(`${one(cannot) ? "This linked session says it cannot" : "These linked sessions say they cannot"} seal to a post-quantum key, ${one(cannot) ? "its" : "their"} cryptography library being unable to use ML-KEM: ${names(cannot)}. Once ${change} takes effect ${one(cannot) ? "it holds its" : "they hold their"} texts until that library is updated there (a cryptography wheel of 48.0.0 or later from PyPI).`);
  }
  if (unsaid.length) {
    words.push(`${one(unsaid) ? "This linked session has" : "These linked sessions have"} not said yet what ${one(unsaid) ? "it" : "they"} can seal: ${names(unsaid)}. A session of this release says so each time it reads the keys, so ${one(unsaid) ? "it" : "each"} may say it on its next read; one from before this release never does, and once ${change} takes effect holds its texts until ade_link.py is updated there.`);
  }
  return `${words.join(" ")} Nothing is sent in the clear${fold ? " once it takes effect" : ""}.`;
}

/** The keys the account holds, classical or post-quantum, what is still stored classical, and the upgrade for a
 *  classical account (one change, unlocked, a touch of each passkey to keep). */
function PostQuantum({ view, ring, onDone }: { view: K.E2EView; ring: K.Ring | null; onDone: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [resealing, setResealing] = useState(false);
  const [said, setSaid] = useState("");
  // the upgrade this tab signed, by its hold: said while that one waits, and no longer once it took effect or was
  // cancelled, when the upgrade is offered again
  const [signed, setSigned] = useState<{ words: string; hold: string | null } | null>(null);
  const texts = classicalTextsWords(view);
  const waits = K.upgradeWaits(view);
  const ours = !!signed && (signed.hold === null || view.pending?.id === signed.hold);
  // while encryption is off: the fold waits, or is offered where a new master key is due and the server has ML-KEM,
  // or the upgrade follows turning it on again
  const due = ring ? K.rekeyDue(ring, view) : view.rekey_due;
  // the fold is offered above only while nothing waits (TurnOnAgainHere gates its button the same way); a fold that
  // waits is its own line (foldWaits), and any other change waiting falls to the generic "upgrade when off" words
  const offWords = K.foldWaits(view) ? FOLD_WAITS_ABOVE
    : due && view.mlkem && !view.pending ? (ring ? FOLD_ABOVE : FOLD_UNLOCK) : K.UPGRADE_OFF;
  return (
    <Section title="Post-quantum keys" note={PQ_SCOPE}>
      <div className={`text-[12.5px] ${view.pq ? "text-done" : "text-wait"}`} role="status">
        {view.pq ? PQ_WORDS : `${CLASSICAL_WORDS} ${!view.on ? offWords : waits ? "" : UPGRADE_BELOW}`.trim()}
      </div>
      {view.pq && texts && <div className="mt-1 text-[12px] text-muted">{texts}</div>}
      {view.pq && texts && (resealing && ring ? <ResealFlow ring={ring} view={view} onDone={async (words) => { setResealing(false); setSaid(words); await onDone(); }} onCancel={() => setResealing(false)} />
        : <button className="btn btn-sm mt-2" disabled={!ring || !view.on}
          title={!view.on ? K.RESEAL_OFF : !ring ? "Unlock first: each text is opened, then sealed again" : undefined}
          onClick={() => { setSaid(""); setResealing(true); }}><RefreshCw size={12} /> Re-seal</button>)}
      {said && <div className="mt-2 text-[12.5px] text-done" role="status">{said}</div>}
      {!view.pq && waits && <div className="mt-2 text-[12.5px] text-done" role="status">{ours ? signed!.words : UPGRADE_WAITS_ABOVE}</div>}
      {!view.pq && view.on && !waits && (
        open && ring ? <UpgradeFlow ring={ring} view={view} onDone={async (words, hold) => { setOpen(false); setSigned({ words, hold }); await onDone(); }} onCancel={() => setOpen(false)} />
          : <div className="mt-2">
            <button className="btn btn-sm btn-primary" disabled={!ring || !!view.pending || !view.mlkem}
              title={!ring ? "Unlock first: the upgrade makes a new master key" : view.pending ? K.UPGRADE_WAITING : !view.mlkem ? K.NO_MLKEM : undefined}
              onClick={() => setOpen(true)}><ShieldCheck size={12} /> Upgrade</button>
            {!view.mlkem && <div className="mt-1 text-[12px] text-wait" role="note">{K.NO_MLKEM}</div>}
          </div>
      )}
    </Section>
  );
}

export const RESEAL_WORDS = "Re-sealing opens each text the server still stores sealed to a classical key and seals it again, with the same place, to your newest post-quantum key; a conversation whole. It rewrites what the server stores, so it goes only with the recovery code: every text swapped is logged by its hashes, and a notice goes to your devices. A text that does not open here is left as it is.";
export const RESEALED_DISK = "To be sure of the disk, move the database to a new volume, then delete the old volume, its snapshots and its backups: they keep what was sealed before.";

/** What re-sealing left as it was, in words, after what it sealed: the texts that did not open here. Empty when it
 *  left none. */
export function resealLeftWords(r: { skipped: number }): string {
  return r.skipped ? `; ${r.skipped} did not open here and ${r.skipped === 1 ? "is" : "are"} left as ${r.skipped === 1 ? "it is" : "they are"}` : "";
}

/** Re-sealing: the recovery code (this tab's when it was unlocked with it,
 *  else typed), then every page of the texts still stored classical sealed again, batch by batch, with what was done
 *  said as it goes. It stops when the person says so, when the screen closes, and when the tab locks (its keys are
 *  then no longer the tab's). */
function ResealFlow({ ring, view, onDone, onCancel }: { ring: K.Ring; view: K.E2EView; onDone: (words: string) => Promise<void>; onCancel: () => void }) {
  const { rk, setKey } = useRecoveryKey(view, ring);
  const { busy, error, run } = useStep();
  const [count, setCount] = useState<{ sealed: number; left: number } | null>(null);
  const going = useRef<K.Reseal | null>(null);
  const [sealing, setSealing] = useState(false);
  useEffect(() => () => going.current?.stop(), []);
  return (
    <div role="group" aria-label="Re-sealing" className="mt-2 rounded-lg border border-accent/40 bg-accent/[0.06] p-3 text-[12.5px]">
      <div>{RESEAL_WORDS}</div>
      <CodeField view={view} account={ring.account} ring={ring} rk={rk} onKey={setKey} run={run} busy={busy} label="The recovery code, to re-seal with it" />
      {count && <div className="mt-2 text-fg-dim" role="status">Sealed again: {count.sealed}. Left as they are: {count.left}.</div>}
      <div className="mt-2 flex gap-2">
        <button className="btn btn-sm btn-primary" disabled={busy || !rk} onClick={() => void run(async () => {
          const r = new K.Reseal(ring, view, { talk: talkSlot, talkKey: talkKeySlot });
          going.current = r;
          setSealing(true);
          try {
            await r.run(rk!, (x) => setCount({ sealed: x.sealed, left: x.skipped }), () => useE2EStore.getState().ring === ring);
          } finally {
            going.current = null;
            setSealing(false);
            setCount({ sealed: r.sealed, left: r.skipped });
          }
          await onDone(`Sealed again: ${r.sealed} ${r.sealed === 1 ? "text" : "texts"}${resealLeftWords(r)}. ${RESEALED_DISK}`);
        })}>{busy ? <Spin /> : <RefreshCw size={12} />} Re-seal</button>
        {sealing ? <button className="btn btn-sm" onClick={() => going.current?.stop()}>Stop</button>
          : <button className="btn btn-sm" disabled={busy} onClick={onCancel}>Cancel</button>}
      </div>
      <Err text={error} />
    </div>
  );
}

/** What the screen says of the time left to touch and sign what was prepared (K.UPGRADE_WINDOW_MS). */
export function leftWords(ms: number): string {
  const s = Math.ceil(ms / 1000);
  return `Touch and sign within ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}: what the server gave for it lasts five minutes from when it was prepared.`;
}

/** Who cancels an upgrade signed with a passkey while it waits (the server's cancel_by and cancel_by_code,
 *  e2e_keys.cancellers: its rules (i) to (iii), counting hardware keys alone while high security is on, in each), and
 *  what a new code is worth if it is: before it is signed, in words. One that keeps the code goes with the code's
 *  proof, which only the code cancels (codeWaitWords). */
export const UPGRADE_CANCEL_WORDS = "Until it takes effect, any passkey of your account it does not remove cancels it, and so does the recovery code you have now; where it leaves your account no passkey but the one that signs it, the passkeys it removes cancel it too. While high security is on, hardware keys alone count, both for who cancels it and for what it leaves.";
/** What holds while an upgrade waits (the server's rule (v), as for any revocation that waits). */
export const UPGRADE_MEANWHILE = "Until it takes effect the keys do not change: links keep sealing to the classical key. A change made meanwhile by whoever may cancel it, such as removing a passkey you lost, takes its place and ends it.";
/** What signing an upgrade that removes passkeys does at once (e2e_keys.revoke: part of what removing a passkey does,
 *  as a revocation's placement does it; the rest, passkeys.remove_in, comes as its hold ends): shown while it would
 *  remove one. */
export const UPGRADE_REMOVES_NOW = "If it removes a passkey, signing it does part of what removing that passkey does, at once: the app connections it allowed end, the link tickets it confirmed that are not used yet are voided, and the devices it approved are refused or signed out; and every approval of your account still waiting on its hold (a session's request to be linked, an app's connection) is cancelled. Cancelling the upgrade gives none of these back. The rest comes only as the upgrade takes effect, with the passkey's removal: the commands and moves it signed that no session has taken are withdrawn, and a link it resumed is stopped again.";
/** Where the passkeys an upgrade removes are added again (the server's rule (v): a passkey added while a change waits
 *  takes its place and ends it, where what confirms the adding may cancel it, and is refused otherwise). */
export const UPGRADE_ADD_AFTER = "Add it again once the upgrade has taken effect: a passkey added while it waits takes its place and ends it, where what confirms the adding may cancel the upgrade, and is refused otherwise.";
/** What adding a passkey from Account does while encryption is on and a change to its keys waits (the server's rule
 *  (v), e2e_keys.hold_passkey: the passkey added takes that change's place and ends it, where what confirms the adding
 *  may cancel it, and is refused otherwise): said beside Add a passkey; null while nothing waits. */
export function addWhileWaitingWords(view: K.E2EView | null): string | null {
  if (!view?.on || !view.pending) return null;
  return `${changeName(view, view.pending)} waits: a passkey added now takes its place and ends it, where what confirms the adding may cancel it, and is refused otherwise. To keep it, add the passkey once it has taken effect.`;
}
/** A tab from before the upgrade, unlocked, checks a waiting upgrade as it checks any replacing of the keys
 *  (checkWaiting), finds the new keys do not open with its own, and says to cancel it (WAITING_BROKEN). */
export const UPGRADE_OTHER_TABS = "Reload or close any other tab of this site first: one from before this release, unlocked, checks the upgrade while it waits as it checks any replacing of the keys, finds that what it puts in place does not open with its own keys, and says to cancel it unless you know why.";
export const NEW_CODE_WORDS = "Your new recovery code, shown once. Write it down and keep it apart from your devices. It replaces the old one only when the upgrade takes effect: until then, and for good if the upgrade is cancelled, the code you have now is the one that unlocks your keys, and this one unlocks nothing.";

/** The fold's words where they differ from the upgrade's: turning a classical account
 *  on again while a new master key is due makes that key as the upgrade does, in the same change. */
export const FOLD_INTRO = "Turning it on again with post-quantum keys makes its new master key as the upgrade does, in the same change: a new content key, and a new post-quantum key for every passkey that unlocks encryption, each passkey touched once. It removes no passkey: remove one that is not at hand from your account first, or turn it on again with these keys instead.";
/** A tab from before the fold, unlocked, checks a waiting fold as a new master key made from its own, finds the new
 *  keys do not open with its own, and says to cancel it (WAITING_BROKEN). */
export const FOLD_OTHER_TABS = "Reload or close any other tab of this site first: one from before this release, unlocked, checks this change while it waits as it checks any new master key, finds that what it puts in place does not open with its own keys, and says to cancel it unless you know why.";
export const FOLD_NEW_CODE_WORDS = "Your new recovery code, shown once. Write it down and keep it apart from your devices. It replaces the old one only when this change takes effect: until then, and for good if it is cancelled, the code you have now is the one that unlocks your keys, and this one unlocks nothing.";
/** Who cancels the fold signed with a passkey while it waits (e2e_keys.cancellers: a turn-on removes no passkey, so every
 *  one of the account's, hardware keys alone while high security is on, and the code). */
export const FOLD_CANCEL_WORDS = "Until it takes effect, any passkey of your account cancels it, and so does the recovery code you have now. While high security is on, hardware keys alone count.";
/** What holds while the fold waits: encryption off; a passkey that unlocks encryption removed from the account leaves it
 *  to take no effect (ended at once where it signed it, else refused as its hold ends: its credential no longer stays);
 *  and the server's rule (v), as for any change that waits. */
export const FOLD_MEANWHILE = "Until it takes effect encryption stays off and the keys do not change: linked sessions send in the clear. If a passkey that unlocks encryption is removed from your account meanwhile, this change takes no effect; and a change made meanwhile by whoever may cancel it takes its place and ends it.";

/** The upgrade's screen: the recovery code kept (typed) or replaced (shown
 *  once, four characters typed back); then each passkey to keep but one touched once, each with its result; what the
 *  upgrade removes (every passkey neither touched nor signed with); the links that have not said they can seal
 *  post-quantum; how long it waits, who cancels it, and that a change made by whoever may cancel it ends it;
 *  then the signature, whose touch keeps the passkey that gives it. What was prepared is touched and signed within the
 *  time the server's challenges last (shown counting down), and prepared again, keeping the code, once it is up or a
 *  passkey's new key could not be made. Nothing is touched or signed while another change waits (the account as read
 *  now, `view`, which follows the server). With `fold`, the same screen for the fold (K.Fold): turning
 *  encryption on again with that new master key, every passkey that unlocks it touched but the signer (it removes
 *  none), and whether it seals in place what linked sessions sent in the clear while it was off. */
function UpgradeFlow({ ring, view, onDone, onCancel, fold = false, seal: sealFirst = true }: { ring: K.Ring; view: K.E2EView; onDone: (words: string, hold: string | null) => Promise<void>;
  onCancel: () => void; fold?: boolean; seal?: boolean }) {
  const [keep, setKeep] = useState<boolean | null>(null);
  const [flow, setFlow] = useState<K.Upgrade | null>(null);
  const [typed, setTyped] = useState("");
  const [ready, setReady] = useState(false);
  const [seal, setSeal] = useState(sealFirst);
  const [, setTouched] = useState(0);
  const { busy, error, setError, run } = useStep();
  useEffect(() => () => flow?.dispose(), [flow]);
  // drawn again each second while it is prepared, so the time left counts down
  useEffect(() => {
    if (!flow || !ready) return;
    const t = setInterval(() => setTouched((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [flow, ready]);
  const left = flow && ready ? flow.left() : 0;
  const choose = (k: boolean) => { flow?.dispose(); setKeep(k); setFlow(fold ? new K.Fold(ring, view, k) : new K.Upgrade(ring, view, k)); setTyped(""); setReady(false); setError(""); };
  const removes = flow && ready ? flow.removes() : [];
  const links = notPqWords(view, fold);
  // a change placed meanwhile, elsewhere: nothing is touched or signed until it is gone
  const waiting = !!view.pending;
  // the server can no longer use ML-KEM, which the upgrade's keys need: nothing is prepared, touched or signed until it
  // can again (the screen was opened while it could)
  const noMlkem = !view.mlkem;
  const stale = ready && left === 0;
  // prepared again: nothing is shown as to touch until it is
  const preparing = busy && ready && !!flow && !flow.seats.length;
  const spent = !!flow && ready && flow.seats.some((s) => !s.made && !s.held);
  const again = () => void run(async () => {
    try {
      await flow!.prepare(view);
    } finally {
      setTouched((n) => n + 1);
    }
  });
  const holdLength = lengthWords(view.holds?.recovery) ?? "on its hold";
  return (
    <div role="group" aria-label={fold ? "Turning encryption on again with post-quantum keys" : "Upgrading to post-quantum keys"} className="mt-2 rounded-lg border border-accent/40 bg-accent/[0.06] p-3 text-[12.5px]">
      <div>{fold ? FOLD_INTRO : <>The upgrade replaces every key in one change: a new master key, a new content key, and a new post-quantum key for each passkey you keep, each passkey touched once. A passkey neither touched nor signed with is removed from your account. {UPGRADE_ADD_AFTER}</>} Touch each on the device that holds it where you can (a passkey built into the device, or a key plugged in directly, keeps its answer off any network).</div>
      <div className="mt-2 text-wait" role="note">{fold ? FOLD_OTHER_TABS : UPGRADE_OTHER_TABS}</div>
      {noMlkem && <div className="mt-2 text-wait" role="note">{K.NO_MLKEM}</div>}
      {keep === null && (
        <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label="The recovery code">
          <button className="btn btn-sm" onClick={() => choose(true)}>Keep my recovery code</button>
          <button className="btn btn-sm" onClick={() => choose(false)}>Make a new recovery code</button>
          <button className="btn btn-sm" onClick={onCancel}>Cancel</button>
        </div>
      )}
      {flow && !ready && (
        <form className="mt-2" onSubmit={(e) => { e.preventDefault(); void run(async () => { await flow.useCode(typed); setTyped(""); await flow.prepare(view); setReady(true); }); }}>
          {keep ? <div className="text-fg-dim">Type the recovery code you have: it is checked against your keys, and its post-quantum key is made from it. Keeping it, {fold ? "turning it on again" : "the upgrade"} goes with the code's proof, waits {holdLength}, and only the code cancels it.</div>
            : <>
              <div className="text-fg-dim">{fold ? FOLD_NEW_CODE_WORDS : NEW_CODE_WORDS}</div>
              <div className="my-2 select-all rounded-md bg-bg px-3 py-2 text-center font-mono text-[15px] tracking-[0.15em]" aria-label="The new recovery code">{flow.codeText()}</div>
              <label htmlFor="e2e-upgrade-group" className="text-fg-dim">Type group {flow.group + 1} of the code:</label>
            </>}
          <div className="mt-2 flex gap-2">
            <input id="e2e-upgrade-group" aria-label={keep ? "Your recovery code" : undefined} className={`input h-7 font-mono uppercase ${keep ? "w-72" : "w-24"}`} autoComplete="off" spellCheck={false}
              value={typed} onChange={(e) => setTyped(e.target.value)} />
            <button className="btn btn-sm btn-primary" disabled={busy || !typed.trim() || noMlkem}>{busy ? <Spin /> : null} Next</button>
            <button type="button" className="btn btn-sm" disabled={busy} onClick={onCancel}>Cancel</button>
          </div>
        </form>
      )}
      {flow && ready && (
        <>
          <ul className="mt-2 divide-y divide-line rounded-lg border border-line" aria-label={fold ? "The passkeys that unlock it" : "The passkeys to keep"}>
            {flow.seats.map((s) => (
              <li key={s.passkey.passkey_id} className="flex items-center gap-3 px-3 py-2">
                <KeyRound size={13} className="flex-none text-muted" />
                <div className="min-w-0 flex-1 truncate">{s.passkey.name}</div>
                {s.made ? <span className="flex items-center gap-1 text-done"><Check size={12} /> {fold ? "Its new key is made" : "Kept: its new key is made"}</span>
                  : !s.held ? <span className="text-fail">Its new key could not be made: prepare it again</span>
                  : <button className="btn btn-sm" disabled={busy || waiting || stale || noMlkem} onClick={() => void run(async () => { try { await flow.touch(s.credential.credential_id); } finally { setTouched((n) => n + 1); } })}>
                    {busy ? <Spin /> : <KeyRound size={12} />} {fold ? "Touch it" : "Touch it to keep it"}</button>}
              </li>
            ))}
          </ul>
          <div className="mt-2 text-fg-dim">{fold ? "Touch each passkey but one, then sign with that one: signing gives it its new key too." : "Touch each passkey to keep but one, then sign with that one: signing keeps it too."}</div>
          {removes.length > 0 && <div className="mt-2 text-wait" role="note">Not touched yet, so the upgrade removes {removes.length === 1 ? "it" : "them"} from your account: {removes.map((id) => `“${nameOf(view, id)}”`).join(", ")}{removes.length === 1 ? ", unless you sign with it" : ", but for the one you sign with, if it is one of them"}. Add {removes.length === 1 ? "it" : "them"} again once the upgrade has taken effect.</div>}
          {removes.length > 0 && <div className="mt-2 text-wait" role="note">{UPGRADE_REMOVES_NOW}</div>}
          {links && <div className="mt-2 text-wait" role="note">{links}</div>}
          <div className="mt-2 text-fg-dim">{keep ? codeWaitWords(view) : `${waitWords(view, "long")} ${fold ? FOLD_CANCEL_WORDS : UPGRADE_CANCEL_WORDS}`} {fold ? FOLD_MEANWHILE : UPGRADE_MEANWHILE}</div>
          {fold && (
            <label className="mt-2 flex items-start gap-2 text-fg-dim">
              <input type="checkbox" className="mt-0.5" checked={seal} onChange={(e) => setSeal(e.target.checked)} />
              <span>Seal in place what linked sessions sent in the clear since it was off.</span>
            </label>
          )}
          {waiting ? <div className="mt-2 text-wait" role="note">{fold ? K.FOLD_WAITING : K.UPGRADE_WAITING}</div>
            : preparing ? <div className="mt-2 text-fg-dim" role="status">Preparing it again: a moment.</div>
            : stale ? <div className="mt-2 text-wait" role="note">{K.UPGRADE_STALE}</div>
            : <div className="mt-2 text-fg-dim" role="timer">{leftWords(left)}</div>}
          <div className="mt-2 flex gap-2">
            <button className="btn btn-sm btn-primary" disabled={busy || waiting || stale || noMlkem || !flow.signers().length} onClick={() => void run(async () => {
              const { answer, via } = flow instanceof K.Fold ? await flow.signAndSend(seal) : await flow.signAndSend();
              const removed = flow.removes().length > 0;            // the signer is kept now: what it removes
              setFlow(null);
              useE2EStore.getState().awaitKeys(via, true);           // they lead back to the keys held
              await onDone(fold
                ? `Turning encryption on again with post-quantum keys is signed. ${holdWords(answer)} This tab unlocks the new keys once it takes effect, while it stays open; elsewhere, unlock with a passkey that unlocks it now${keep ? " or the recovery code" : ", or the new recovery code"}. Linked sessions seal what they send from their next run after it${seal ? ", and what they sent in the clear while it was off is sealed in place" : ""}. If it is cancelled first, encryption stays off and the keys stay classical${keep ? "" : ", and the new code unlocks nothing"}.`
                : `The upgrade is signed. ${holdWords(answer)} This tab unlocks the new keys once it takes effect, while it stays open; elsewhere, unlock with a passkey you kept${keep ? " or the recovery code" : ", or the new recovery code"}. If it is cancelled first, the keys and your passkeys stay as they are${removed ? ", though what signing it ended at once stays ended" : ""}${keep ? "" : ", and the new code unlocks nothing"}.`, answer.hold?.id ?? null);
            })}>{busy ? <Spin /> : <KeyRound size={12} />} {fold ? "Sign turning it on again" : "Sign the upgrade"}</button>
            {(stale || spent) && <button className="btn btn-sm" disabled={busy || waiting || noMlkem} onClick={again}>Prepare again</button>}
            <button className="btn btn-sm" disabled={busy} onClick={onCancel}>Cancel</button>
          </div>
        </>
      )}
      <Err text={error} />
    </div>
  );
}

// ---- where it stands, turning it on, unlocking ----------------------------------------------------------------------

function Status({ view, ring, why, state, account, onDone, onTrusted, onAccount }: { view: K.E2EView | null; ring: K.Ring | null; why: string; state: string; account: string;
  onDone: () => Promise<void>; onTrusted: () => void; onAccount?: () => void }) {
  const keys = K.hasKeys(view);
  // keys older than this browser saw: said, and neither turning it off (below the keys) nor starting again is offered;
  // no keys where it saw the account's: said, with what an owner who restored the server does (RESTORED_TURN_ON), and
  // turning it on offered below it, as to a new account
  const rolled = view ? K.rolledBack(account, view) : null;
  const [said, setSaid] = useState("");
  const turnedOn = async (words: string) => { setSaid(words); await onDone(); };
  const unreached = ring?.unreached.length ?? 0;
  const unopened = ring?.unopened.length ?? 0;
  // turning it on waits on its hold (the change below): nothing more to turn on until it ends; while this tab waits on
  // the keys it made, another turning-on in its place is said
  const turningOn = view?.pending?.change === "turn-on";
  const waiting = useE2EStore((s) => s.waiting);
  const waitingOn = view?.pending && waiting && K.turnOnInPlace(view.pending, waiting.anchor)
    ? <div className="text-[12px] text-wait" role="status">{waiting.chains ? foldInPlaceWords(view, view.pending) : inPlaceWords(view, view.pending)}</div>
    : <div className="text-[12px] text-muted">{WAITING_ON}</div>;
  return (
    <Section title="End-to-end encryption" note="What your linked machines and sessions send you is stored encrypted: History titles and conversations, the mirror of a conversation, and their notes, questions and replies to you. Only a browser you unlock, with one of your passkeys or the recovery code, reads it; the server does not, nor does a copy of its database. Messages between sessions, checkpoints, your own commands, and names, times and sizes stay in the clear.">
      {said && <div className="mb-2 flex items-start gap-1.5 text-[12.5px] text-done" role="status"><Check size={13} className="mt-0.5 flex-none" /> {said}</div>}
      {view === null ? <div className="text-[12.5px] text-muted"><Loader2 size={13} className="mr-1 inline animate-spin" /> Loading…</div>
        : !keys ? (
          <>
            {rolled && <div className="mb-1 text-[12px] text-fail" role="alert"><span>{rolled}</span> <span>{RESTORED_TURN_ON}</span></div>}
            {turningOn ? waitingOn : <TurnOnFlow account={account} replace={false} onDone={turnedOn} />}
          </>
        )
        : (
          <>
            <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
              <span className={view.on ? "flex items-center gap-1 text-done" : "text-muted"}>{view.on && <ShieldCheck size={13} />}{view.on ? "On" : "Off"}</span>
              <span className="text-faint">·</span>
              <span className="flex items-center gap-1 text-fg-dim">{state === "unlocked" ? <LockOpen size={12} /> : <Lock size={12} />}{state === "unlocked" ? "unlocked in this tab" : "locked in this tab"}</span>
            </div>
            {!view.on && <div className="mt-1 text-[12px] text-muted">Linked sessions send in the clear. What was sealed before stays sealed: unlock to read it.</div>}
            {rolled && <div className="mt-1 text-[12px] text-fail" role="alert">{rolled}</div>}
            {view.sealed && <SealedInPlace sealed={view.sealed} compacting={view.compaction_due} />}
            {state !== "unlocked" && why && <div className="mt-1 text-[12px] text-wait" role="status">{why}</div>}
            {unreached > 0 && <div className="mt-1 text-[12px] text-wait" role="note">The server lists {unreached === 1 ? "a content key" : `${unreached} content keys`} under a master key that does not lead back to yours: what was sealed to {unreached === 1 ? "it" : "them"} is not opened here.</div>}
            {ring && unopened > 0 && <div className="mt-1 text-[12px] text-wait" role="note">{K.newestUnopened(ring, view) ? K.NEWEST_UNOPENED
              : `${unopened === 1 ? "A content key" : `${unopened} content keys`} the server lists under one of your master keys ${unopened === 1 ? "does" : "do"} not open: what was sealed to ${unopened === 1 ? "it" : "them"} is not opened here.`}</div>}
            <Unlock unlocked={state === "unlocked"} ring={ring} view={view} account={account} why={why} onTrusted={onTrusted} onAccount={onAccount} />
            {!view.on && (turningOn ? <div className="mt-3">{waitingOn}</div>
              : ring ? <TurnOnAgain view={view} ring={ring} onDone={turnedOn} />
              : rolled ? null
              : <div className="mt-3"><div className="mb-1 text-[12px] text-muted">Unlock to turn it on again with these keys, or start again: what was sealed under them is then never read again, and starting again waits a day.</div>
                <TurnOnFlow account={account} replace onDone={turnedOn} /></div>)}
          </>
        )}
    </Section>
  );
}

/** What sealing in place at a post-quantum turn-on deleted (the view's sealed.dropped): conversations that held a
 *  message sealed already, which cannot be sealed whole; each link sends its own again under the new key, if it still
 *  runs and lists that session (e2e_keys.seal_in_place). */
export function droppedWords(d: number): string {
  const one = d === 1;
  return `${d} ${one ? "conversation" : "conversations"} that held a message sealed already, to an earlier key, could not be sealed whole and ${one ? "was" : "were"} deleted: ${one ? "its linked session sends it" : "their linked sessions send them"} again under the new key, if ${one ? "it still runs and lists that session" : "they still run and list those sessions"}`;
}

/** What the last sealing in place sealed, and whether the database may still hold what it replaced. */
function SealedInPlace({ sealed, compacting }: { sealed: K.E2ESealed; compacting: boolean }) {
  const n = (sealed.events ?? 0) + (sealed.rows ?? 0) + (sealed.talks ?? 0);
  const dropped = typeof sealed.dropped === "number" && sealed.dropped > 0 ? sealed.dropped : 0;
  return (
    <div className="mt-1 text-[12px] text-muted">
      Sealed in place{sealed.at ? ` at ${dayTime(sealed.at)}` : ""}: {n} {n === 1 ? "text" : "texts"} linked sessions had sent in the clear{sealed.left ? `; ${sealed.left} could not be, sent without an id to seal them by` : ""}{dropped ? `; ${droppedWords(dropped)}` : ""}.
      {compacting && " The database may still hold what they were, in its free space or its log, until it is compacted."}
    </div>
  );
}

/** The passkeys that unlock encryption, by name and kind, and the recovery code. */
export function unlocksWithWords(view: K.E2EView): string {
  const named = view.credentials.flatMap((c) => {
    const p = view.passkeys.find((k) => k.passkey_id === c.passkey_id);
    return p ? [p] : [];
  });
  if (!named.length) return "No passkey unlocks it yet: only the recovery code does.";
  const said = named.map((p) => {
    const kind = kindName(p);
    return p.name.toLowerCase() === kind ? `“${p.name}”` : `“${p.name}” (${kind})`;
  });
  return `Unlocks with ${said.join(", ")}, or the recovery code.`;
}

/** How this device's own passkey comes to unlock encryption: added to the account, then, unlocked here another way,
 *  added to encryption; a hardware key while high security is on. */
export function thisDeviceWords(view: K.E2EView): string {
  // while encryption is off and keeps its keys, nothing is added under What unlocks it (its Add needs it on): none said
  if (!view.on) return "";
  const others = view.credentials.length ? "one of these or " : "";
  return `To unlock with a passkey of this device's: add it to your account, unlock here with ${others}the recovery code, then add it under What unlocks it, below.${view.high_security ? " While high security is on, it must be a hardware key." : ""}`;
}

function Unlock({ unlocked, ring, view, account, why, onTrusted, onAccount }: { unlocked: boolean; ring: K.Ring | null; view: K.E2EView; account: string; why: string;
  onTrusted: () => void; onAccount?: () => void }) {
  const [code, setCode] = useState("");
  const [trusted, setTrusted] = useState(false);
  const { busy, error, setError, run } = useStep();
  if (unlocked) {
    const via = ring?.via.kind === "recovery" ? "with the recovery code"
      : `with ${view.passkeys.find((p) => p.passkey_id === (ring?.via as { passkeyId?: string } | undefined)?.passkeyId)?.name ?? "a passkey"}`;
    return (
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px] text-muted">
        <span>Unlocked {via}. It locks after 30 minutes without your input, when you sign out, and here:</span>
        <button className="btn btn-sm" onClick={() => useE2EStore.getState().lock()}><Lock size={12} /> Lock</button>
      </div>
    );
  }
  // the keys the server holds do not lead back to the newest this browser opened: only the person says to trust them.
  // Asked at once where the view has fewer statements than this browser saw, since nothing is changed from it until
  // then (no unlock need be tried first); a classical view where it saw post-quantum keys is asked no such word. Trust
  // waits while an unlock here is on its way: answered after the floor is dropped, that unlock would open the keys with
  // no second click, where the note asks for one
  const rolledBack = error === K.ROLLED_BACK || why === K.ROLLED_BACK || K.rolledBack(account, view) === K.ROLLED_BACK;
  const trust = async () => {
    await K.dropFloor(account);
    setError("");
    useE2EStore.setState({ why: "" });
    setTrusted(true);
    onTrusted();                                      // what is said and offered follows the floor as it now is
  };
  return (
    <div className="mt-2">
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn btn-sm btn-primary" disabled={busy || !view.credentials.length} onClick={() => { setTrusted(false); void run(() => useE2EStore.getState().unlock()); }}>
          {busy ? <Spin /> : <KeyRound size={12} />} Unlock
        </button>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); setTrusted(false); void run(async () => { await useE2EStore.getState().unlockWithCode(code); setCode(""); }); }}>
          <input aria-label="Your recovery code" className="input h-7 w-64 font-mono uppercase" placeholder="Or the recovery code" autoComplete="off" autoCapitalize="characters" spellCheck={false}
            value={code} onChange={(e) => setCode(e.target.value)} />
          <button className="btn btn-sm" disabled={busy || !code.trim()}>Unlock with it</button>
        </form>
      </div>
      <Err text={error} />
      {rolledBack && (
        <div className="mt-2 rounded-lg border border-wait/50 bg-wait/[0.05] p-3 text-[12px]" role="group" aria-label="Keys older than this browser's">
          <div>The keys the server holds do not lead back to the newest this browser opened. Trust them only if you started encryption again yourself, or restored the server from a backup yourself: otherwise someone may have put an old copy of its keys back, which a passkey removed since could open.</div>
          <button className="btn btn-sm mt-2" disabled={busy} onClick={() => void trust()}>Trust the server&apos;s keys</button>
        </div>
      )}
      {trusted && <div className="mt-2 text-[12px] text-muted" role="status">This browser now takes the keys the server holds: unlock again.</div>}
      <div className="mt-2 text-[12px] text-muted" role="note" aria-label="What unlocks encryption">
        <span>{unlocksWithWords(view)}</span>{" "}<span>{thisDeviceWords(view)}</span>
        {onAccount && thisDeviceWords(view) && <>{" "}<button type="button" className="btn btn-sm" onClick={onAccount}>Add it on Account</button></>}
      </div>
      {view.credentials.length > 0 && <PasskeyCheckRow view={view} />}
    </div>
  );
}

/** What the passkey check found, in words (K.passkeyCheck). */
export function checkWords(r: K.PasskeyCheck): string {
  const by = r.attachment === "platform" ? "this device's own authenticator"
    : r.attachment === "cross-platform" ? "another device or a security key" : "an authenticator the browser does not name";
  const offered = r.offered === 1 ? "the one passkey offered" : `one of ${r.offered} offered`;
  const lengths = r.lengths.some((n) => n !== 32) ? ` (outputs of ${r.lengths.join(" and ")} bytes)` : "";
  const b = r.b === null ? "not given" : `${r.b} (for its hash)`;
  return `${r.name} (${offered}), answered by ${by}, which ${r.verified ? "verified you" : "did not say it verified you"}: A ${r.a} (for the test input), B ${b}${lengths}.`;
}

/** What the owner does with two checks (K.passkeyCheck): Unlock opens the keys here where both show the same A, or
 *  where a device whose Unlock works shows as A this device's B (K.unlockAllowed); not otherwise. */
export const CHECK_COMPARE = "Run this check on a device where Unlock works too, and compare. If both show the same A, or that device's A is this device's B, Unlock works here as well. Otherwise this passkey gives this device another key, and Unlock cannot open your keys here: use the recovery code here.";

/** The passkey check beside Unlock: one touch, nothing sent to the server, what it found shown
 *  (K.passkeyCheck). */
function PasskeyCheckRow({ view }: { view: K.E2EView }) {
  const [found, setFound] = useState<K.PasskeyCheck | null>(null);
  const { busy, error, run } = useStep();
  return (
    <div className="mt-3 text-[12px]">
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn btn-sm" disabled={busy} onClick={() => void run(async () => setFound(await K.passkeyCheck(view)))}>
          {busy ? <Spin /> : <KeyRound size={12} />} Check a passkey on this device
        </button>
        <span className="text-muted">One touch, nothing sent to the server: what a passkey gives on this device for a test input.</span>
      </div>
      <Err text={error} />
      {found && <div className="mt-1.5 break-words font-mono text-[11.5px]" role="status" aria-label="Passkey check">{checkWords(found)}</div>}
      {found && <div className="mt-1 text-muted">{CHECK_COMPARE}</div>}
    </div>
  );
}

/** Turning encryption on: the recovery code shown once and a group of it typed back, then two touches of one passkey
 *  (the first signs, the second checks its PRF gives the same key), then one request. `replace` starts again, for an
 *  account that keeps keys from before. */
function TurnOnFlow({ account, replace, onDone }: { account: string; replace: boolean; onDone: (words: string) => Promise<void> }) {
  const [flow, setFlow] = useState<K.TurnOn | null>(null);
  const [step, setStep] = useState<"code" | "sign" | "again" | null>(null);
  const [shown, setShown] = useState("");
  const [typed, setTyped] = useState("");
  const [ready, setReady] = useState(false);
  const [seal, setSeal] = useState(true);
  const toSeal = useE2EStore((s) => s.view?.to_seal ?? null);
  // the page makes post-quantum keys alone: where the server's probe found no ML-KEM, that is said before a code is
  // shown, not after it is written down (TurnOn.prepare refuses it then too); where it loses ML-KEM once the code is
  // shown, nothing more is prepared, touched or sent until it can again
  const mlkem = useE2EStore((s) => s.view?.mlkem !== false);
  const { busy, error, setError, run } = useStep();
  useEffect(() => () => flow?.dispose(), [flow]);
  const prepare = (t: K.TurnOn) => {
    setReady(false);
    void K.E2EApi.view().then((v) => t.prepare(v)).then(() => setReady(true), (e) => setError(message(e)));
  };
  // a nonce lasts five minutes: made again after four while the page waits for the click
  useEffect(() => {
    if (step !== "sign" || !ready || !flow) return;
    const t = setTimeout(() => prepare(flow), 240000);
    return () => clearTimeout(t);
  }, [step, ready, flow]);    // eslint-disable-line react-hooks/exhaustive-deps
  const start = () => {
    const t = new K.TurnOn(account, replace);
    setFlow(t); setShown(t.codeText()); setTyped(""); setError(""); setStep("code");
  };
  const stop = () => { flow?.dispose(); setFlow(null); setShown(""); setStep(null); };
  const confirmCode = () => {
    if (!flow?.codeMatches(typed)) { setError(`That is not group ${(flow?.group ?? 0) + 1} of the code.`); return; }
    setShown(""); setError(""); setStep("sign"); prepare(flow);
  };
  const n = (toSeal?.events ?? 0) + (toSeal?.rows ?? 0) + (toSeal?.talks ?? 0);
  if (step === null) {
    return (
      <div>
        <button className={`btn btn-sm ${replace ? "" : "btn-primary"}`} disabled={!mlkem} title={mlkem ? undefined : K.NO_MLKEM} onClick={start}><Lock size={12} /> {replace ? "Start again" : "Turn on"}</button>
        {!mlkem && <div className="mt-1 text-[12px] text-wait" role="note">{K.NO_MLKEM}</div>}
        <Err text={error} />
      </div>
    );
  }
  return (
    <div role="group" aria-label="Turning encryption on" className="rounded-lg border border-accent/40 bg-accent/[0.06] p-3 text-[12.5px]">
      {!mlkem && <div className="mb-2 text-[12px] text-wait" role="note">{K.NO_MLKEM}</div>}
      {step === "code" && (
        <>
          <div>Your recovery code. It unlocks everything when every passkey is lost. Write it down and keep it apart from your devices: it is shown once, and losing it with every passkey loses what was encrypted.</div>
          <div className="my-2 select-all rounded-md bg-bg px-3 py-2 text-center font-mono text-[15px] tracking-[0.15em]" aria-label="The recovery code">{shown}</div>
          <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); confirmCode(); }}>
            <label htmlFor="e2e-code-group" className="text-fg-dim">Type group {flow!.group + 1} of the code:</label>
            <input id="e2e-code-group" className="input h-7 w-24 font-mono uppercase" autoComplete="off" spellCheck={false} maxLength={6} value={typed} onChange={(e) => setTyped(e.target.value)} />
            <button className="btn btn-sm btn-primary" disabled={!typed.trim() || !mlkem}>Next</button>
            <button type="button" className="btn btn-sm" onClick={stop}>Cancel</button>
          </form>
        </>
      )}
      {step === "sign" && (
        <>
          <div>Sign it with one of your passkeys: that one unlocks encryption from now on, and you add others after. Then touch it once more, to check it gives the same key twice.</div>
          {toSeal && n > 0 && (
            <label className="mt-2 flex items-start gap-2 text-fg-dim">
              <input type="checkbox" className="mt-0.5" checked={seal} onChange={(e) => setSeal(e.target.checked)} />
              <span>Seal in place what linked sessions sent in the clear before: {n} {n === 1 ? "text" : "texts"}{toSeal.left ? `; ${toSeal.left} cannot be, sent without an id to seal them by` : ""}. Copies in backups and free disk space stay as they were.</span>
            </label>
          )}
          <div className="mt-2 flex gap-2">
            <button className="btn btn-sm btn-primary" disabled={busy || !ready || !mlkem} onClick={() => void run(async () => { await flow!.firstTouch(); setStep("again"); })}>
              {busy || !ready ? <Spin /> : <KeyRound size={12} />} Sign with a passkey
            </button>
            <button className="btn btn-sm" disabled={busy} onClick={stop}>Cancel</button>
          </div>
        </>
      )}
      {step === "again" && (
        <>
          <div>Now touch the same passkey again.</div>
          <div className="mt-2 flex gap-2">
            <button className="btn btn-sm btn-primary" disabled={busy || !mlkem} onClick={() => void run(async () => {
              try { await flow!.secondTouch(); } catch (e) { stop(); throw e; }
              const { answer, ring, via } = await flow!.send(seal);
              setFlow(null);
              setStep(null);
              if (ring) {
                useE2EStore.getState().adopt(ring);
                await onDone("Encryption is on. Linked sessions seal what they send from their next run; one that cannot verify the key keeps its texts and says why.");
                return;
              }
              // it waits on its hold, as any change does: this tab unlocks the keys once it has taken effect
              useE2EStore.getState().awaitKeys(via);
              await onDone(`Turning encryption on is signed. ${holdWords(answer)} The recovery code you wrote down unlocks it only if this change takes effect: if it is cancelled or replaced first, that code unlocks nothing. This tab unlocks it then, while it stays open; elsewhere, unlock it with your passkey. Linked sessions seal what they send from their next run after it${seal ? ", and what they sent in the clear before is sealed in place" : ""}.`);
            })}>
              {busy ? <Spin /> : <KeyRound size={12} />} Touch it again
            </button>
            <button className="btn btn-sm" disabled={busy} onClick={stop}>Cancel</button>
          </div>
        </>
      )}
      <Err text={error} />
    </div>
  );
}

/** Where the fold is offered beside turning it on again with the keys as they are: a
 *  classical account's new master key due, and the server able to use ML-KEM. */
export function foldOfferWords(view: K.E2EView): string {
  return `Turn it on again with post-quantum keys to make that master key as the upgrade does, in the same change: it takes a touch of each passkey that unlocks encryption, and your recovery code or a new one, and it waits ${lengthWords(view.holds?.long) ?? "on its hold"}. Turning it on again with these keys keeps them classical, and the upgrade is then a change of its own.`;
}

/** Turning it on again with the keys kept from before, unlocked; or, for a classical account whose new master key is
 *  due, the fold, which makes that key as the upgrade does (K.Fold, its screen UpgradeFlow's), offered first. */
function TurnOnAgain({ view, ring, onDone }: { view: K.E2EView; ring: K.Ring; onDone: (words: string) => Promise<void> }) {
  const [folding, setFolding] = useState<{ seal: boolean } | null>(null);
  if (folding) {
    return (
      <div className="mt-3">
        <UpgradeFlow fold seal={folding.seal} ring={ring} view={view} onDone={async (words) => { setFolding(null); await onDone(words); }} onCancel={() => setFolding(null)} />
      </div>
    );
  }
  return <TurnOnAgainHere view={view} ring={ring} onDone={onDone} onFold={(seal) => setFolding({ seal })} />;
}

/** Turning it on again with the keys kept from before, unlocked: one touch signs a new content key. While a new master
 *  key is due (a passkey that unlocked it was removed, or left out by high security, while it was off), it makes one
 *  without those passkeys, as a revocation does, and says which stay; for a classical account, the fold is offered
 *  first where the server can use ML-KEM (`onFold`), and said not to be where it cannot. It waits on its hold, as
 *  every change does: what was signed is said where encryption's state is (onDone's words). */
function TurnOnAgainHere({ view, ring, onDone, onFold }: { view: K.E2EView; ring: K.Ring; onDone: (words: string) => Promise<void>; onFold: (seal: boolean) => void }) {
  const [seal, setSeal] = useState(true);
  const { busy, error, setError, run } = useStep();
  const flow = usePrepared(() => new K.TurnOnAgain(ring, view), setError, true, keysKey(ring, view));
  const due = K.rekeyDue(ring, view);
  const { kept, leaving } = K.rosterAfter(ring, view);
  const classicalDue = due && !ring.pq && !view.pq;
  // not while a change waits: the fold would show a new recovery code, then refuse at its first step (the upgrade's
  // button is disabled the same way); a waiting change is cancelled below first
  const folds = classicalDue && view.mlkem && !view.pending;
  return (
    <div className="mt-3">
      {due && ring.roster && (
        <div className="mb-2 text-[12px] text-fg-dim">
          {leaving.length ? `${sentence(leaving.map((c) => nameOf(view, c.passkey_id)).join(", "))} no longer ${leaving.length === 1 ? "unlocks" : "unlock"} encryption: removed, or left out by high security, while it was off. ` : "A passkey that unlocked encryption was removed, or left out by high security, while it was off. "}
          Turning it on again makes a new master key without {leaving.length === 1 ? "it" : "them"}, as replacing the keys does. {keptWords(view, kept.map((c) => c.passkey_id))}
          {abandonWords(ring) && ` ${abandonWords(ring)}`}
        </div>
      )}
      {folds && <div className="mb-2 text-[12px] text-fg-dim">{foldOfferWords(view)}</div>}
      {classicalDue && !view.mlkem && <div className="mb-2 text-[12px] text-wait" role="note">{K.NO_MLKEM}</div>}
      <label className="flex items-start gap-2 text-[12px] text-fg-dim">
        <input type="checkbox" className="mt-0.5" checked={seal} onChange={(e) => setSeal(e.target.checked)} />
        <span>Seal in place what linked sessions sent in the clear since it was off.</span>
      </label>
      <div className="mt-2 flex flex-wrap gap-2">
        {folds && <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => onFold(seal)}><ShieldCheck size={12} /> Turn it on again with post-quantum keys</button>}
        <button className={`btn btn-sm ${folds ? "" : "btn-primary"}`} disabled={busy || !flow} onClick={() => void run(async () => {
          const answer = await flow!.signAndSend(seal);
          await onDone(answer.hold ? `Turning encryption on again is signed. ${holdWords(answer)}` : "Encryption is on again.");
        })}>
          {busy || !flow ? <Spin /> : <Lock size={12} />} Turn it on again
        </button>
      </div>
      <Err text={error} />
    </div>
  );
}

/** What made the change that waits, and what it removes, in words: the passkey that signed or confirmed it, the
 *  recovery code, or a linked session, and where the code made it or its proof came with it (by_code) that only the
 *  code cancels it; the passkeys it removes, and the recovery code where it replaces it. */
export function madeWords(view: K.E2EView, pending: K.E2EPending): string {
  const made = pending.made_by === "recovery" ? "Made with the recovery code: only the code cancels it."
    : pending.made_by && pending.by_code ? `Made with ${nameOf(view, pending.made_by)} and the recovery code's proof: only the code cancels it.`
    : pending.made_by ? `Made with ${nameOf(view, pending.made_by)}.` : pending.change === "upload" ? "Sent in by a linked session." : "";
  const removes = [...pending.removes.map((id) => nameOf(view, id)), ...(pending.replaces_code ? ["the recovery code"] : [])];
  return [made, removes.length ? `It removes ${removes.join(", ")}.` : ""].filter(Boolean).join(" ");
}

/** The change that waits, checked with the keys this tab unlocked (K.checkWaiting), and again whenever those keys, what
 *  unlocked them, or the change's body change; null while it is checked, when the tab is locked, or when there is
 *  nothing these keys open of it. */
function useWaitingCheck(ring: K.Ring | null, view: K.E2EView, pending: K.E2EPending): K.WaitingCheck | null {
  const [checked, setChecked] = useState<K.WaitingCheck | null>(null);
  const inputs = useRef({ ring, view });
  inputs.current = { ring, view };
  const key = JSON.stringify([ring?.master.id ?? null, ring?.rosterSealed ?? null, ring ? [...ring.keys.keys()] : null,
    ring ? (ring.via.kind === "credential" ? ring.via.credentialId : "recovery") : null, pending.id, pending.change, pending.body]);
  useEffect(() => {
    let live = true;
    setChecked(null);
    const { ring: r, view: v } = inputs.current;
    if (r) K.checkWaiting(r, v).then((c) => { if (live) setChecked(c); }, () => { if (live) setChecked({ state: "broken", why: K.WAITING_BROKEN }); });
    return () => { live = false; };
  }, [key]);
  return checked;
}

const CHECKED_COLOUR = { opens: "text-done", leaves: "text-wait", broken: "text-fail" } as const;

/** The change that waits, and cancelling it with exactly what the server lists as cancelling it (cancel_by,
 *  cancel_by_code): not with the sign-in alone, which whoever holds a stolen device has, but with one touch of one of
 *  those passkeys (its challenge prepared, so the click reaches the prompt first, and prepared again whenever who may
 *  cancel it changes), or the recovery code in force. It says what made the change and what it removes, and what the
 *  keys this tab unlocked make of what it puts in place (useWaitingCheck), or, locked, that unlocking checks that. */
function Pending({ pending, view, ring, account, onDone }: { pending: K.E2EPending; view: K.E2EView; ring: K.Ring | null; account: string; onDone: () => Promise<void> }) {
  const [code, setCode] = useState("");
  const { busy, error, setError, run } = useStep();
  const flow = usePrepared(() => new K.CancelChange(account, view, pending), setError, true,
    JSON.stringify([pendingKey(pending), view.passkeys.map((p) => [p.passkey_id, p.credential_id])]));
  const checked = useWaitingCheck(ring, view, pending);
  const cancel = new K.CancelChange(account, view, pending);
  const names = cancel.passkeys().map((p) => p.name);
  const byCode = cancel.byCode();
  // on a post-quantum account the server cannot take the code's proof, nor make a revocation, without ML-KEM
  const codeNow = cancel.codeCancels();
  const noMlkem = view.pq && !view.mlkem;
  const touch = `a touch of ${names.join(" or ")}`;
  const made = madeWords(view, pending);
  const told = noticeWords(pending.told);
  return (
    <div className="mb-6 rounded-lg border border-wait/50 bg-wait/[0.05] p-3 text-[12.5px]" role="status">
      <div>{changeName(view, pending)} waits until {dayTime(pending.takes_effect_at)}.{told ? ` ${told}` : ""} Cancel it if it is not yours.</div>
      {made && <div className="mt-1 text-[12px] text-fg-dim">{made}</div>}
      {checked && <div className={`mt-1 text-[12px] ${CHECKED_COLOUR[checked.state]}`}>{checked.why}</div>}
      {!ring && K.hasKeys(view) && K.checkable(pending) && (
        <div className="mt-1 text-[12px] text-muted">Unlock encryption in this tab to check what it puts in place against your keys.</div>
      )}
      <div className="mt-1 text-[12px] text-muted">
        {names.length && codeNow ? `Signing in alone does not cancel it: it takes ${touch}, or the recovery code.`
          : names.length ? `Signing in alone does not cancel it: it takes ${touch}.${byCode ? ` ${K.CODE_NEEDS_MLKEM}` : ""}`
          : byCode ? `Signing in alone does not cancel it, and no passkey of yours can: it takes the recovery code.${codeNow ? "" : ` ${K.CODE_NEEDS_MLKEM}`}`
          : "Signing in alone does not cancel it, and no passkey of yours can: nothing left to you cancels it."}
      </div>
      {view.on && pending.made_by && pending.made_by !== "recovery" && !noMlkem && (
        <div className="mt-1 text-[12px] text-muted">Not yours? Remove the passkey that made it (What unlocks it, below): that revocation takes its place in one step, with the recovery code where none of your passkeys that stay could cancel it. Cancelling it and then removing the passkey leaves a moment in which another can be placed.</div>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {names.length > 0 && (
          <button className="btn btn-sm" disabled={busy || !flow} onClick={() => void run(async () => { await flow!.withPasskey(); await onDone(); })}>
            {busy || !flow ? <Spin /> : <KeyRound size={12} />} Cancel it with a passkey
          </button>
        )}
        {!codeNow ? null : ring?.via.kind === "recovery" ? (
          <button className="btn btn-sm" disabled={busy} onClick={() => void run(async () => { await cancel.withCode({ ring }); await onDone(); })}>
            Cancel it with the recovery code
          </button>
        ) : (
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void run(async () => { await cancel.withCode({ code }); setCode(""); await onDone(); }); }}>
            <input aria-label="The recovery code, to cancel it" className="input h-7 w-56 font-mono uppercase" placeholder="Or the recovery code" autoComplete="off" autoCapitalize="characters" spellCheck={false}
              value={code} onChange={(e) => setCode(e.target.value)} />
            <button className="btn btn-sm" disabled={busy || !code.trim()}>Cancel it with the code</button>
          </form>
        )}
      </div>
      <Err text={error} />
    </div>
  );
}

// ---- the keys ------------------------------------------------------------------------------------------------------

/** Why a security key the browser said gives no PRF does not unlock encryption yet, and where it is added: some browsers
 *  give a security key none. */
export const KEY_NO_PRF_WORDS = "the browser it was added in did not say it gives PRF, as some browsers, Safari among them, do not for a security key. Adding it here finds out whether this one does; if not, add it in another browser, such as Chrome or Firefox, on a computer: on an iPhone or iPad, whose browsers use Safari's engine, another browser may not help";

function Keys({ view, ring, onDone }: { view: K.E2EView; ring: K.Ring | null; onDone: () => Promise<void> }) {
  const [doing, setDoing] = useState<{ what: "add" | "remove" | "code" | "rotate" | "anchors"; id?: string } | null>(null);
  const close = async () => { setDoing(null); await onDone(); };
  const needsUnlock = !ring;
  // keys whose roster did not open, or does not name the key that unlocked them, read but add, remove and renew nothing
  const noRoster = ring && !ring.roster ? ring.rosterWhy : "";
  const hardwareOnly = view.high_security;
  return (
    <Section title="What unlocks it" note={`Each passkey here unlocks encryption with one touch, or the recovery code does. ${hardwareOnly ? "High security is on: only a hardware key unlocks it, and only one can be added." : "While high security is on, only a hardware key can be added."} A passkey is added with two touches, the second to check it gives the same key twice; removing one replaces the keys, so the removed one opens nothing.`}>
      <ul className="mb-3 divide-y divide-line rounded-lg border border-line">
        {view.passkeys.map((p) => {
          const enrolled = view.credentials.find((c) => c.passkey_id === p.passkey_id);
          // named by the roster with the credential and the key the server lists, not by the passkey alone
          const listed = ring?.roster && enrolled ? K.isNamed(ring, enrolled) : null;
          // the roster's word alone: it says no new master key is sealed to it, never why
          const status = enrolled && listed === false ? <span className="text-wait">Not named by the roster of these keys: no new master key is sealed to it</span>
            : enrolled ? <span className="text-done">Unlocks encryption{enrolled.created_at ? `, since ${dayTime(enrolled.created_at)}` : ""}</span>
            : p.can_enrol === false ? <span>{hardwareOnly && p.tier !== "hardware" ? "High security is on: only a hardware key unlocks encryption" : "Does not unlock encryption"}</span>
            : <span>Does not unlock encryption yet{p.prf === false ? (isSecurityKey(p) ? `: ${KEY_NO_PRF_WORDS}` : ": this browser did not say it gives PRF when it was made, and adding it finds out") : ""}</span>;
          return (
            <li key={p.passkey_id} className="px-3 py-2.5">
              <div className="flex items-center gap-3">
                <KeyRound size={14} className="flex-none text-muted" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px]">{p.name}</div>
                  {kindName(p) === "hardware key"
                    ? <div className="text-[11.5px] text-done"><ShieldCheck size={11} className="mr-1 inline" />{kindWords(p)}</div>
                    : <div className="text-[11.5px] text-muted">{kindWords(p)}</div>}
                  <div className="text-[11.5px] text-muted">{status}</div>
                </div>
                {view.on && !enrolled && p.can_enrol !== false && (
                  <button className="btn btn-sm" disabled={needsUnlock || !!noRoster} title={needsUnlock ? "Unlock first: the master key is sealed to the passkey added" : noRoster || undefined}
                    onClick={() => setDoing({ what: "add", id: p.passkey_id })}><KeyRound size={12} /> Add</button>
                )}
                {view.on && (
                  <button className="btn btn-sm" aria-label={`Remove ${p.name}`} disabled={needsUnlock || !!noRoster} title={needsUnlock ? "Unlock first: removing a passkey replaces the keys" : noRoster || undefined}
                    onClick={() => setDoing({ what: "remove", id: p.passkey_id })}><Trash2 size={12} /> Remove</button>
                )}
              </div>
              {ring && doing?.id === p.passkey_id && doing.what === "add" && <EnrolFlow ring={ring} view={view} passkey={p} onDone={close} onCancel={() => setDoing(null)} />}
              {ring && doing?.id === p.passkey_id && doing.what === "remove" && (
                <RevokeFlow ring={ring} view={view} what={{ remove: [p.passkey_id] }} label={`Removing “${p.name}”`}
                  words={`Removing “${p.name}” from your account replaces the keys: a new master key, sealed to the passkeys that stay and the recovery code, and a new content key every link seals to. What it signed that no session has taken is withdrawn. One touch of another passkey signs it.`}
                  onDone={close} onCancel={() => setDoing(null)} />
              )}
            </li>
          );
        })}
        <li className="flex items-center gap-3 px-3 py-2.5">
          <Lock size={14} className="flex-none text-muted" />
          <div className="min-w-0 flex-1">
            <div className="text-[13px]">Recovery code</div>
            <div className="text-[11.5px] text-muted">{view.recovery?.created_at ? `Made ${dayTime(view.recovery.created_at)}. ` : ""}Keep it offline, apart from your devices.</div>
          </div>
          {view.on && <button className="btn btn-sm" disabled={needsUnlock || !!noRoster} title={noRoster || undefined} onClick={() => setDoing({ what: "code" })}>New recovery code</button>}
        </li>
      </ul>
      {!view.on && <div className="mb-2 text-[12px] text-muted">While encryption is off, a passkey is added to it, or a new recovery code made, once it is on again. A passkey removed under Passkeys takes its key with it, and turning encryption on again then makes a new master key without it.</div>}
      {view.on && needsUnlock && <div className="mb-2 text-[12px] text-muted">Unlock to add, remove, rotate or renew: each opens the master key.</div>}
      {noRoster && <div className="mb-2 text-[12px] text-wait" role="note">{noRoster}</div>}
      {ring && doing?.what === "code" && <NewCodeFlow ring={ring} view={view} onDone={close} onCancel={() => setDoing(null)} />}
      {view.on && (
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-sm" disabled={needsUnlock} onClick={() => setDoing({ what: "rotate" })}><RefreshCw size={12} /> Rotate the key</button>
          <button className="btn btn-sm" disabled={needsUnlock || !!noRoster} title={noRoster || undefined} onClick={() => setDoing({ what: "anchors" })}>Renew anchors</button>
        </div>
      )}
      {ring && doing?.what === "rotate" && <RotateFlow ring={ring} view={view} onDone={close} onCancel={() => setDoing(null)} />}
      {ring && doing?.what === "anchors" && <AnchorsFlow ring={ring} view={view} onDone={close} onCancel={() => setDoing(null)} />}
    </Section>
  );
}

/** Adding a passkey to encryption: two touches of it, the first sealing the key to it and the second checking it gives
 *  the same key again, which is also its own confirmation that it is the passkey added (the server takes the key only
 *  with it); then it is confirmed by a touch of a passkey that unlocks encryption already and still does (one
 *  that may take the place of the change that waits), or by the recovery code, which waits longer and which only the
 *  code cancels; only by the code while a change waits that none of those passkeys may take the place of. Made again,
 *  from its first touch, when the keys change meanwhile. */
function EnrolFlow({ ring, view, passkey, onDone, onCancel }: { ring: K.Ring; view: K.E2EView; passkey: K.E2EPasskey; onDone: () => Promise<void>; onCancel: () => void }) {
  const [step, setStep] = useState<"first" | "second" | "confirm" | "sent">("first");
  const [answer, setAnswer] = useState<K.Answer | null>(null);
  const { busy, error, setError, run } = useStep();
  const flow = usePrepared(() => new K.Enrol(ring, view, passkey), setError, false, keysKey(ring, view));
  const { rk, setKey } = useRecoveryKey(view, ring);
  useEffect(() => { setStep((s) => (s === "sent" ? s : "first")); }, [flow]);
  const confirmers = flow?.confirmers().map((p) => p.name) ?? [];
  const sent = (a: K.Answer) => { setAnswer(a); setStep("sent"); };
  return (
    <div role="group" aria-label={`Adding “${passkey.name}”`} className="mt-2 rounded-lg border border-accent/40 bg-accent/[0.06] p-3 text-[12.5px]">
      {step === "first" && <>
        <div>Touch {passkey.name} twice: the first seals the key to it, the second checks it gives the same key again and confirms, from {passkey.name} itself, that it is the passkey being added. Then a passkey that unlocks encryption already confirms it, or the recovery code does.</div>
        <div className="mt-2 flex gap-2">
          <button className="btn btn-sm btn-primary" disabled={busy || !flow} onClick={() => void run(async () => { await flow!.firstTouch(); setStep("second"); })}>{busy || !flow ? <Spin /> : <KeyRound size={12} />} Touch it</button>
          <button className="btn btn-sm" disabled={busy} onClick={onCancel}>Cancel</button>
        </div>
      </>}
      {step === "second" && <>
        <div>Now touch {passkey.name} again.</div>
        <div className="mt-2 flex gap-2">
          <button className="btn btn-sm btn-primary" disabled={busy || !flow} onClick={() => void run(async () => { await flow!.secondTouch(); setStep("confirm"); })}>
            {busy ? <Spin /> : <KeyRound size={12} />} Touch it again
          </button>
          <button className="btn btn-sm" disabled={busy} onClick={onCancel}>Cancel</button>
        </div>
      </>}
      {step === "confirm" && flow && <>
        {flow.byCode
          ? <div>A change waits that none of your passkeys that unlock encryption may take the place of, but the recovery code can: adding it goes with the code. {codeWaitWords(view)}</div>
          : <div>Now confirm it with a touch of {confirmers.join(" or ")}, which unlocks encryption already. {waitWords(view, "change")} Or confirm it with the recovery code instead. {codeWaitWords(view)}</div>}
        <CodeField view={view} account={ring.account} ring={ring} rk={rk} onKey={setKey} run={run} busy={busy} label="The recovery code, to confirm adding it" />
        <div className="mt-2 flex flex-wrap gap-2">
          {!flow.byCode && (
            <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void run(async () => sent(await flow.confirmAndSend()))}>
              {busy ? <Spin /> : <KeyRound size={12} />} Confirm with a passkey
            </button>
          )}
          <button className={`btn btn-sm ${flow.byCode ? "btn-primary" : ""}`} disabled={busy || !rk} onClick={() => void run(async () => sent(await flow.sendWithCode(rk!)))}>
            Confirm with the recovery code
          </button>
          <button className="btn btn-sm" disabled={busy} onClick={onCancel}>Cancel</button>
        </div>
      </>}
      {step === "sent" && <>
        <div className="text-done">{passkey.name} is added{answer?.hold?.takes_effect_at ? " when its hold ends" : ""}. {holdWords(answer)}</div>
        <button className="btn btn-sm mt-2" onClick={() => void onDone()}>Close</button>
      </>}
      <Err text={error} />
    </div>
  );
}

/** A revocation, prepared when shown and made again when the keys change, and signed with one touch: what no longer
 *  unlocks encryption after it and what does, and how long it waits, in words; the change that waits whose place it
 *  takes. It is signed by a passkey that unlocks encryption and still does after it, one that may take the place of the
 *  change that waits; where none may (byCode: a change waits that only the code cancels, or no passkey stays after it),
 *  only with the recovery code's proof, the code typed here and checked before the touch unless the keys were unlocked
 *  with it, in one step, by any passkey of the account. Before it is signed, a warning when the passkeys it removes
 *  could still cancel it (it leaves no passkey but its signer), with the remedy: send it with the code, which only the
 *  code cancels then. For one that leaves no passkey, the person's word that only the recovery code is wanted; for high
 *  security with no hardware key enrolled, an offer to add one first. */
function RevokeFlow({ ring, view, what, label, words, onDone, onCancel, onAddHardware }: { ring: K.Ring; view: K.E2EView; what: K.RevokeWhat; label: string; words: string;
  onDone: () => Promise<void>; onCancel: () => void; onAddHardware?: () => void }) {
  const [answer, setAnswer] = useState<K.Answer | null>(null);
  const [onlyCode, setOnlyCode] = useState(false);
  const { busy, error, setError, run } = useStep();
  const flow = usePrepared(() => new K.Revoke(ring, view, { ...what, onlyCode }), setError, true, `${keysKey(ring, view)}|${onlyCode}`);
  const { rk, setKey } = useRecoveryKey(view, ring);
  const { kept, leaving } = K.rosterAfter(ring, view, what);
  const waiting = view.pending;
  const waits = waiting ? CHANGES[waiting.change] ?? "A change to encryption" : null;
  // only with the recovery code's proof (byCode): no passkey that stays after it may take the waiting change's place, or
  // none stays; as the flow prepared says, or the view while it is prepared again; one that could not be prepared (no
  // passkey kept, no hardware key kept) offers no code
  const byCode = flow ? flow.byCode : !error && K.onlyWithCode(view, { ...what, onlyCode });
  const staying = K.stayingMakers(view, what).staying.length > 0;
  // the passkeys it removes that could still cancel it, signed by a passkey alone: the code's proof is the remedy
  const stillCancel = K.removedMayCancel(view, what).map((id) => nameOf(view, id));
  const codeOffered = byCode || stillCancel.length > 0;
  return (
    <div role="group" aria-label={label} className="mt-2 rounded-lg border border-wait/50 bg-wait/[0.05] p-3 text-[12.5px]">
      {answer ? <>
        <div className="text-done">Signed. {holdWords(answer)}</div>
        <button className="btn btn-sm mt-2" onClick={() => void onDone()}>Close</button>
      </> : <>
        <div>{words}</div>
        {leaving.length > 0 && <div className="mt-1 text-fg-dim">No longer unlocks encryption: {leaving.map((c) => nameOf(view, c.passkey_id)).join(", ")}.</div>}
        <div className="mt-1 text-fg-dim">{keptWords(view, kept.map((c) => c.passkey_id), !!what.newCode)}</div>
        {abandonWords(ring) && <div className="mt-1 text-fg-dim" role="note">{abandonWords(ring)}</div>}
        {waits && !byCode && <div className="mt-1 text-fg-dim">It takes the place of the change that waits ({waits}), signed with a passkey that could cancel that one.</div>}
        {!byCode && <div className="mt-1 text-fg-dim">{waitWords(view, K.revocationHold(view, what, false))}</div>}
        {byCode && (
          <div className="mt-2" role="group" aria-label="With the recovery code">
            <div className="text-fg-dim">
              {staying ? `A change waits (${waits ?? "a change to encryption"}) that no passkey that unlocks encryption and still does after this one could cancel, but the recovery code can: with the code, this one takes its place in one step. Cancelling that one and then revoking would leave a moment in which whoever made it could place another.`
                : `No passkey that unlocks encryption still does after it to sign it: it goes with the recovery code, and any passkey of your account signs it.${waits ? ` With the code it takes the place of the change that waits (${waits}) in one step.` : ""}`} {codeWaitWords(view)}
            </div>
            <CodeField view={view} account={ring.account} ring={ring} rk={rk} onKey={setKey} run={run} busy={busy} label="The recovery code, for this one to take its place" />
          </div>
        )}
        {stillCancel.length > 0 && !byCode && (
          <div className="mt-2" role="note">
            <div className="text-wait">It removes {stillCancel.join(", ")}, leaving no passkey but the one that signs it, so until it takes effect {stillCancel.join(" or ")} can still cancel it. Send it with the recovery code instead: {codeWait(view)}.</div>
            <CodeField view={view} account={ring.account} ring={ring} rk={rk} onKey={setKey} run={run} busy={busy} label="The recovery code, so that only it cancels this" />
          </div>
        )}
        {(onlyCode || error === K.NONE_KEPT) && <div className="mt-2"><OnlyCode checked={onlyCode} onChange={setOnlyCode} /></div>}
        {error === K.NO_HARDWARE_KEPT && onAddHardware && (
          <button className="btn btn-sm mt-2" onClick={onAddHardware}><KeyRound size={12} /> Add a hardware key to encryption</button>
        )}
        <div className="mt-2 flex flex-wrap gap-2">
          {!byCode && (
            <button className="btn btn-sm btn-primary" disabled={busy || !flow} onClick={() => void run(async () => setAnswer(await flow!.signAndSend()))}>
              {busy || !flow ? <Spin /> : <KeyRound size={12} />} Sign it
            </button>
          )}
          {codeOffered && (
            <button className={`btn btn-sm ${byCode ? "btn-primary" : ""}`} disabled={busy || !flow || !rk} onClick={() => void run(async () => setAnswer(await flow!.signAndSendWithCode(rk!)))}>
              {busy || !flow ? <Spin /> : <KeyRound size={12} />} Sign it, with the recovery code
            </button>
          )}
          <button className="btn btn-sm" disabled={busy} onClick={onCancel}>Cancel</button>
        </div>
      </>}
      <Err text={error} />
    </div>
  );
}

/** A new recovery code: shown once, a group of it typed back, then a revocation to it. The code's bytes are the ones
 *  the revocation is made from, not a copy, and are zeroed when this closes. */
function NewCodeFlow({ ring, view, onDone, onCancel }: { ring: K.Ring; view: K.E2EView; onDone: () => Promise<void>; onCancel: () => void }) {
  const [code, setCode] = useState<K.TurnOn | null>(null);    // a code, and which group to ask back
  const [typed, setTyped] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");
  // made as it opens and dropped as it closes (made again should React open it twice), so no copy is left behind
  useEffect(() => {
    const made = new K.TurnOn(ring.account);
    setCode(made);
    return () => made.dispose();
  }, [ring.account]);
  const what = useMemo(() => (code ? { newCode: code.code } : null), [code]);
  if (!code || !what) return null;
  if (confirmed) {
    return <RevokeFlow ring={ring} view={view} what={what} label="A new recovery code"
      words="When this change takes effect, the new code replaces the old one, which then opens nothing; if it is cancelled or replaced first, the old code still opens everything and the new one never does. It makes a new master key, sealed to your passkeys and the new code, and a new content key every link seals to. One touch of a passkey signs it." onDone={onDone} onCancel={onCancel} />;
  }
  return (
    <div role="group" aria-label="A new recovery code" className="mb-3 rounded-lg border border-accent/40 bg-accent/[0.06] p-3 text-[12.5px]">
      <div>Your new recovery code, shown once. Write it down and keep it apart from your devices. It replaces the old one only when the change it goes with takes effect: until then the old one still opens everything, and if that change is cancelled or replaced first, this one never does.</div>
      <div className="my-2 select-all rounded-md bg-bg px-3 py-2 text-center font-mono text-[15px] tracking-[0.15em]" aria-label="The new recovery code">{code.codeText()}</div>
      <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (code.codeMatches(typed)) setConfirmed(true); else setError(`That is not group ${code.group + 1} of the code.`); }}>
        <label htmlFor="e2e-new-code-group" className="text-fg-dim">Type group {code.group + 1} of the code:</label>
        <input id="e2e-new-code-group" className="input h-7 w-24 font-mono uppercase" autoComplete="off" spellCheck={false} maxLength={6} value={typed} onChange={(e) => setTyped(e.target.value)} />
        <button className="btn btn-sm btn-primary" disabled={!typed.trim()}>Next</button>
        <button type="button" className="btn btn-sm" onClick={onCancel}>Cancel</button>
      </form>
      <Err text={error} />
    </div>
  );
}

/** A routine rotation: signed by a passkey that unlocks encryption and still does, one that may take the place of the
 *  change that waits; where none may, only with the recovery code's proof, by any passkey of the account. */
function RotateFlow({ ring, view, onDone, onCancel }: { ring: K.Ring; view: K.E2EView; onDone: () => Promise<void>; onCancel: () => void }) {
  const [answer, setAnswer] = useState<K.Answer | null>(null);
  const { busy, error, setError, run } = useStep();
  const flow = usePrepared(() => new K.Rotate(ring, view), setError, true, keysKey(ring, view));
  const { rk, setKey } = useRecoveryKey(view, ring);
  const byCode = flow ? flow.byCode : !error && K.onlyWithCode(view);
  return (
    <div role="group" aria-label="Rotating the key" className="mt-2 rounded-lg border border-accent/40 bg-accent/[0.06] p-3 text-[12.5px]">
      {answer ? <>
        <div className="text-done">Signed. {holdWords(answer)} Links seal to the new key at their next run; the old one is retired an hour after.</div>
        <button className="btn btn-sm mt-2" onClick={() => void onDone()}>Close</button>
      </> : <>
        <div>A new content key for links to seal to, under the same master key. Sign it with a passkey your links pinned, so they can verify it (Links, below). The server takes a routine rotation at most once a day unless it is set otherwise, and says so when it refuses one: a key that must go now goes with replacing the keys.</div>
        {byCode ? (
          <div className="mt-2" role="group" aria-label="With the recovery code">
            <div className="text-fg-dim">A change waits that none of your passkeys that unlock encryption may take the place of, but the recovery code can: this goes with the code, and any passkey of your account signs it. {codeWaitWords(view)}</div>
            <CodeField view={view} account={ring.account} ring={ring} rk={rk} onKey={setKey} run={run} busy={busy} label="The recovery code, to rotate the key with it" />
          </div>
        ) : <div className="mt-1 text-fg-dim">{waitWords(view, "change")}</div>}
        <div className="mt-2 flex gap-2">
          {byCode
            ? <button className="btn btn-sm btn-primary" disabled={busy || !flow || !rk} onClick={() => void run(async () => setAnswer(await flow!.signAndSendWithCode(rk!)))}>{busy || !flow ? <Spin /> : <KeyRound size={12} />} Sign it, with the recovery code</button>
            : <button className="btn btn-sm btn-primary" disabled={busy || !flow} onClick={() => void run(async () => setAnswer(await flow!.signAndSend()))}>{busy || !flow ? <Spin /> : <KeyRound size={12} />} Sign it</button>}
          <button className="btn btn-sm" disabled={busy} onClick={onCancel}>Cancel</button>
        </div>
      </>}
      <Err text={error} />
    </div>
  );
}

/** Renewing anchors: one touch of each passkey that unlocks encryption, each its own passkey's confirmation, and the
 *  recovery code's anchor, with the code this tab was unlocked with or the code typed here (no touch). The first
 *  passkey touched that may take the place of the change that waits confirms the change; where none may, the code
 *  given does (byCode: a change waits that only the code cancels). The code's anchor renewed goes only with the code's
 *  proof (the server's ANCHOR_CODE), so the renewal then waits the code's hold and only the code cancels it: said
 *  before the code is given, and again once its anchor is renewed. */
function AnchorsFlow({ ring, view, onDone, onCancel }: { ring: K.Ring; view: K.E2EView; onDone: () => Promise<void>; onCancel: () => void }) {
  const [, setTouched] = useState(0);
  const [typed, setTyped] = useState("");
  const [answer, setAnswer] = useState<K.Answer | null | "none">(null);
  const { busy, error, setError, run } = useStep();
  const flow = usePrepared(() => new K.RenewAnchors(ring, view), setError, false, keysKey(ring, view));
  const name = (c: K.E2ECredential) => view.passkeys.find((p) => p.passkey_id === c.passkey_id)?.name ?? "A passkey";
  const send = () => run(async () => setAnswer((await flow!.send()) ?? "none"));
  const code = () => run(async () => { await flow!.renewCode(flow!.codeHeld() ? undefined : typed); setTyped(""); setTouched((n) => n + 1); });
  const may = flow?.mayConfirm().map((id) => nameOf(view, id)) ?? [];
  const maker = flow?.maker() ?? null;
  return (
    <div role="group" aria-label="Renewing anchors" className="mt-2 rounded-lg border border-accent/40 bg-accent/[0.06] p-3 text-[12.5px]">
      <div>Each passkey's own record names the master key it was added under, and the recovery code's anchor the one it was made under. Renewing names the current one, so a master key kept from before (a removed passkey with an old copy of the database) cannot lead to a fork for it. One touch each, and the recovery code; skip one you do not have at hand. A passkey's key stays the same: what a removed passkey's holder took from it before, only removing that passkey ends.</div>
      {flow?.byCode && <div className="mt-2 text-fg-dim">A change waits that none of your passkeys that unlock encryption may take the place of, but the recovery code can: give the code to confirm this. Each passkey you touch still gives its own confirmation. {codeWaitWords(view)}</div>}
      {flow && !flow.byCode && !answer && (
        <div className="mt-2 text-fg-dim">
          {flow.recovery ? `The recovery code's anchor is renewed only with the code's proof, so this goes with it: ${codeWait(view)}. Each passkey you touch still gives its own confirmation.` : <>
            {flow.done.size > 0 && !maker && may.length > 0 && `To confirm it, touch ${may.join(" or ")} too: none of the passkeys touched may take the place of the change that waits. `}
            {waitWords(view, maker || !flow.codeDone ? "change" : "recovery")}
            {flow.hasCode() && !flow.codeDone && ` Where the recovery code's anchor needs renewing, that goes only with the code's proof: the renewal then waits ${lengthWords(view.holds?.recovery) ?? "on its hold"}, and only the code cancels it.`}
          </>}
        </div>
      )}
      {answer ? <>
        <div className="mt-2 text-done">{answer === "none" ? "Every anchor renewed was current already." : `Signed. ${holdWords(answer)}`}</div>
        <button className="btn btn-sm mt-2" onClick={() => void onDone()}>Close</button>
      </> : <>
        <div className="mt-2 flex flex-wrap gap-2">
          {flow?.targets().map((c) => (
            <button key={c.credential_id} className="btn btn-sm" disabled={busy || flow.done.has(c.credential_id)}
              onClick={() => void run(async () => { await flow.touch(c.credential_id); setTouched((n) => n + 1); })}>
              {flow.done.has(c.credential_id) ? <Check size={12} /> : <KeyRound size={12} />} {name(c)}
            </button>
          ))}
          {flow?.hasCode() && flow.codeHeld() && (
            <button className="btn btn-sm" disabled={busy || flow.codeDone} onClick={() => void code()}>
              {flow.codeDone ? <Check size={12} /> : <Lock size={12} />} Recovery code
            </button>
          )}
          <button className="btn btn-sm btn-primary" disabled={busy || !flow || (flow.done.size === 0 && !flow.codeDone)} onClick={() => void send()}>Send</button>
          <button className="btn btn-sm" disabled={busy} onClick={onCancel}>Cancel</button>
        </div>
        {flow?.hasCode() && !flow.codeHeld() && (flow.codeDone ? <div className="mt-2 flex items-center gap-1 text-done" role="status"><Check size={12} /> The recovery code's anchor is renewed.</div> : (
          <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); void code(); }}>
            <input aria-label="The recovery code, to renew its anchor" className="input h-7 w-56 font-mono uppercase" placeholder="The recovery code" autoComplete="off" autoCapitalize="characters" spellCheck={false}
              value={typed} onChange={(e) => setTyped(e.target.value)} />
            <button className="btn btn-sm" disabled={busy || !typed.trim()}>Renew the code's anchor</button>
          </form>
        ))}
      </>}
      <Err text={error} />
    </div>
  );
}

// ---- the stated key, the links -------------------------------------------------------------------------------------

/** The newest key stated, as its statement was signed (readView); or, while the server lists a statement other than it
 *  was signed, that, since no change is made from these keys then. */
function Stated({ view }: { view: K.E2EView }) {
  if (view.misstated.length) {
    return (
      <Section title="The key links seal to">
        <div role="alert" className="text-[12.5px] text-wait">{K.STATED_OTHERWISE}</div>
      </Section>
    );
  }
  const newest = view.statements.at(-1);
  if (!newest || newest.purpose !== "e2e-key") return null;
  const signer = view.passkeys.find((p) => p.passkey_id === newest.passkey_id);
  return (
    <Section title="The key links seal to">
      <div className="text-[12.5px] text-fg-dim">
        Key <code className="font-mono text-[11.5px]">{newest.key_id?.slice(0, 8)}</code>, statement {newest.seq}, signed with {signer ? <>{signer.name} (added {dayTime(signer.created_at)})</> : "a passkey this account no longer has"} on {dayTime(newest.created_at)}.
      </div>
    </Section>
  );
}

/** Whether a link's pins can be matched to the account's passkeys: they were read (true), are being read, or could
 *  not be read. */
export type PinsMatched = true | "reading" | "failed";

/** A link's pins, in words: the account's passkeys it pinned, by name, and how many keys the account no longer has;
 *  that it has not reported them; or, while the account's passkeys are not read, only how many there are. */
export function pinsWords(l: K.E2ELink, passkeys: (K.E2EPasskey & { fingerprint?: string })[], matched: PinsMatched = true): string {
  const pins = l.pins;
  if (pins === null) return "Pins: not reported yet.";
  if (!pins.length) return "Pins: none of your passkeys.";
  if (matched !== true) {
    const n = `${pins.length} ${pins.length === 1 ? "key" : "keys"}`;
    return matched === "failed" ? `Pins: ${n}, not matched: this account's passkeys could not be read.` : `Pins: ${n}, being matched to your passkeys.`;
  }
  const named = passkeys.filter((p) => p.fingerprint && pins.includes(p.fingerprint)).map((p) => p.name);
  const unknown = pins.length - named.length;
  return `Pins: ${named.length ? named.join(", ") : "none of your passkeys"}${unknown > 0 ? `; ${unknown} ${unknown === 1 ? "key" : "keys"} this account no longer has` : ""}.`;
}

/** What a link holds, and what to do so it can verify the newest key: nothing is advised about its pins until it has
 *  reported them and the account's passkeys were read to match them to (`matched`). */
export function linkWords(l: K.E2ELink, newest: K.E2EStatement | undefined, passkeys: (K.E2EPasskey & { fingerprint?: string })[], matched: PinsMatched = true): { state: string; advice: string | null } {
  const newestKey = newest?.key_id ?? null;
  const signer = passkeys.find((p) => p.passkey_id === newest?.passkey_id);
  const holding = l.last_required_at !== null && (l.key_id === null || l.key_id !== newestKey);
  const state = l.key_id === newestKey && newestKey ? "Seals to the newest key."
    : holding ? `Keeps its texts: the ADE refused it one in the clear at ${dayTime(l.last_required_at)}, and it holds no key it can verify.`
    : l.key_id ? `Seals to an older key (${l.key_id.slice(0, 8)}): it has not taken the newest yet.`
    : "Has sealed nothing yet.";
  if (l.key_id === newestKey && newestKey) return { state, advice: null };
  const pins = l.pins;
  if (pins === null) return { state, advice: "It has not reported which passkeys it pinned yet, so which key it can take is not known." };
  if (pins.length && matched !== true) {
    return { state, advice: matched === "failed" ? "Its pins could not be matched to your passkeys, which could not be read: reload this page to try again." : null };
  }
  const pinned = passkeys.filter((p) => p.fingerprint && pins.includes(p.fingerprint));
  if (!pinned.length) return { state, advice: "It pinned none of your passkeys: give it a pin line (Passkeys, in Account) in its own window." };
  if (signer && pinned.includes(signer)) return { state, advice: `It pinned ${signer.name}, which signed the newest key: it takes it at its next run.` };
  return { state, advice: `It did not pin ${signer?.name ?? "the passkey that signed the newest key"}: rotate the key signing with ${pinned.map((p) => p.name).join(" or ")}, which it pinned, or give it that passkey's pin line.` };
}

function Links({ view, keys }: { view: K.E2EView; keys: AccountKey[] | "failed" | null }) {
  const read = Array.isArray(keys) ? keys : [];
  const matched: PinsMatched = Array.isArray(keys) ? true : keys === "failed" ? "failed" : "reading";
  const passkeys = view.passkeys.map((p) => ({ ...p, fingerprint: read.find((k) => k.id === p.passkey_id)?.fingerprint }));
  const newest = view.statements.at(-1);
  if (!view.links.length) return null;
  return (
    <Section title="Links" note="What each linked session last sealed to, and the owner keys it says it pinned: it takes a new key only when a passkey it pinned signed it, or signed a later one.">
      <ul className="divide-y divide-line rounded-lg border border-line">
        {view.links.map((l) => {
          const w = linkWords(l, newest, passkeys, matched);
          return (
            <li key={l.link_id} className="px-3 py-2.5 text-[12.5px]">
              <div className="font-medium"><bdi>{l.name}</bdi></div>
              <div className={w.advice ? "text-wait" : "text-muted"}>{w.state}</div>
              <div className="text-[11.5px] text-faint">{pinsWords(l, passkeys, matched)}</div>
              {w.advice && <div className="text-[12px] text-fg-dim">{w.advice}</div>}
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

function TurnOff({ view, account, onDone }: { view: K.E2EView; account: string; onDone: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [answer, setAnswer] = useState<K.Answer | null>(null);
  return (
    <Section title="Turning it off" note="Linked sessions then send their titles, conversations and notes in the clear again, from their next run, and the server keeps them so. Nothing sealed is opened: it stays sealed, and this page reads it with your keys.">
      {answer ? <div className="text-[12.5px] text-done">Signed. {holdWords(answer)}</div>
        : !open ? <button className="btn btn-sm" onClick={() => setOpen(true)}>Turn off</button>
        : <TurnOffFlow view={view} account={account} onSigned={async (a) => { setAnswer(a); await onDone(); }} onCancel={() => setOpen(false)} />}
    </Section>
  );
}

/** Turning encryption off: signed by a passkey that unlocks encryption and still does, one that may take the place of the
 *  change that waits; where none may, only with the recovery code's proof, by any passkey of the account. It waits the
 *  long hold either way. */
function TurnOffFlow({ view, account, onSigned, onCancel }: { view: K.E2EView; account: string; onSigned: (a: K.Answer) => Promise<void>; onCancel: () => void }) {
  const { busy, error, setError, run } = useStep();
  const flow = usePrepared(() => new K.TurnOff(view, account), setError, true, keysKey(null, view));
  const ring = useE2EStore((s) => s.ring);
  const { rk, setKey } = useRecoveryKey(view, ring);
  const byCode = flow ? flow.byCode : !error && K.onlyWithCode(view);
  return (
    <>
      {byCode ? (
        <div className="mb-2 text-[12px]" role="group" aria-label="With the recovery code">
          <div className="text-fg-dim">A change waits that none of your passkeys that unlock encryption may take the place of, but the recovery code can: turning it off goes with the code, and any passkey of your account signs it. {codeWaitWords(view)}</div>
          <CodeField view={view} account={account} ring={ring} rk={rk} onKey={setKey} run={run} busy={busy} label="The recovery code, to turn it off with it" />
        </div>
      ) : <div className="mb-2 text-[12px] text-fg-dim">{waitWords(view, "long")}</div>}
      <div className="flex gap-2">
        {byCode
          ? <button className="btn btn-sm btn-danger" disabled={busy || !flow || !rk} onClick={() => void run(async () => onSigned(await flow!.signAndSendWithCode(rk!)))}>
            {busy || !flow ? <Spin /> : <KeyRound size={12} />} Sign turning it off, with the recovery code
          </button>
          : <button className="btn btn-sm btn-danger" disabled={busy || !flow} onClick={() => void run(async () => onSigned(await flow!.signAndSend()))}>
            {busy || !flow ? <Spin /> : <KeyRound size={12} />} Sign turning it off
          </button>}
        <button className="btn btn-sm" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
      <Err text={error} />
    </>
  );
}

// ---- for the Passkeys section, while encryption is on ---------------------------------------------------------------

/** Removing a passkey, or turning high security on, while encryption is on: a revocation the unlocked page signs, or
 *  why it cannot be made yet. `onAddHardware` opens Encryption, where a hardware key is added first. */
export function RevokeFromAccount({ what, label, words, onDone, onCancel, onAddHardware }: { what: K.RevokeWhat; label: string; words: string;
  onDone: () => Promise<void>; onCancel: () => void; onAddHardware?: () => void }) {
  const ring = useE2EStore((s) => s.ring);
  const view = useE2EStore((s) => s.view);
  if (!ring || !view) {
    return (
      <div role="group" aria-label={label} className="mt-3 rounded-lg border border-wait/50 bg-wait/[0.05] p-3 text-[12.5px]">
        <div>End-to-end encryption is on: {label.charAt(0).toLowerCase() + label.slice(1)} replaces its keys, so unlock it first (Encryption, in this window).</div>
        <button className="btn btn-sm mt-2" onClick={onCancel}>Close</button>
      </div>
    );
  }
  if (!ring.roster) {
    // keys whose roster did not open, or does not name the key that unlocked them: no new master key is made from them
    return (
      <div role="group" aria-label={label} className="mt-3 rounded-lg border border-wait/50 bg-wait/[0.05] p-3 text-[12.5px]">
        <div>{ring.rosterWhy}</div>
        <button className="btn btn-sm mt-2" onClick={onCancel}>Close</button>
      </div>
    );
  }
  return <RevokeFlow ring={ring} view={view} what={what} label={label} words={words} onDone={onDone} onCancel={onCancel} onAddHardware={onAddHardware} />;
}

/** What removing a passkey, or turning high security on, does to the keys while encryption is off and keeps them: no
 *  revocation (one would turn encryption on), but the server deletes what the passkey unlocked, and turning encryption
 *  on again makes a new master key without it. */
export const OFF_KEYS_WORDS = "End-to-end encryption is off and keeps its keys: what this passkey unlocked goes with it, and turning encryption on again makes a new master key without it.";
export const OFF_HIGH_WORDS = "End-to-end encryption is off and keeps its keys: once high security is on, turning encryption on again makes a new master key sealed to your hardware keys alone.";
