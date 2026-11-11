import { useEffect, useRef, useState } from "react";
import { ArrowLeft, KeyRound, Loader2, Smartphone } from "lucide-react";
import { APP, Api, api, DESKTOP, NO_PASSWORD, server, serverAddress, setServer, type User } from "../api";
import { passkeysHere, signInWithPasskey } from "../passkey";
import { LOCAL } from "../places";
import { useStore } from "../store";
import { Licenses } from "./Licenses";
import { DEFAULT_SERVER, Logo, switchPlace } from "./TitleBar";

/** Signing in (a password or a passkey), or creating the account an invitation is for. In an app (the phone app, or a
 *  hosted place in the desktop app), also which server: a passkey there is the server's site's, which an app cannot
 *  use without that site naming it. */
export function SignIn({ invite, onInviteDone }: { invite?: string | null; onInviteDone?: () => void }) {
  const signIn = useStore((s) => s.signIn);
  // an app names its server; the browser uses the page's own
  const [address, setAddress] = useState(() => server() || DEFAULT_SERVER);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState<"" | "password" | "passkey" | "invite">("");
  const [error, setError] = useState("");
  const [invited, setInvited] = useState<string | null>(null);
  const [byDevice, setByDevice] = useState(false);
  // Proofpane Tray's sign-in is not offered: approving a device takes a confirmation made for its
  // request, a passkey's or the password's, which the tray, holding only a sign-in, cannot give, so every hosted account
  // refuses it, until the tray asks for the account's password
  const after = useRef(onInviteDone);
  after.current = onInviteDone;

  useEffect(() => {
    if (!invite) return;
    api<{ email: string }>("/api/auth/invitation", { method: "POST", body: { token: invite } })   // in the body, never a URL
      .then((r) => setInvited(r.email))
      .catch((e) => { setError(e instanceof Error ? e.message : String(e)); after.current?.(); });
  }, [invite]);

  const noPassword = (() => { try { return localStorage.getItem(NO_PASSWORD) === "1"; } catch { return false; } })();
  const done = async (r: { token: string; user: User }) => {
    try { localStorage.removeItem(NO_PASSWORD); } catch { /* this page only */ }
    // another account signed in on this page before (an invitation opened while signed in) leaves nothing of its own
    useStore.getState().signedIn(r.token, r.user);
    await useStore.getState().refresh();
  };

  const run = async (what: typeof busy, fn: () => Promise<void>) => {
    setBusy(what);
    setError("");
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  // the app's server, checked and kept before anything is asked of it
  const pointAt = () => {
    if (!APP) return true;
    const origin = serverAddress(address);
    if (!origin) {
      setError("The server is an https address, such as https://proofpane-oss.fly.dev.");
      return false;
    }
    setServer(origin);
    return true;
  };

  if (byDevice) {
    return (
      <Frame subtitle="Signed in somewhere already? Let that device sign this one in.">
        <DeviceSignIn onDone={done} onBack={() => setByDevice(false)} />
        {DESKTOP && <BackToThisMac />}
      </Frame>
    );
  }

  if (invite && invited) {
    return (
      <Frame subtitle="You were invited. Choose a password to create your account.">
        <form onSubmit={(e) => { e.preventDefault(); void run("invite", async () => { await done(await api("/api/auth/register", { method: "POST", body: { token: invite, password } })); after.current?.(); }); }} className="panel space-y-3 p-5">
          <div>
            <div className="label mb-1.5">Account</div>
            <div className="text-[13px] font-medium">{invited}</div>
          </div>
          <div>
            <label className="label mb-1.5 block" htmlFor="new-password">Password</label>
            <input id="new-password" className="input" type="password" autoComplete="new-password" minLength={10} autoFocus value={password} onChange={(e) => setPassword(e.target.value)} required />
            <div className="mt-1 text-[11.5px] text-faint">At least 10 characters. You can add a passkey afterwards.</div>
          </div>
          {error && <div role="alert" className="text-[12.5px] text-fail">{error}</div>}
          <button className="btn btn-primary h-9 w-full justify-center" disabled={!!busy}>{busy ? "Creating…" : "Create account"}</button>
        </form>
      </Frame>
    );
  }

  return (
    <Frame subtitle={DESKTOP ? "Sign in to a hosted Proofpane Community workspace." : "Run coding agents in parallel, under governance you can prove."}>
      <form onSubmit={(e) => {
        e.preventDefault();
        void run("password", async () => {
          if (APP) {
            const origin = serverAddress(address);
            if (!origin) throw new Error("The server is an https address, such as https://proofpane-oss.fly.dev.");
            setServer(origin);
          }
          await signIn(email.trim(), password);
        });
      }} className="panel space-y-3 p-5">
        {noPassword && (
          <div role="status" className="rounded-md border border-line bg-panel px-3 py-2 text-[12px] text-fg-dim">
            The account you signed out of is this Mac&apos;s own, which has no password: sign in again with
            {" "}<b className="font-medium text-fg">Sign in with another device</b>, and approve it in the Proofpane Community app.
          </div>
        )}
        {APP && (
          <div>
            <label className="label mb-1.5 block" htmlFor="server">Server</label>
            <input id="server" className="input" type="url" inputMode="url" autoCapitalize="off" autoCorrect="off" value={address} onChange={(e) => setAddress(e.target.value)} required />
            <div className="mt-1 text-[11.5px] text-faint">The Proofpane Community your workspace is on.</div>
          </div>
        )}
        <div>
          <label className="label mb-1.5 block" htmlFor="email">Email</label>
          <input id="email" className="input" type="email" autoComplete="username webauthn" autoFocus value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div>
          <label className="label mb-1.5 block" htmlFor="password">Password</label>
          <input id="password" className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>
        {error && <div role="alert" className="text-[12.5px] text-fail">{error}</div>}
        <button className="btn btn-primary h-9 w-full justify-center" disabled={!!busy}>{busy === "password" ? "Signing in…" : "Sign in"}</button>
        <div className="flex items-center gap-3 py-0.5 text-[11px] text-faint"><span className="h-px flex-1 bg-line" />or<span className="h-px flex-1 bg-line" /></div>
        {passkeysHere && !APP && (
          <button type="button" className="btn h-9 w-full justify-center" disabled={!!busy} onClick={() => run("passkey", async () => done(await signInWithPasskey()))}>
            {busy === "passkey" ? <Loader2 size={14} className="animate-spin" /> : <KeyRound size={14} />} Sign in with a passkey
          </button>
        )}
        <button type="button" className="btn h-9 w-full justify-center" disabled={!!busy}
          onClick={() => { setError(""); if (pointAt()) setByDevice(true); }}>
          <Smartphone size={14} /> Sign in with another device
        </button>
      </form>
      {DESKTOP && <BackToThisMac />}
    </Frame>
  );
}

/** What this device calls itself when it asks to be signed in: the app or a browser, and on what. */
export function deviceName(ua: string = navigator.userAgent, app: boolean = APP): string {
  const what = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) ? "iPad" : /Android/.test(ua) ? "Android"
    : /Macintosh|Mac OS X/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "a device";
  return app ? `Proofpane Community on ${what}` : `A browser on ${what}`;
}

