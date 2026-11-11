import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Bell, Check, CircleAlert, Copy, FileCheck2, HardDrive, KeyRound, Loader2, Lock, LogOut, Plug, ShieldCheck, Smartphone, Trash2, Upload, UserPlus, Users } from "lucide-react";
import { pushOff, pushOn, pushState, pushTest, type PushState } from "../push";
import { APP, Api, api, DESKTOP, onThisMac, server, setToken, type ConnectedApp, type DeviceRequest, type Health, type User } from "../api";
import { hostLabel } from "../places";
import { confirmWays, confirmWithPasskey, confirmWithPassword, kindName, kindWords, makePasskey, newPasskeyOptions, passkeysHere, renamePasskey, type ConfirmWays, type NewPasskeyOptions } from "../passkey";
import { checkFiles, type Checked } from "../records";
import { useStore } from "../store";
import { useE2EStore } from "../e2e";
import { hasKeys, recoveryProof, STILL_LOADING, type RecoveryProof, type RevokeWhat } from "../e2eKeys";
import { addWhileWaitingWords, EncryptionPane, holdWords, OFF_HIGH_WORDS, OFF_KEYS_WORDS, RevokeFromAccount } from "./Encryption";
import { ConnectedAppsPane, KEEP_WORKING, StillConnected } from "./ConnectedApps";
import { focusables, Modal } from "./Modal";
import { SignersSection } from "./Signers";
import { StorageChoice } from "./StorageChoice";
import { TaskCustody } from "./TaskCustody";
import { Verdict } from "./bits";
import { clock, dayTime } from "../when";
import { CLIENT, FINGERPRINT, inForm } from "../shellForms";

export type AccountTab = "account" | "storage" | "encryption" | "notifications" | "people" | "verify" | "device" | "apps";

/** A passkey as the server lists it: its tier is "hardware" (a certified security key, by the FIDO metadata, with a
 *  PIN, that can derive a key and works with a phone; `maker` is its maker's name; `phone_by_browser`: the metadata
 *  lists no transports for its model, so working with a phone rests on the browser's word; `reported`: its model has
 *  since been reported revoked or compromised, and it still counts only because high security is on and every
 *  hardware key of the account is reported) or "device" (`why` says why). */
interface Passkey {
  id: string; name: string; created_at: number; last_used_at: number | null; backed_up: boolean; fingerprint?: string;
  tier?: "hardware" | "device"; maker?: string | null; why?: string | null; judge_again?: boolean;
  phone_by_browser?: boolean; reported?: string | null;
  /** what the server says it is (passkeys.kind_of: hardware, synced, security-key or device); none from a server before */
  kind?: string | null;
}
/** Whether this server holds a valid copy of the FIDO metadata, without which no key counts as a hardware key. */
interface Metadata { valid: boolean; next_update: string | null }
interface Invitation { email: string; created_by: string; created_at: number; expires_at: number; used_at: number | null }
interface PublicKey { alg: string; key: string; public_key: string }

const when = (t?: number | null) =>
  dayTime(t);
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The account: its passkeys and sign-ins, and the custody of its tasks between teammates; for an admin, the people
 *  invited; and checking what this deployment signed. */
export function AccountDialog({ tab: first = "account", deviceCode, onClose }: { tab?: AccountTab; deviceCode?: string; onClose: () => void }) {
  const user = useStore((s) => s.user);
  const [tab, setTab] = useState<AccountTab>(first === "people" && !user?.admin ? "account" : first);
  if (!user) return null;
  const tabs: [AccountTab, string, ReactNode][] = [
    ["account", "Account", <KeyRound key="a" size={14} />],
    ["storage", "Where your work lives", <HardDrive key="s" size={14} />],
    ["encryption", "Encryption", <Lock key="e" size={14} />],
    ["notifications", "Notifications", <Bell key="n" size={14} />],
    ...(user.admin ? [["people", "People", <Users key="p" size={14} />] as [AccountTab, string, ReactNode]] : []),
    ["device", "Sign in a device", <Smartphone key="d" size={14} />],
    ["apps", "Connected apps", <Plug key="c" size={14} />],
    ["verify", "Check a record", <FileCheck2 key="v" size={14} />],
  ];
  return (
    <Modal title="Account" onClose={onClose} width={780}>
      <div className="-mx-4 -my-4 flex min-h-[440px] flex-col sm:flex-row">
        <nav className="flex flex-none gap-1 overflow-x-auto border-b border-line p-2 sm:w-48 sm:flex-col sm:overflow-visible sm:border-b-0 sm:border-r">
          {tabs.map(([id, label, icon]) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              aria-current={tab === id ? "page" : undefined}
              className={`flex h-8 flex-none items-center gap-2 whitespace-nowrap rounded-md px-2.5 text-left text-[12.5px] ${tab === id ? "bg-active text-fg" : "text-muted hover:bg-hover hover:text-fg"}`}
            >
              {icon} {label}
            </button>
          ))}
        </nav>
        <div className="min-w-0 flex-1 px-5 py-5">
          {tab === "account" ? <AccountPane user={user} onEncryption={() => setTab("encryption")} />
            : tab === "storage" ? <StorageChoice onEncryption={() => setTab("encryption")} />
            : tab === "encryption" ? <EncryptionPane onAccount={() => setTab("account")} /> : tab === "notifications" ? <NotificationsPane />
            : tab === "people" ? <PeoplePane /> : tab === "device" ? <DevicePane fromLink={deviceCode} />
            : tab === "apps" ? <ConnectedAppsPane /> : <VerifyPane />}
        </div>
      </div>
    </Modal>
  );
}

function Section({ title, children, note }: { title: string; children: ReactNode; note?: ReactNode }) {
  return (
    <section className="mb-6 last:mb-0">
      <h3 className="mb-1 text-[13px] font-semibold">{title}</h3>
      {note && <p className="mb-3 text-[12px] text-muted">{note}</p>}
      {children}
    </section>
  );
}

/** The release the server of this account runs and the commit its image was built from, as GET /api/health says them
 *  (each "unknown" where the build kept none), small, under the account's address: what to quote when telling of a
 *  fault. Named the server's, since in the desktop and phone apps the page is the app's own build and the server
 *  another's. The commit is cut to 12 characters, its whole id in the title; nothing is shown until the answer comes,
 *  or if none does. */
export function BuildLine() {
  const [build, setBuild] = useState<Health | null>(null);
  useEffect(() => {
    let live = true;
    Api.health().then((h) => { if (live) setBuild(h); }, () => {});
    return () => { live = false; };
  }, []);
  if (typeof build?.release !== "string") return null;
  const commit = typeof build.commit === "string" && build.commit !== "unknown" ? build.commit : null;
  return (
    <div className="mt-1 text-[11.5px] text-faint" title={commit ?? undefined}>
      Server release {build.release}{commit ? ` · commit ${commit.slice(0, 12)}` : ""}
    </div>
  );
}

/** A device's code as devices show it (XXXX-XXXX): what the person typed, without its spaces and dashes, in capitals,
 *  as the server reads it. */
export function shownCode(code: string): string {
  const raw = code.replace(/[\s-]/g, "").toUpperCase();
  return raw.length === 8 ? `${raw.slice(0, 4)}-${raw.slice(4)}` : raw;
}

/** Signing in another device with this account: the code it shows, what it says it is, then the person's answer.
 *  Approving takes the person's own word as well as this sign-in (ConfirmIt), as a risky change does, since the device
 *  gets a sign-in of its own for 7 days: one of the account's passkeys or its password, or one of its hardware keys
 *  while its high security is on, made for this device's code and for nothing else. This Mac's own account, which has
 *  nothing to confirm with, is asked only to go ahead. Refusing takes nothing more. */
