import { useEffect, useState } from "react";
import { Loader2, RotateCw } from "lucide-react";
import { Api, ApiError, DESKTOP, signInOfThisWindowAlone, token, whenSignedOut } from "../api";
import { activeId, place, setPlaceToken } from "../places";
import type { PopOut } from "../popOut";
import { isSignIn, useStore } from "../store";
import { ControlView } from "./ControlView";
import { ErrorBoundary } from "./ErrorBoundary";

/** The fixed words a pop-out's window is titled with: what it draws, never an agent's name or a session's title. */
export function popOutTitle(p: PopOut): string {
  const what = p.view === "map" ? "The map" : p.view === "session" ? "A session's map" : "An agent";
  return `${what} · Proofpane Community`;
}

/** How a pop-out in the desktop app is loaded again (a test stands in for it). */
export const again = { load: () => location.reload() };

/** Who the page that opened a pop-out is signed in as, in the storage every window of the page shares: in a browser the
 *  sign-in itself; in the desktop app, whose windows each hold their own copy of it, the place the window shows, its
 *  server and the account it names (a sign-out there forgets the account). */
export function signedInAs(): string {
  if (!DESKTOP) return token() ?? "";
  const id = activeId();
  const p = place(id);
  return JSON.stringify([id, p?.server ?? null, p?.email ?? null]);
}

/** A pop-out: one panel of the map in a tab (a browser's) or window (the desktop app's) of its own,
 *  the map, one session's map or one agent, signed in with the sign-in the page that opened it keeps (the browser's
 *  storage, or the desktop app's), and keeping itself current. Nothing else of the app runs in it but, in the desktop
 *  app, the app's start (`DesktopStart`, main.tsx), which reads the sign-in it draws with: no list, no notifications,
 *  no title that counts. Its sign-in ends with that page's: once another window signs out, signs in as another
 *  account or (in the desktop app) shows another place, it stops drawing, and says why, rather than go on drawing what
 *  that sign-in showed. */
export function PopOutPage({ pop }: { pop: PopOut }) {
  // in the desktop app this window's sign-in is its own copy: a 401 it is answered lets go of that copy alone
  useState(() => { signInOfThisWindowAlone(); return null; });
  const [state, setState] = useState<"checking" | "in" | "out" | "refused" | "changed" | "unreachable">("checking");
  const [tries, setTries] = useState(0);
  useEffect(() => { document.title = popOutTitle(pop); }, [pop]);
  useEffect(() => {
    if (!token()) { setState("out"); return; }
    let gone = false;
    const as = signedInAs();
    const shownFor = activeId();
    setState("checking");
    Api.me().then((user) => {
      if (gone) return;
      // another account than the one this window showed: nothing of the one before is kept, as the page keeps nothing
      // of one account for the next (store.ts forget)
      const was = useStore.getState().user;
      useStore.setState({ user, booted: true, ...(was && was.id !== user.id ? { accounts: null, records: [], signIns: [] } : {}) });
      setState(signedInAs() === as ? "in" : "changed");
    }).catch((e) => { if (!gone) setState(e instanceof ApiError && e.status === 401 ? "out" : "unreachable"); });
    // a request of this window's that the server answers 401 (the api's own sign-out of this page): in the desktop app
    // it refused this window's own copy alone, which says nothing of the page that opened it, which may hold a newer
    // sign-in since "Sign out everywhere"
    whenSignedOut(() => setState(DESKTOP ? "refused" : "out"));
    // another window of the page changed who it is signed in as: this window's sign-in, its own copy in the desktop
    // app, ends with it
    const changed = () => {
      if (signedInAs() === as) return;
      if (DESKTOP) setPlaceToken(shownFor, null, false);
      setState("changed");
    };
    window.addEventListener("storage", changed);
    return () => { gone = true; window.removeEventListener("storage", changed); };
  }, [tries]);
  // the accounts, to name each as the list names it, and the sessions' records, to name each session's agent as the list
  // does and to leave its sign-ins out: once signed in, and again each minute while it shows
  useEffect(() => {
    if (state !== "in") return;
    const read = () => {
      void useStore.getState().refreshAccounts(false);
      Api.sessions().then(({ sessions }) => useStore.setState({ records: sessions.filter((r) => !isSignIn(r)), signIns: sessions.filter(isSignIn) }))
        .catch(() => { /* the map names them from its own fields meanwhile */ });
    };
    read();
    const t = setInterval(() => { if (document.visibilityState === "visible") read(); }, 60000);
    return () => clearInterval(t);
  }, [state]);

  if (state === "checking") {
    return <div className="flex h-full items-center justify-center"><Loader2 size={18} className="animate-spin text-muted" aria-label="Signing in" /></div>;
  }
  if (state !== "in") {
    const said = state === "out" ? "This window is signed in as the page that opened it, and that page is not signed in now. Sign in there, then try again."
      : state === "refused" ? "This window's sign-in is no longer taken, while the page that opened it may hold a newer one. Try again, and it reads that page's sign-in afresh."
      : state === "changed" ? "The page that opened this window has signed out, or shows another account or place now, so this window shows nothing more. Open it again from there."
      : "Cannot reach the server. Check your connection, then try again.";
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
        <p role="status" className="max-w-[420px] text-[12.5px] text-muted">{said}</p>
        {/* the desktop app's window reads its sign-in as it loads (DesktopStart): it is loaded again */}
        <button className="btn btn-sm" onClick={() => (DESKTOP ? again.load() : setTries((n) => n + 1))}><RotateCw size={13} /> Try again</button>
      </div>
    );
  }
  return (
    <main className="h-full overflow-y-auto">
      <ErrorBoundary what="The map">
        <ControlView pop={pop} />
      </ErrorBoundary>
    </main>
  );
}
