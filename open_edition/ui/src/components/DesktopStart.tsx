import { useEffect, useState, type ReactNode } from "react";
import { Loader2, TriangleAlert, X } from "lucide-react";
import { onThisMac } from "../api";
import { loadHosted, startDesktop } from "../desktop";
import { activeId, LOCAL } from "../places";
import { Logo } from "./TitleBar";

declare global {
  interface Window { ppFailed?: (why: string) => void }
}

/** The desktop app's window until this Mac's server answers: "starting", then the app, or why the server did not
 *  start. A hosted place opens without waiting for it, with a line saying so if this Mac's workspace is not there; if
 *  this Mac's server stops later (the shell calls `ppFailed`), the same. */
export function DesktopStart({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [hosted, setHosted] = useState(false);      // a hosted place's sign-in, read from the keychain
  const [failed, setFailed] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    window.ppFailed = (why: string) => { setFailed(why); setDismissed(false); };
    void loadHosted().finally(() => setHosted(true));
    startDesktop()
      .then(() => setReady(true))
      .catch((e) => setFailed(e instanceof Error ? e.message : String(e)));
    return () => { window.ppFailed = undefined; };
  }, []);

  const elsewhere = activeId() !== LOCAL;
  if (hosted && (ready || elsewhere) && !(failed && onThisMac())) {
    return (
      <>
        {failed && !dismissed && (
          <div className="fixed inset-x-0 top-0 z-50 flex items-center justify-center gap-2 border-b border-fail/40 bg-fail/10 px-4 py-1.5 text-[12px] text-fail">
            <TriangleAlert size={13} /> This Mac's workspace is not running: {failed}
            <button className="icon-btn !h-5 !w-5" title="Hide" onClick={() => setDismissed(true)}><X size={12} /></button>
          </div>
        )}
        {children}
      </>
    );
  }
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
      <Logo size={36} />
      <div className="text-[17px] font-semibold tracking-tight">Proofpane Community</div>
      {failed ? (
        <>
          <div className="text-[13px] text-fg-dim">This Mac's workspace did not start.</div>
          <div className="max-w-[560px] whitespace-pre-wrap text-[12.5px] text-fail" role="alert">{failed}</div>
        </>
      ) : (
        <div className="flex items-center gap-2 text-[13px] text-muted"><Loader2 size={14} className="animate-spin" /> Starting the workspace on this Mac</div>
      )}
    </div>
  );
}