function DevicePane({ fromLink }: { fromLink?: string }) {
  const [code, setCode] = useState(fromLink ?? "");
  const [seen, setSeen] = useState<DeviceRequest | null>(null);
  const [done, setDone] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState<Change | null>(null);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try { await fn(); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };
  const answered = (r: { name: string; approved: boolean }) => {
    setDone(r.approved ? `${r.name} is signed in with this account.` : `${r.name} was refused.`);
    setSeen(null);
    setCode("");
    setLinked(false);
    setAsking(null);
  };
  const decide = (approve: boolean) => run(async () => {
    if (!approve) return answered(await Api.deviceRefuse(code));
    if (!seen) return;
    // the words say what is approved, and the confirmation is made for this code alone, which the approval names
    setAsking({ what: "device", label: `Signing in “${seen.name}” (code ${shownCode(code)})`, action: "Approve",
      ways: await confirmWays(), device: code,
      note: "Once approved, it is signed in with this account for 7 days, or until you sign out everywhere." });
  });
  // the code the pane holds now: a lookup's answer, or its refusal, that comes back after the code changed (typed over,
  // or a new link) is dropped, so the card never shows one request's name, time and network beside another's code
  const current = useRef(code);
  current.current = code;
  const lookUp = (asked: string) => run(async () => {
    try {
      const got = await Api.deviceLookup(asked);
      if (current.current === asked) setSeen(got);
    } catch (e) {
      if (current.current === asked) throw e;
    }
  });
  // a code that came in a link is looked up at once; the person still compares it with the device before approving.
  // A new link replaces what the pane holds: its code, its card, and a confirmation open for another code
  const [linked, setLinked] = useState(!!fromLink);
  useEffect(() => {
    if (!fromLink) return;
    current.current = fromLink;
    setCode(fromLink);
    setSeen(null);
    setLinked(true);
    setAsking(null);
    setDone("");
    void lookUp(fromLink);
  }, [fromLink]);      // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Section title="Sign in a device" note="Approve only a code you can see on a device of your own, in front of you now: someone who reads you a code is asking for your account.">
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); setDone(""); void lookUp(code); }}>
        <input aria-label="The code the device shows" className="input font-mono uppercase tracking-widest" placeholder="XXXX-XXXX" autoCapitalize="characters" autoCorrect="off" spellCheck={false}
          value={code} onChange={(e) => { setCode(e.target.value); setSeen(null); setLinked(false); setAsking(null); }} required />
        <button className="btn" disabled={busy || !code.trim()}>Look</button>
      </form>
      {seen && (
        <div className="mt-3 rounded-lg border border-wait/50 bg-wait/[0.05] p-3">
          {linked && (
            <div className="mb-2">
              <div className="text-center font-mono text-[22px] font-semibold tracking-[0.2em]" aria-label="The code from the link">{code.toUpperCase()}</div>
              <div className="text-[12px] text-fg-dim">This code came in a link. Approve it only if a device in front of you shows this same code and you just asked it to sign in.</div>
            </div>
          )}
          <div className="text-[12.5px]">A device that calls itself &ldquo;<bdi className="font-medium">{seen.name}</bdi>&rdquo; asks to be signed in with this account.</div>
          <DeviceOrigin seen={seen} />
          <div className="mt-2 flex gap-2">
            <button className="btn btn-sm btn-primary" disabled={busy || !!asking} onClick={() => void decide(true)}>Approve</button>
            <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => void decide(false)}>Refuse</button>
          </div>
          {asking && <ConfirmIt key={asking.device} change={asking} onConfirmed={async (confirmation) => answered(await Api.deviceApprove(asking.device ?? code, confirmation))} onCancel={() => setAsking(null)} />}
        </div>
      )}
      {done && <div className="mt-3 text-[12.5px] text-done">{done}</div>}
      {error && <div role="alert" className="mt-3 text-[12.5px] text-fail">{error}</div>}
    </Section>
  );
}

/** Where a device's request came from, as the server saw it, against this browser's own network: what the asker cannot
 *  choose, beside the name it can. */
export function DeviceOrigin({ seen }: { seen: Pick<DeviceRequest, "created_at" | "asked_from" | "viewer" | "same_network"> }) {
  const at = clock(seen.created_at);
  if (!seen.asked_from) return <div className="mt-1 text-[12px] text-fg-dim">It asked at {at}.</div>;
  const from = <span className="font-mono">{seen.asked_from}</span>;
  if (seen.same_network === false) {
    return (
      <div className="mt-1 text-[12px] text-wait" role="note">
        It asked at {at} from {from}, which is not this browser's network{seen.viewer ? <> (<span className="font-mono">{seen.viewer}</span>)</> : null}.
        A phone on mobile data would be one reason; a link or a message from someone else would not.
      </div>
    );
  }
  return <div className="mt-1 text-[12px] text-fg-dim">It asked at {at} from {from}{seen.same_network ? ", this browser's network too" : ""}.</div>;
}

/** A change the sign-in alone does not make (passkeys.py): it waits for the person's own word in ConfirmIt. The
 *  session channel's link changes (a ticket, a revoke) take the same word. `hardware`: a change only a hardware key
 *  confirms, whatever the account's high security (turning it on or off); while it is on, every change is one.
 *  `e2e`: end-to-end encryption is on, so a passkey or the recovery code confirms it and the password does not (adding a
 *  passkey, which could then be added to encryption). `note`: what it does besides, in words (to encryption's keys).
 *  `device`: approving the device waiting with this code, which the confirmation is made for, and nothing else.
 *  `subject`: otherwise, the change the confirmation is made for, as the server names it (passkeys.CHANGE), so it is
 *  spent on that change alone. `passkeyOnly`: a passkey's word alone, neither the password's nor the recovery code's
 *  (renaming a passkey while end-to-end encryption is on: passkeys.rename). `to`: a rename's new name. */
export interface Change { what: string; label: string; action: string; id?: string; ways: ConfirmWays; hardware?: boolean; e2e?: boolean; note?: string; device?: string; subject?: string; passkeyOnly?: boolean; to?: string }

/** What an account change's confirmation is made for (Change.subject): adding a passkey, removing or renaming the
 *  passkey `id`, turning high security on or off (`id`), signing out everywhere. */
export function accountSubject(change: Pick<Change, "what" | "id">): string {
  if (change.what === "add") return "passkey-add";
  if (change.what === "remove") return `passkey-remove:${change.id ?? ""}`;
  if (change.what === "rename") return `passkey-rename:${change.id ?? ""}`;
  if (change.what === "high") return `high-security-${change.id === "on" ? "on" : "off"}`;
  return "sign-out-everywhere";
}

/** A button that copies `text`, saying so for two seconds; where the clipboard is refused, it says that instead. */
export function CopyText({ label, text }: { label: string; text: string }) {
  const [said, setSaid] = useState("");
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); setSaid("Copied"); }
    catch { setSaid("This browser would not copy it"); }
    setTimeout(() => setSaid(""), 2000);
  };
  return <button className="btn btn-sm" onClick={() => void copy()}><Copy size={12} /> {said || label}</button>;
}

/** The line that pins a passkey added after a session linked: run in that session's own window with the client where
 *  SETUP_A.md installs it (CLIENT, never a file in that window's folder), NAME its link's name, as the link command
 *  names it (its directory is ~/.proofpane-ade/NAME unless it was given another). Null when the fingerprint is not in
 *  the form the server writes it (shellForms): no line is given then. */
export const pinLine = (fingerprint: string | null | undefined) => (inForm(FINGERPRINT, fingerprint)
  ? `${CLIENT} --dir "$HOME/.proofpane-ade/NAME" pin --add ${fingerprint}` : null);