/** In the desktop app, a hosted place's sign-in screen leads back to the workspace on this Mac. */
function BackToThisMac() {
  return (
    <button className="btn mt-3 h-9 w-full justify-center" onClick={() => switchPlace(LOCAL)}>
      <ArrowLeft size={14} /> Back to this Mac
    </button>
  );
}

/** A device without a sign-in asks the server for one: it shows a code, which the person approves on a device signed
 *  in already, and it asks every few seconds whether that happened. */
export function DeviceSignIn({ onDone, onBack }: { onDone: (r: { token: string; user: User }) => Promise<void>; onBack: () => void }) {
  const [code, setCode] = useState("");
  const [state, setState] = useState<"starting" | "waiting" | "refused" | "withdrawn" | "expired" | "failed">("starting");
  const [error, setError] = useState("");
  const [round, setRound] = useState(0);
  const finish = useRef(onDone);
  finish.current = onDone;

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setState("starting");
    setCode("");
    (async () => {
      try {
        const r = await Api.deviceStart(deviceName());
        if (stop) return;
        setCode(r.code);
        setState("waiting");
        const ask = async () => {
          if (stop) return;
          try {
            const got = await Api.devicePoll(r.id, r.poll);
            if (stop) return;
            if (got.status === "approved" && got.token && got.user) {
              await finish.current({ token: got.token, user: got.user });
              return;
            }
            // an approval withdrawn before this device collected it is no refusal on the other device
            if (got.status === "refused") { setState(got.withdrawn ? "withdrawn" : "refused"); return; }
            if (got.status === "expired" || got.status === "used") { setState("expired"); return; }
          } catch { /* a moment without the network: ask again */ }
          timer = setTimeout(() => void ask(), r.interval * 1000 + 200);
        };
        timer = setTimeout(() => void ask(), r.interval * 1000 + 200);
      } catch (e) {
        if (!stop) { setError(e instanceof Error ? e.message : String(e)); setState("failed"); }
      }
    })();
    return () => { stop = true; if (timer) clearTimeout(timer); };
  }, [round]);

  return (
    <div className="panel space-y-3 p-5">
      {state === "starting" && <div className="flex justify-center py-6 text-muted"><Loader2 size={18} className="animate-spin" /></div>}
      {state === "waiting" && (
        <>
          <div className="text-center font-mono text-[28px] font-semibold tracking-[0.2em]" aria-label="Your code">{code}</div>
          <div className="text-[12.5px] text-fg-dim">
            On a device where you are signed in to this Proofpane Community, open the account menu, then <span className="font-medium">Sign in a device</span>, and enter this code. It works for ten minutes.
          </div>
          <div className="flex items-center gap-2 text-[12px] text-muted"><Loader2 size={12} className="animate-spin" /> Waiting for it to be approved</div>
        </>
      )}
      {state === "refused" && <div role="alert" className="text-[12.5px] text-fail">It was refused on the other device.</div>}
      {state === "withdrawn" && (
        <div role="alert" className="text-[12.5px] text-fail">
          Its approval was withdrawn before this device signed in: the account signed out everywhere, removed the passkey
          that approved it, or turned high security on.
        </div>
      )}
      {state === "expired" && <div role="alert" className="text-[12.5px] text-muted">The code expired before it was approved.</div>}
      {state === "failed" && <div role="alert" className="text-[12.5px] text-fail">{error}</div>}
      <div className="flex gap-2">
        {(state === "refused" || state === "withdrawn" || state === "expired" || state === "failed") && (
          <button className="btn btn-primary h-9 flex-1 justify-center" onClick={() => setRound((n) => n + 1)}>A new code</button>
        )}
        <button className="btn h-9 flex-1 justify-center" onClick={onBack}>Back</button>
      </div>
    </div>
  );
}

function Frame({ subtitle, children }: { subtitle: string; children: React.ReactNode }) {
  // the licenses of what the app carries, before anyone signs in too: the phone app opens here
  const [licenses, setLicenses] = useState(false);
  return (
    <div className="flex h-full items-center justify-center overflow-y-auto px-4 py-8">
      <div className="w-full max-w-[360px]">
        <div className="mb-7 flex flex-col items-center gap-3">
          <Logo size={36} />
          <div className="text-center">
            <div className="text-[18px] font-semibold tracking-tight">Proofpane Community</div>
            <div className="mt-1 text-[12.5px] text-muted">{subtitle}</div>
          </div>
        </div>
        {children}
        <div className="mt-6 text-center">
          <button className="touch-target text-[11.5px] text-muted underline-offset-2 hover:text-fg hover:underline" onClick={() => setLicenses(true)}>Licenses</button>
        </div>
      </div>
      {licenses && <Licenses onClose={() => setLicenses(false)} />}
    </div>
  );
}