/** Copy pin line, for a passkey that has one. */
function PinCopy({ pin }: { pin: string | null }) {
  return pin === null ? null : <CopyText label="Copy pin line" text={pin} />;
}

/** The person's own word for a change: one of the account's passkeys (only in a browser at the server's own address,
 *  which its passkeys are for) or its password. An account with neither (this Mac's own) is asked only to go ahead.
 *  While the account's high security is on, or for a change that needs one (Change.hardware), one of its hardware
 *  keys alone: no password is asked for. While end-to-end encryption is on (Change.e2e), a passkey or the recovery
 *  code, for an owner who lost every passkey: its proof (e2eKeys.recoveryProof) goes in place of a confirmation, from
 *  the keys the code unlocked in this tab, or from the code typed. */
export function ConfirmIt({ change, onConfirmed, onCancel }: { change: Change; onConfirmed: (confirmation?: string, proof?: RecoveryProof) => Promise<void>; onCancel: () => void }) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // it is the next step of a two-step action wherever it opens: the keyboard goes to its first field or button, inside
  // its group, whose label (the change) is then read
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = box.current;
    if (el) focusables(el)[0]?.focus();
  }, []);
  const account = useStore((s) => s.user?.id ?? "");
  const ring = useE2EStore((s) => s.ring);
  // the account's form, or null while its keys are not read yet: a proof made then would be of a guessed form
  const pq = useE2EStore((s) => (s.view ? s.view.pq === true : null));
  const { needed, password: hasPassword, passkeys } = change.ways;
  const hardware = !!change.hardware || !!change.ways.high_security;
  const e2e = !!change.e2e;
  const passkeyOnly = !!change.passkeyOnly && !e2e;     // a passkey's word alone: no password, no recovery code
  const hardwareKeys = change.ways.hardware ?? 0;
  const here = !APP && passkeysHere;
  const viaPasskey = hardware ? here && hardwareKeys > 0 : (needed || e2e || passkeyOnly) && here && passkeys > 0;
  const viaPassword = !hardware && !e2e && !passkeyOnly && needed && hasPassword;
  const byCode = e2e && ring?.via.kind === "recovery";
  const go = async (how: () => Promise<{ confirmation?: string; proof?: RecoveryProof }>) => {
    setBusy(true); setError("");
    try { const r = await how(); await onConfirmed(r.confirmation, r.proof); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };
  const ways = viaPasskey && hasPassword ? "one of your passkeys or your password" : viaPasskey ? "one of your passkeys" : "your password";
  const said = e2e
    ? `${change.label} takes ${hardware ? "one of your hardware keys" : "one of your passkeys"} or your recovery code as well as this sign-in, since end-to-end encryption is on: your password does not confirm it.${here ? "" : ` Passkeys work only in a browser at ${hostLabel(server())}.`}`
    : passkeyOnly
    ? `${change.label} takes ${hardware ? "one of your hardware keys" : "one of your passkeys"} as well as this sign-in, since end-to-end encryption is on: your password does not confirm it.${here ? "" : ` Passkeys work only in a browser at ${hostLabel(server())}.`}`
    : hardware
    ? hardwareKeys === 0 ? `${change.label} takes one of your hardware keys, and this account has none: add one first.`
      : !here ? `${change.label} takes one of your hardware keys, and they work only in a browser at ${hostLabel(server())}: confirm it there.`
      : `${change.label} takes one of your hardware keys as well as this sign-in${change.ways.high_security ? ", since high security is on" : ""}; your password and your other passkeys do not confirm it.`
    : !needed ? `${change.label}?` : !viaPasskey && !hasPassword
      ? `${change.label} takes one of your passkeys as well as this sign-in, and they work only in a browser at ${hostLabel(server())}: confirm it there.`
      : `${change.label} takes ${ways} as well as this sign-in, so a sign-in someone else has taken cannot do it.`;
  return (
    <div ref={box} role="group" aria-label={change.label} className="mt-3 rounded-lg border border-wait/50 bg-wait/[0.05] p-3">
      <div className="text-[12.5px]">{said}</div>
      {change.note && <div className="mt-1 text-[12px] text-fg-dim">{change.note}</div>}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {!hardware && !needed && !e2e && !passkeyOnly && <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void go(async () => ({}))}>{change.action}</button>}
        {viaPasskey && (
          <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void go(async () => ({ confirmation: await confirmWithPasskey(hardware, { device: change.device, change: change.subject }) }))}>
            <KeyRound size={12} /> {hardware ? "Use a hardware key" : "Use a passkey"}
          </button>
        )}
        {viaPassword && (
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void go(async () => ({ confirmation: await confirmWithPassword(password, { device: change.device, change: change.subject }) })); }}>
            <input type="password" aria-label="Your password" autoComplete="current-password" className="input h-7 w-44" placeholder="Your password"
              value={password} onChange={(e) => setPassword(e.target.value)} />
            <button className="btn btn-sm" disabled={busy || !password}>{busy ? <Loader2 size={12} className="animate-spin" /> : null} Confirm</button>
          </form>
        )}
        {e2e && byCode && (
          <button className="btn btn-sm" disabled={busy} onClick={() => void go(async () => ({ proof: await recoveryProof(account, "passkey", { ring }) }))}>
            Use the recovery code
          </button>
        )}
        {e2e && !byCode && (
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void go(async () => { if (pq === null) throw new Error(STILL_LOADING); return { proof: await recoveryProof(account, "passkey", { code, pq }) }; }); }}>
            <input aria-label="Your recovery code" autoComplete="off" spellCheck={false} className="input h-7 w-56 font-mono uppercase" placeholder="Or your recovery code"
              value={code} onChange={(e) => setCode(e.target.value)} />
            <button className="btn btn-sm" disabled={busy || !code.trim()}>Use the recovery code</button>
          </form>
        )}
        <button className="btn btn-sm" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
      {e2e && <div className="mt-2 text-[12px] text-muted">What the recovery code confirms waits a day before it is made, and each device of yours with notifications on is told.</div>}
      {error && <div role="alert" className="mt-2 text-[12.5px] text-fail">{error}</div>}
    </div>
  );
}

/** The Passkeys section's words: what a hardware key is, and what each change to a passkey asks for, by whether
 *  end-to-end encryption is on (`e2e`) and high security (`high`). */
export function passkeysNote(e2e: boolean, high: boolean): string {
  const what = "Sign in with this device's fingerprint, face or security key instead of the password. Each passkey stays on the device or in the password manager that made it. A certified security key with a PIN that can derive a key and works with a phone is a hardware key; any other passkey, synced or device-bound, or a security key that is not certified, counts as a device passkey here.";
  const word = high ? "one of your hardware keys" : "one of your passkeys";
  return e2e
    ? `${what} While end-to-end encryption is on, your password confirms no change to a passkey: adding one asks for ${word} or your recovery code first, renaming one asks for ${word}, and removing one replaces encryption's keys.`
    : `${what} Adding, renaming or removing one asks for ${high ? "one of your hardware keys" : "one of your passkeys or your password"} first.`;
}

/** What adding a passkey takes, said beside Add before anything is asked: the person's word as ConfirmIt
 *  asks for it, one of the account's passkeys or its password; one of its hardware keys while high security is on; a
 *  passkey or the recovery code while end-to-end encryption is on (`e2e`: the server's word, ways.e2e, where it gives
 *  one), the passkey then added once its wait ends, and the recovery code alone where no passkey is left. Empty for an
 *  account with nothing to confirm with (this Mac's own), which is asked for nothing. */
export function addTakesWords(ways: ConfirmWays, e2e: boolean): string {
  if (ways.high_security) {
    return `Adding a passkey takes a touch of one of your hardware keys first${e2e ? ", or your recovery code. It is added once its wait ends." : "."}`;
  }
  if (e2e && ways.passkeys === 0) {
    return "Adding a passkey takes your recovery code first: while end-to-end encryption is on, your password does not confirm it. It is added once its wait ends.";
  }
  if (e2e) {
    return "Adding a passkey takes a touch of one this account already has first, or your recovery code: while end-to-end encryption is on, your password does not confirm it. It is added once its wait ends.";
  }
  if (!ways.needed) return "";
  if (ways.passkeys === 0) return ways.password ? "Adding your first passkey takes your password first." : "";
  return ways.password ? "Adding a passkey takes a touch of one this account already has, or your password, first."
    : "Adding a passkey takes a touch of one this account already has first.";
}

function AccountPane({ user, onEncryption }: { user: User; onEncryption?: () => void }) {
  const signOut = useStore((s) => s.signOut);
  const [keys, setKeys] = useState<Passkey[] | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [everywhere, setEverywhere] = useState(false);
  const [signedOut, setSignedOut] = useState(0);     // each sign-out everywhere, which revokes every signer: Signers reads again
  const [asking, setAsking] = useState<Change | null>(null);
  const [made, setMade] = useState<NewPasskeyOptions | null>(null);      // confirmed: the next click makes the passkey
  const [high, setHigh] = useState(false);
  const [metadata, setMetadata] = useState<Metadata | null>(null);
  // what the account confirms a change with, read with the list: what adding takes is said before it is asked for
  const [ways, setWays] = useState<ConfirmWays | null>(null);
  // what Add is making: a passkey, or one on a security key (its own button, which asks the browser for one)
  const [addKey, setAddKey] = useState<"passkey" | "security-key">("passkey");
  const [renaming, setRenaming] = useState<{ id: string; value: string } | null>(null);
  // a security key's request answered by another kind of passkey (a phone reached by its code, a password manager):
  // what it made, and the passkey to remove where it is added already (none while it waits on its hold)
  const [notKey, setNotKey] = useState<{ kind: string; id?: string; name?: string } | null>(null);
  const [turnedOn, setTurnedOn] = useState("");       // what turning high security on withdrew, said once
  // while end-to-end encryption is on, removing a passkey and turning high security on are revocations (Encryption.tsx),
  // and a passkey added waits on a hold; while it is off and keeps its keys, they are confirmed here, saying what they
  // do to the keys (the server deletes what the passkey unlocked, and the next turn-on makes a new master key)
  const e2eOn = useE2EStore((s) => !!s.view?.on);
  const e2eKeys = useE2EStore((s) => hasKeys(s.view));
  // a passkey added while a change to the keys waits takes its place and ends it (or is refused): said beside the button
  const addWaiting = useE2EStore((s) => addWhileWaitingWords(s.view));
  const [revoking, setRevoking] = useState<{ what: RevokeWhat; label: string; words: string; key: string } | null>(null);
  const [held, setHeld] = useState("");
  const [turnedOff, setTurnedOff] = useState(false);  // high security was just turned off: what that leaves, said once
  // the Claude app connector (the server's oauth.py): whether this deployment has it on, when a lost device's steps say
  // that connected apps keep working; what removing a passkey said of them; and what signing out everywhere answered
  const [connectorOn, setConnectorOn] = useState(false);
  const [removedApps, setRemovedApps] = useState("");
  const [stillConnected, setStillConnected] = useState<{ apps: ConnectedApp[]; words: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const got = await api<{ passkeys: Passkey[]; high_security?: boolean; metadata?: Metadata }>("/api/account/passkeys");
      setKeys(got.passkeys);
      setHigh(!!got.high_security);
      setMetadata(got.metadata ?? null);
    } catch (e) { setError(message(e)); }
    try { setWays(await confirmWays()); } catch { setWays(null); }   // no words about adding, rather than wrong ones
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    let live = true;
    Api.connector().then((s) => { if (live) setConnectorOn(s.on === true); }).catch(() => { /* said as off */ });
    return () => { live = false; };
  }, []);
  const hardwareKeys = (keys ?? []).filter((k) => k.tier === "hardware");
  // a key still waiting to be judged could become a hardware key later, without a hardware key's word
  const waiting = (keys ?? []).some((k) => k.judge_again);
  // while high security is on, a session pins the hardware keys alone: only those are worth a pin line, and only one
  // whose fingerprint is in the form the server writes it has one
  const pinFor = (k: Passkey) => (!high || k.tier === "hardware" ? pinLine(k.fingerprint) : null);
  const pinnable = (k: Passkey) => pinFor(k) !== null;
  // while high security is on, the server keeps the account's last hardware key (passkeys.LAST_HARDWARE_KEY): its
  // Remove is not offered, with why, rather than refused after a touch
  const lastHardware = (k: Passkey) => high && k.tier === "hardware" && (keys ?? []).filter((x) => x.tier === "hardware").length === 1;
  // what a session linked while it was on still refuses once it is off: the other passkeys, until each is pinned
  const unpinned = (keys ?? []).flatMap((k) => {
    const pin = k.tier !== "hardware" ? pinLine(k.fingerprint) : null;
    return pin ? [{ k, pin }] : [];
  });

  const begin = async (change: Omit<Change, "ways">) => {
    setError(""); setMade(null); setAsking(null); setEverywhere(false); setRevoking(null); setHeld("");
    setRemovedApps(""); setStillConnected(null); setNotKey(null);
    try { setAsking({ ...change, subject: accountSubject(change), ways: await confirmWays() }); } catch (e) { setError(message(e)); }
  };
  const revoke = (r: Omit<NonNullable<typeof revoking>, "key">) => {
    setError(""); setMade(null); setAsking(null); setEverywhere(false); setHeld(""); setRemovedApps(""); setStillConnected(null);
    setRevoking({ ...r, key: `${r.label}-${Date.now()}` });
  };
  const revoked = async () => { setRevoking(null); await load(); };
  // a failure of the change itself (a confirmation that has expired, a passkey already gone) shows in ConfirmIt, which
  // stays open to try again
  const confirmed = (change: Change) => async (confirmation?: string, proof?: RecoveryProof) => {
    if (change.what === "add") {
      setMade(await newPasskeyOptions(confirmation, proof));
    } else if (change.what === "remove") {
      // the app connections the passkey allowed were revoked with it; the server says so, and that the rest keep working
      const r = await api<{ apps?: string }>(`/api/account/passkeys/${encodeURIComponent(change.id ?? "")}`, { method: "DELETE", body: { confirmation } });
      // this browser remembers it removed it, so it leaves the next master key whatever the server lists later
      useE2EStore.getState().removed(change.id ?? "");
      setRemovedApps(r.apps ?? "");
      await load();
    } else if (change.what === "rename") {
      // the new name the person confirmed, as the confirmation's words said it (Change.to), not the field as it is now
      await renamePasskey(change.id ?? "", change.to ?? "", confirmation);
      setRenaming(null);
      await load();
    } else if (change.what === "high") {
      const r = await api<{ high_security: boolean; withdrawn?: number; stopped?: string[]; cancelled?: string[] }>("/api/account/high-security", { method: "PUT", body: { on: change.id === "on", confirmation } });
      setTurnedOn(r.high_security ? withdrawnWords(r.withdrawn ?? 0, (r.stopped ?? []).length, (r.cancelled ?? []).length) : "");
      setTurnedOff(!r.high_security);
      await load();
    } else {
      // it ends no app connection: the server answers with those allowed in the last days, each listed with Revoke
      const r = await api<{ token: string; connected_apps?: ConnectedApp[]; apps?: string }>("/api/account/sign-out-everywhere", { method: "POST", body: { confirmation } });
      setToken(r.token);
      setEverywhere(true);
      setSignedOut((n) => n + 1);
      setStillConnected({ apps: r.connected_apps ?? [], words: r.apps ?? "" });
    }
    setAsking(null);
  };
  const make = async () => {
    if (!made) return;
    setBusy(true); setError("");
    try {
      // a name left empty is the server's to give, by what the passkey is
      const answer = await makePasskey(made, name, { securityKey: addKey === "security-key" });
      const hold = answer.held as { id: string; takes_effect_at: number } | undefined;
      setHeld(hold?.takes_effect_at ? `The passkey is added once its wait ends. ${holdWords({ hold })}` : "");
      // asked for a security key, the browser may have made another kind: said, with Remove where it is added already
      const kind = typeof answer.kind === "string" ? answer.kind : null;
      setNotKey(addKey === "security-key" && kind && kind !== "security-key" && kind !== "hardware"
        ? { kind, id: hold ? undefined : (answer.id as string | undefined), name: answer.name as string | undefined } : null);
      setName(""); setMade(null); await load();
    }
    catch (e) { setError(message(e)); }
    finally { setBusy(false); }
  };
  // Add a passkey and Add a security key ask for the same word, then make it each its own way
  const addAs = (kind: "passkey" | "security-key") => {
    setAddKey(kind);
    void begin({ what: "add", label: kind === "security-key" ? "Adding a security key" : "Adding a passkey", action: "Continue", e2e: e2eOn });
  };
  // a name is the owner's word on which passkey is which, which removing and revoking go by: renaming one takes the
  // person's word made for it, a passkey's alone while end-to-end encryption is on (passkeys.rename); left empty, the
  // server names it by what it is again
  const saveName = () => {
    if (!renaming) return;
    const k = (keys ?? []).find((x) => x.id === renaming.id);
    const to = renaming.value.trim();
    const from = k?.name ?? "this passkey";
    void begin({ what: "rename", id: renaming.id, to, action: "Rename it", passkeyOnly: e2eNow,
      label: to ? `Renaming \u201c${from}\u201d to \u201c${to}\u201d` : `Naming \u201c${from}\u201d by what it is` });
  };
  // the server's word that encryption is on (confirm_ways), where it gives one: encryption's own view may not be read yet
  const e2eNow = ways?.e2e ?? e2eOn;
  const addTakes = ways ? addTakesWords(ways, e2eNow) : "";
  const ask = (what: Change["what"]) => asking?.what === what &&
    <ConfirmIt key={`${asking.what}-${asking.id ?? ""}`} change={asking} onConfirmed={confirmed(asking)} onCancel={() => setAsking(null)} />;

  // the desktop app's window onto this Mac signs itself in: nothing to sign out of there. This Mac's account has no
  // password and no passkey (none can be made at its server's address), which the server says (can_sign_in_again); a
  // browser signed in as it may still sign out, to end that sign-in, and signs in again with another device
  const here = onThisMac();
  const local = useStore.getState().user?.can_sign_in_again === false;
  return (
    <>
      <Section title="Signed in" note={here ? "The account of the workspace on this Mac. The app signs it in each time it starts; it has no password."
        : local ? "This Mac's own account, which has no password: signed out here, this browser signs in again with another device, approved in the Proofpane Community app." : undefined}>
        <div className="flex flex-wrap items-center gap-2 text-[13px]">
          <span className="font-medium">{user.email}</span>
          {user.admin && <span className="pill text-accent">admin</span>}
          {!here && <button className="btn btn-sm ml-auto" onClick={signOut}><LogOut size={12} /> Sign out</button>}
        </div>
        <BuildLine />
      </Section>

      {!here && !local && <Section title="Passkeys" note={passkeysNote(e2eNow, high)}>
        {keys === null ? <div className="text-[12.5px] text-muted"><Loader2 size={13} className="mr-1 inline animate-spin" /> Loading…</div> : (
          <ul className="mb-3 divide-y divide-line rounded-lg border border-line">
            {keys.length === 0 && <li className="px-3 py-2.5 text-[12.5px] text-muted">No passkeys yet.</li>}
            {keys.map((k) => (
              <li key={k.id} className="flex items-center gap-3 px-3 py-2.5">
                <KeyRound size={14} className="flex-none text-muted" />
                <div className="min-w-0 flex-1">
                  {renaming?.id === k.id ? (
                    <form className="flex flex-wrap items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); void saveName(); }}>
                      <input aria-label={`New name for ${k.name}`} className="input h-7 min-w-0 flex-1" value={renaming.value} maxLength={60} autoFocus
                        placeholder="Empty: named by what it is" onChange={(e) => setRenaming({ id: k.id, value: e.target.value })} />
                      <button className="btn btn-sm" aria-label="Save the name" disabled={busy}>Save</button>
                      <button type="button" className="btn btn-sm" aria-label="Cancel renaming" disabled={busy} onClick={() => setRenaming(null)}>Cancel</button>
                    </form>
                  ) : <div className="truncate text-[13px]">{k.name}</div>}
                  <div className="text-[11.5px] text-faint">
                    Added {when(k.created_at)} · {k.last_used_at ? `last used ${when(k.last_used_at)}` : "not used yet"}{k.backed_up ? " · synced" : ""}
                  </div>
                  {k.tier === "hardware"
                    ? <div className="text-[11.5px] text-done"><ShieldCheck size={11} className="mr-1 inline" />Hardware key{k.maker ? `: ${k.maker}` : ""}</div>
                    : <div className="text-[11.5px] text-muted">{kindWords(k)}{k.why ? `: ${k.why}` : ""}</div>}
                  {k.tier === "hardware" && k.phone_by_browser && <div className="text-[11.5px] text-muted">That it works with a phone rests on the browser&apos;s word: the FIDO metadata lists no transports for its model.</div>}
                  {k.tier === "hardware" && k.reported && <div className="text-[11.5px] text-wait" role="alert">{k.reported} It still counts, since high security is on and every hardware key of yours is reported: add another hardware key, then remove this one.</div>}
                  {k.fingerprint && <div className="font-mono text-[11px] text-faint" title="What a linked session pins (ade_link.py pin)">fingerprint {k.fingerprint}</div>}
                  {lastHardware(k) && <div id={`last-hardware-${k.id}`} className="text-[11.5px] text-muted">Your last hardware key: it is not removed while high security is on. Add another hardware key first, or turn high security off.</div>}
                </div>
                <PinCopy pin={pinFor(k)} />
                {renaming?.id !== k.id && <button className="btn btn-sm" aria-label={`Rename ${k.name}`} onClick={() => setRenaming({ id: k.id, value: k.name })}>Rename</button>}
                {/* marked unavailable (aria-disabled), not disabled, so the keyboard still reaches it and reads why */}
                <button className="btn btn-sm aria-disabled:cursor-not-allowed aria-disabled:opacity-50" aria-label={`Remove ${k.name}`}
                  aria-disabled={lastHardware(k) || undefined}
                  aria-describedby={lastHardware(k) ? `last-hardware-${k.id}` : undefined} onClick={() => lastHardware(k) ? undefined : (e2eOn
                  ? revoke({ what: { remove: [k.id] }, label: `Removing \u201c${k.name}\u201d`,
                    words: `End-to-end encryption is on, so removing \u201c${k.name}\u201d replaces its keys: a new master key, sealed to the passkeys that stay and the recovery code, and a new content key every link seals to. What it signed that no session has taken is withdrawn. One touch of another passkey signs it.` })
                  : void begin({ what: "remove", id: k.id, label: `Removing \u201c${k.name}\u201d`, action: "Remove it", note: e2eKeys ? OFF_KEYS_WORDS : undefined }))}>
                  <Trash2 size={12} /> Remove
                </button>
              </li>
            ))}
          </ul>
        )}
        {metadata && !metadata.valid && (
          <div className="mb-3 text-[12px] text-wait" role="note">This server has no valid copy of the FIDO metadata yet, so no key counts as a hardware key; a security key added now is judged again once it has one.</div>
        )}
        {keys && keys.some(pinnable) && (
          <div className="mb-3 flex flex-wrap items-center gap-2 text-[12px] text-muted">
            <CopyText label="Copy fingerprints" text={keys.filter(pinnable).map((k) => k.fingerprint).join(" ")} />
            <span>A linked session acts on a command only when a passkey it pinned signed it. A session linked from this page pins your passkeys as it links: the link command carries them. After adding a passkey, give its pin line to each session already linked that should take your commands, in that session&apos;s own window, with the session&apos;s link name for NAME (Copy pin line); after removing one, run pin there again. These fingerprints are this server&apos;s word: a pin held outside it is only as good as where its fingerprints came from.</span>
          </div>
        )}
        {ask("remove")}
        {ask("rename")}
        {revoking && !revoking.what.highSecurity && <RevokeFromAccount key={revoking.key} what={revoking.what} label={revoking.label} words={revoking.words} onDone={revoked} onCancel={() => setRevoking(null)} />}
        {APP ? (
          <div className="text-[12px] text-muted">A passkey belongs to the server's website, so it is made in a browser: sign in at {hostLabel(server())} and add one there.</div>
        ) : passkeysHere ? (
          <>
            {addTakes && (
              <div role="note" aria-label="What adding a passkey takes" className="mb-2 rounded-lg border border-accent/40 bg-accent/[0.06] p-3 text-[12.5px]">
                <span>{addTakes}</span>
                {e2eNow && <>
                  {" "}<span>Unlocking end-to-end encryption with it is a second step, in Encryption: add it there under What unlocks it.{high ? " While high security is on, only a hardware key can be added there." : ""}</span>
                  {onEncryption && <>{" "}<button type="button" className="btn btn-sm" onClick={onEncryption}>Go to Encryption</button></>}
                </>}
              </div>
            )}
            <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); addAs("passkey"); }}>
              <input aria-label="The passkey's name" className="input min-w-0 flex-1" placeholder="Its name; left empty, it is named by what it is" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} />
              <button className="btn btn-primary" disabled={busy || !!made}><KeyRound size={13} /> Add a passkey</button>
              <button type="button" className="btn" disabled={busy || !!made} onClick={() => addAs("security-key")}><KeyRound size={13} /> Add a security key</button>
            </form>
          </>
        ) : <div className="text-[12px] text-muted">This browser cannot make passkeys.</div>}
        {!APP && passkeysHere && addWaiting && <div className="mt-2 text-[12px] text-wait" role="note">{addWaiting}</div>}
        {ask("add")}
        {made && (
          // the next step takes the keyboard, and its words are read as the description of the button it lands on (a
          // live region inserted with its words already in it is not reliably said)
          <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-accent/40 bg-accent/[0.06] p-3 text-[12.5px]">
            <Check size={13} className="text-done" /> <span id="passkey-next-step">{addKey === "security-key"
              ? "Confirmed. Now make it on your security key: plug it in or hold it to this device when the browser asks."
              : "Confirmed. Now make the passkey on this device."}</span>
            <button className="btn btn-sm btn-primary" autoFocus aria-describedby="passkey-next-step" disabled={busy} onClick={() => void make()}>{busy ? <Loader2 size={12} className="animate-spin" /> : <KeyRound size={12} />} {addKey === "security-key" ? "Make it on the security key" : "Make the passkey"}</button>
            <button className="btn btn-sm" disabled={busy} onClick={() => setMade(null)}>Cancel</button>
          </div>
        )}
        {held && <div className="mt-2 text-[12.5px] text-done" role="status">{held}</div>}
        {notKey && (
          <div role="note" aria-label="Not a security key" className="mt-2 rounded-lg border border-wait/50 bg-wait/[0.05] p-3 text-[12.5px]">
            <span>{`The browser made a ${kindName({ kind: notKey.kind })}, not one on a security key: a phone reached by its code, or a password manager, can take a security key's request. `}</span>
            {notKey.id
              ? <>
                  <span>If you meant a security key, remove it and add the key again, choosing the security key when the browser asks.</span>{" "}
                  <button type="button" className="btn btn-sm" onClick={() => void begin({ what: "remove", id: notKey.id, label: `Removing \u201c${notKey.name ?? "the new passkey"}\u201d`, action: "Remove it", note: e2eKeys ? OFF_KEYS_WORDS : undefined })}>Remove it</button>
                </>
              : <span>It waits on its hold: if you meant a security key, cancel it in Encryption, then add the key again, choosing the security key when the browser asks.</span>}
          </div>
        )}
        {removedApps && <div className="mt-2 text-[12.5px] text-muted" role="status" aria-label="Connected apps">{removedApps}</div>}
        {error && <div role="alert" className="mt-2 text-[12.5px] text-fail">{error}</div>}
      </Section>}

      {!here && !local && <Section title="High security" note="When it is on, only a hardware key confirms a risky change: linking a session, revoking one, adding or removing a passkey, approving another device's sign-in, and signing every other device out. Only a hardware key signs your commands to a linked session, and a session pins your hardware keys alone. Your password confirms none of these, and only a hardware key turns it off.">
        <div className="flex flex-wrap items-center gap-3 text-[12.5px]">
          <span className={high ? "flex items-center gap-1 text-done" : "text-muted"}>{high && <ShieldCheck size={13} />}{high ? "On" : "Off"}</span>
          <button className="btn btn-sm" disabled={keys === null || (!high && (hardwareKeys.length === 0 || waiting))}
            onClick={() => (!high && e2eOn
              ? revoke({ what: { highSecurity: true }, label: "Turning high security on",
                words: "End-to-end encryption is on, so turning high security on replaces its keys: a new master key, sealed to your hardware keys and the recovery code, and a new content key every link seals to. Your other passkeys no longer unlock encryption. One of your hardware keys signs it." })
              : void begin({ what: "high", id: high ? "off" : "on", label: high ? "Turning high security off" : "Turning high security on", action: high ? "Turn it off" : "Turn it on", hardware: true,
                note: !high && e2eKeys ? OFF_HIGH_WORDS : undefined }))}>
            {high ? "Turn it off" : "Turn it on"}
          </button>
        </div>
        <div className="mt-2 text-[12px] text-muted">Keep two hardware keys, one of them kept apart: lose the only one while it is on, and none of these changes can be made.</div>
        {keys !== null && !high && hardwareKeys.length === 0 && <div className="mt-1 text-[12px] text-muted">It needs a hardware key first: add one under Passkeys.</div>}
        {keys !== null && !high && hardwareKeys.length > 0 && waiting && <div className="mt-1 text-[12px] text-muted">A passkey still waits to be judged: high security can be turned on once it is, or once that passkey is removed.</div>}
        {high && turnedOn && <div className="mt-1 text-[12px] text-done" role="status">{turnedOn}</div>}
        {!high && turnedOff && unpinned.length > 0 && (
          <div className="mt-1 text-[12px] text-muted" role="status">
            <div>A session linked while high security was on pinned your hardware keys alone, and still refuses what your other passkeys sign: give it the pin line of each one it should take, in that session&apos;s own window, with the session&apos;s link name for NAME.</div>
            {unpinned.map(({ k, pin }) => (
              <div key={k.id} className="mt-1 flex flex-wrap items-center gap-2">
                <span>{k.name}</span>
                <code className="break-all font-mono text-[11px] text-faint">{pin}</code>
                <CopyText label="Copy pin line" text={pin} />
              </div>
            ))}
          </div>
        )}
        {high && <div className="mt-1 text-[12px] text-muted">A session linked before it was turned on still holds your other passkeys: run pin again in that session&apos;s own window, so it keeps your hardware keys alone. A hardware key it does not hold yet goes in with that key&apos;s pin line, above: pin alone drops keys, and never adds one.</div>}
        {ask("high")}
        {revoking?.what.highSecurity && <RevokeFromAccount key={revoking.key} what={revoking.what} label={revoking.label} words={revoking.words} onDone={revoked} onCancel={() => setRevoking(null)} onAddHardware={onEncryption} />}
      </Section>}

      {!here && !local && <Section title="Signers" note="A signer is a program on a Mac of your own, ade_link.py sign, that shows a step there and has your security key sign it over USB; you read the words in your own terminal, from the bytes your key signs. Pair one with a ticket made here; revoke it here, or sign out everywhere."><SignersSection key={signedOut} /></Section>}

      {/* the Custody section, which takes no props and reads its own route */}
      <TaskCustody />

      {(here || local) && error && <div role="alert" className="mt-2 text-[12.5px] text-fail">{error}</div>}
      {!here && <Section title="Every device" note={`Ends every sign-in this account has, on every device and browser; this one is signed in again.${local ? " The app on this Mac signs in again the next time it starts." : high ? " It asks for one of your hardware keys first." : " It asks for one of your passkeys or your password first."}${connectorOn ? ` ${KEEP_WORKING}` : ""}`}>
        <div className="flex items-center gap-3">
          <button className="btn btn-sm" onClick={() => void begin({ what: "everywhere", label: "Signing out everywhere", action: "Sign out everywhere" })}><LogOut size={12} /> Sign out everywhere</button>
          {everywhere && <span className="flex items-center gap-1 text-[12.5px] text-done"><Check size={13} /> Every other device is signed out.</span>}
        </div>
        {ask("everywhere")}
        {everywhere && stillConnected && <StillConnected {...stillConnected} />}
      </Section>}
    </>
  );
}

/** What turning high security on withdrew: the commands the account's other passkeys signed that no session had taken,
 *  the sessions one of them had resumed, stopped again, and the approvals still waiting out their delay, cancelled
 *  (nothing records which way confirmed them). */
export function withdrawnWords(withdrawn: number, stopped: number, cancelled = 0): string {
  const parts = [
    withdrawn ? `${withdrawn} ${withdrawn === 1 ? "command" : "commands"} your other passkeys signed, not yet taken, ${withdrawn === 1 ? "was" : "were"} withdrawn` : "",
    stopped ? `${stopped} ${stopped === 1 ? "session" : "sessions"} another passkey had resumed ${stopped === 1 ? "was" : "were"} stopped again: resume ${stopped === 1 ? "it" : "them"} with a hardware key` : "",
    cancelled ? `${cancelled} ${cancelled === 1 ? "approval" : "approvals"} still waiting to take effect ${cancelled === 1 ? "was" : "were"} cancelled: approve again with a hardware key what you meant` : "",
  ].filter(Boolean);
  return parts.length ? `${parts.join("; ")}.` : "";
}

function NotificationsPane() {
  // the desktop app's own notifications are the system's, from this Mac's server; a hosted place's reach this Mac by a
  // browser's push or the phone app
  if (DESKTOP) {
    return onThisMac() ? (
      <Section title="This Mac" note="When a session on this Mac needs you and the window is not in front, the app shows the system's own notification: an agent waiting for a permission, a chat's question, a command waiting for approval, or a question on the session channel.">
        <div className="text-[12.5px] text-muted">To turn them off or change how they show, open System Settings, then Notifications, then Proofpane Community.</div>
      </Section>
    ) : (
      <Section title={hostLabel(server())} note="While this window shows it, what a hosted session needs shows here: the approvals and the linked sessions in the title bar, and each session's own state.">
        <div className="text-[12.5px] text-muted">For notifications while the app is closed, turn on push in a browser at {hostLabel(server())}, or use the phone app.</div>
      </Section>
    );
  }
  return <BrowserNotifications />;
}

function BrowserNotifications() {
  const [page, setPage] = useState<NotificationPermission | "unsupported">(typeof Notification === "undefined" ? "unsupported" : Notification.permission);
  const [push, setPush] = useState<PushState | null>(null);
  const [busy, setBusy] = useState("");
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { pushState().then(setPush).catch(() => setPush("unsupported")); }, []);
  const run = async (what: string, fn: () => Promise<void>) => {
    setBusy(what); setError(""); setSaid("");
    try { await fn(); setPush(await pushState()); } catch (e) { setError(message(e)); } finally { setBusy(""); }
  };
  return (
    <>
      <Section title="On this page" note="While the app is open here, a session that needs you shows a notification, and the tab's title counts them.">
        {page === "granted" ? <div className="flex items-center gap-1.5 text-[12.5px] text-done"><Check size={13} /> On for this browser.</div>
          : page === "denied" ? <div className="text-[12.5px] text-muted">This browser blocks them for this site: allow them in its site settings.</div>
          : page === "unsupported" ? <div className="text-[12.5px] text-muted">This browser has no notifications.</div>
          : <button className="btn btn-sm btn-primary" onClick={async () => setPage(await Notification.requestPermission())}><Bell size={12} /> Allow notifications</button>}
      </Section>
      <Section title="On this device, with no page open" note="Push: a chat's question, an agent waiting for a permission, and a command waiting for approval reach this device, and open the app at it when tapped.">
        {push === null ? <div className="text-[12.5px] text-muted"><Loader2 size={13} className="mr-1 inline animate-spin" /> Checking…</div>
          : push === "home-screen" ? <div className="text-[12.5px] text-muted">On an iPhone or iPad, add this page to the Home Screen first (Share, then Add to Home Screen) and open it from there.</div>
          : push === "unsupported" ? <div className="text-[12.5px] text-muted">This browser has no push notifications.</div>
          : (
            <div className="flex flex-wrap items-center gap-2">
              {push === "on" ? <span className="flex items-center gap-1.5 text-[12.5px] text-done"><Check size={13} /> On for this device.</span>
                : <button className="btn btn-sm btn-primary" disabled={!!busy} onClick={() => run("on", pushOn)}>{busy === "on" ? <Loader2 size={12} className="animate-spin" /> : <Bell size={12} />} Turn on</button>}
              {push === "on" && (
                <>
                  <button className="btn btn-sm" disabled={!!busy} onClick={() => run("test", async () => { const r = await pushTest(); setSaid(`Sent to ${r.sent} of ${r.devices} ${r.devices === 1 ? "device" : "devices"}.`); })}>
                    {busy === "test" ? <Loader2 size={12} className="animate-spin" /> : null} Send a test
                  </button>
                  <button className="btn btn-sm" disabled={!!busy} onClick={() => run("off", pushOff)}>Turn off</button>
                </>
              )}
            </div>
          )}
        {said && <div className="mt-2 text-[12.5px] text-fg-dim">{said}</div>}
        {error && <div role="alert" className="mt-2 text-[12.5px] text-fail">{error}</div>}
      </Section>
    </>
  );
}

function PeoplePane() {
  const [list, setList] = useState<Invitation[] | null>(null);
  const [email, setEmail] = useState("");
  const [made, setMade] = useState<{ email: string; link: string; expires_in_hours: number } | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try { setList((await api<{ invitations: Invitation[] }>("/api/admin/invitations")).invitations); }
    catch (e) { setError(message(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const invite = async () => {
    setBusy(true); setError(""); setCopied(false);
    try {
      setMade(await api("/api/admin/invitations", { method: "POST", body: { email: email.trim() } }));
      setEmail("");
      await load();
    } catch (e) { setError(message(e)); }
    finally { setBusy(false); }
  };
  const copy = async () => {
    if (!made) return;
    try { await navigator.clipboard.writeText(made.link); setCopied(true); }
    catch { setError("This browser did not allow copying: select the link and copy it."); }
  };
  const now = Date.now() / 1000;

  return (
    <>
      <Section title="Invite someone" note="Makes a link that lets one person create an account with this email address. Send it to them yourself: it is shown once, here.">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void invite(); }}>
          <input aria-label="The address to invite" className="input flex-1" type="email" required placeholder="name@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
          <button className="btn btn-primary" disabled={busy || !email.trim()}>{busy ? <Loader2 size={13} className="animate-spin" /> : <UserPlus size={13} />} Make a link</button>
        </form>
        {made && (
          <div className="mt-3 rounded-lg border border-accent/40 bg-accent/[0.06] p-3">
            <div className="mb-2 text-[12.5px]">The link for <b className="font-medium">{made.email}</b>, valid for {made.expires_in_hours} hours:</div>
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 select-all truncate rounded-md bg-bg px-2.5 py-1.5 font-mono text-[11.5px] text-fg-dim">{made.link}</code>
              <button className="btn btn-sm" onClick={copy}>{copied ? <Check size={12} /> : <Copy size={12} />} {copied ? "Copied" : "Copy"}</button>
            </div>
          </div>
        )}
        {error && <div role="alert" className="mt-2 text-[12.5px] text-fail">{error}</div>}
      </Section>
      <Section title="Invitations">
        {list === null ? <div className="text-[12.5px] text-muted"><Loader2 size={13} className="mr-1 inline animate-spin" /> Loading…</div> : list.length === 0 ? (
          <div className="text-[12.5px] text-muted">No one has been invited yet.</div>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-line">
            <table className="w-full text-left text-[12.5px]">
              <thead className="border-b border-line text-[11px] uppercase tracking-wide text-faint">
                <tr><th className="px-3 py-2 font-medium">Email</th><th className="px-3 py-2 font-medium">Invited by</th><th className="px-3 py-2 font-medium">Made</th><th className="px-3 py-2 font-medium">State</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {list.map((i) => (
                  <tr key={`${i.email}-${i.created_at}`}>
                    <td className="px-3 py-2">{i.email}</td>
                    <td className="px-3 py-2 text-muted">{i.created_by}</td>
                    <td className="px-3 py-2 text-muted">{when(i.created_at)}</td>
                    <td className="px-3 py-2">
                      {i.used_at ? <span className="text-done">Joined {when(i.used_at)}</span> : i.expires_at < now ? <span className="text-faint">Expired</span> : <span className="text-wait">Waiting, until {when(i.expires_at)}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </>
  );
}

function VerifyPane() {
  const [result, setResult] = useState<Checked | null>(null);
  const [names, setNames] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [key, setKey] = useState<PublicKey | null>(null);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => { api<PublicKey>("/api/public-key").then(setKey).catch(() => {}); }, []);

  const check = async (files: File[]) => {
    if (!files.length) return;
    setBusy(true); setError(""); setResult(null); setNames(files.map((f) => f.name));
    try { setResult(await checkFiles(await Promise.all(files.map((f) => f.text())))); }
    catch (e) { setError(message(e)); }
    finally { setBusy(false); if (input.current) input.current.value = ""; }
  };

  return (
    <>
      <Section title="Check a signed record" note="A session's Finish signs its change with this deployment's key: its Activity tab downloads the record. Check one here, with the same checker as the offline verifier.">
        <div
          className={`flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-4 py-7 text-center ${over ? "border-accent bg-accent/[0.05]" : "border-line-strong"}`}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); void check(Array.from(e.dataTransfer.files)); }}
        >
          <Upload size={18} className="text-muted" />
          <div className="text-[12.5px] text-fg-dim">Drop a record here: a Finish record, a manifest and its signature, or an audit chain</div>
          <button className="btn btn-sm" onClick={() => input.current?.click()} disabled={busy}>
            {busy ? <Loader2 size={12} className="animate-spin" /> : null} Choose a file
          </button>
          <input ref={input} type="file" multiple accept=".json,.jsonl,application/json,text/plain" className="hidden" onChange={(e) => void check(Array.from(e.target.files || []))} />
        </div>
        {error && <div role="alert" className="mt-3 flex items-start gap-2 text-[12.5px] text-fail"><CircleAlert size={14} className="mt-0.5 flex-none" /> {error}</div>}
        {result && <Result r={result} names={names} />}
      </Section>
      {key && (
        <Section title="This deployment's public key" note="Served to anyone. The code that checks a signature against it offline is the signing package (oss/sign), published with Proofpane Community's source; here, the section above checks a record's signature.">
          <div className="space-y-1.5 text-[12px]">
            <div className="flex gap-2"><span className="w-16 flex-none text-faint">Key</span><code className="min-w-0 select-all break-all font-mono text-fg-dim">{key.key}</code></div>
            <div className="flex gap-2"><span className="w-16 flex-none text-faint">Algorithm</span><code className="font-mono text-fg-dim">{key.alg}</code></div>
          </div>
        </Section>
      )}
    </>
  );
}

export function Result({ r, names = [] }: { r: Checked; names?: string[] }) {
  const line = (ok: boolean, text: ReactNode) => (
    <div className={`flex items-start gap-2 ${ok ? "text-done" : "text-fail"}`}>
      {ok ? <ShieldCheck size={14} className="mt-0.5 flex-none" /> : <CircleAlert size={14} className="mt-0.5 flex-none" />}
      <span>{text}</span>
    </div>
  );
  return (
    <div className="mt-3 space-y-2 rounded-lg border border-line bg-bg px-3.5 py-3 text-[12.5px]">
      {names.length > 0 && <div className="truncate text-[11px] text-faint">{names.join(" + ")}</div>}
      {(r.what === "record" || r.what === "pair") && line(r.signature.ok,
        r.signature.ok ? <>Signed by this deployment&apos;s key <code className="font-mono text-[11px]">{r.signature.key.slice(0, 23)}…</code></> : "The signature does not match: the record was changed after it was signed, or another key signed it.")}
      {r.what !== "chain" && line(r.manifest.ok, r.manifest.ok ? "The manifest is intact: its decision follows from its obligations." : "The manifest does not check out.")}
      {r.what !== "chain" && r.manifest.ok && r.manifest.decision && (
        <div className="pl-[22px]">
          <Verdict verify={r.manifest.decision.verify} claims={r.manifest.decision.claims} rule={r.manifest.decision.rule_id} />
        </div>
      )}
      {r.what === "manifest" && <div className="text-muted">It has no signature with it: add its manifest.sig.json to check who signed it.</div>}
      {r.what === "chain" && line(r.chain.ok, r.chain.ok ? `The chain is whole: ${r.chain.lines} lines, head ${r.chain.head.slice(0, 23)}…` : "The chain does not check out.")}
      {(r.what === "chain" ? r.chain.errors : r.manifest.errors).slice(0, 8).map((e, i) => <div key={i} className="font-mono text-[11.5px] text-fail">{e}</div>)}
    </div>
  );
}
