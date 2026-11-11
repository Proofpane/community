import { useRef } from "react";
import { Loader2, Power } from "lucide-react";
import { Api, type AccountOwnerView, type WorkspaceState } from "../api";
import { useVisibleEvery } from "../sessionMap";
import { useStore, workspaceRuns } from "../store";
import { workspaceWords } from "../workspaceWords";
import { ClaudeAccounts, useConnectionsReads } from "./Connections";
import { Modal } from "./Modal";

/** How often the place reads the owner's confirmed accounts while it is open. */
export const PLACE_EVERY = 3000;

/** What the picker says when the place closes on more than one account confirmed at once. */
export const MANY_CONFIRMED =
  "More than one account was confirmed while that was open: choose the one the Manager runs on.";

/** The place the Manager's account picker opens, to sign
 *  an account in and come back with it chosen. An account signs in inside the workspace, so a stopped one is started
 *  first; then Connections' own Claude Code accounts, read as Connections reads them, where an account is signed in and
 *  confirmed as the owner's own (Yes, it is mine), a confirmation waiting before it counts.
 *
 *  The confirmed accounts are read every PLACE_EVERY milliseconds while the page is visible, each read handed to the
 *  picker (`onRead`), so its list is as fresh as the place's. What was there before is what the picker showed when the
 *  place opened (`shown`), or, where the picker had read none, the place's own first read. An account confirmed since
 *  is chosen for the Manager (`onChosen`) when it is the only one, and the place closes; two or more at once are left
 *  to the picker's list, the place closing with MANY_CONFIRMED. A refused save closes it too: the panel says why, and
 *  the list offers the account. */
export function ManagerAccountSetup({ shown, onRead, onChosen, onClose }: {
  shown: string[] | null; onRead: (accounts: AccountOwnerView[]) => void;
  onChosen: (account: string) => Promise<unknown>; onClose: (note?: string) => void;
}) {
  useConnectionsReads();
  const ws = useStore((s) => s.ws);
  const before = useRef<Set<string> | null>(shown ? new Set(shown) : null);
  const chosen = useRef(false);
  useVisibleEvery(true, PLACE_EVERY, async (signal) => {
    try {
      const got = await Api.taskAccounts();
      if (signal.aborted || chosen.current) return true;
      onRead(got.accounts);
      const confirmed = got.accounts.filter((a) => a.state === "confirmed").map((a) => a.account);
      if (before.current === null) {
        before.current = new Set(confirmed);
        return true;
      }
      const fresh = confirmed.filter((a) => !before.current!.has(a));
      if (fresh.length === 1) {
        chosen.current = true;
        await onChosen(fresh[0]);
        onClose();
      } else if (fresh.length > 1) {
        chosen.current = true;
        onClose(MANY_CONFIRMED);
      }
      return true;
    } catch (e) {
      return e instanceof DOMException && e.name === "AbortError";
    }
  });
  return (
    <Modal title="An account for the Manager" onClose={() => onClose()} width={600}>
      <div className="space-y-3">
        <p className="text-[12px] text-muted">
          The Manager runs on an account you have confirmed as your own. Sign one in here and press Yes, it is mine. A
          confirmed account waits before it counts as yours, as its row says; the Manager is set to it then, and this
          closes.
        </p>
        <Workspace ws={ws} />
      </div>
    </Modal>
  );
}

/** The place's workspace part: Connections' Claude Code accounts while the workspace runs (as the shell takes it,
 *  workspaceRuns); otherwise the main panel's own words for it (workspaceWords), why a start failed, and a start where
 *  the deployment gives the account a workspace. */
function Workspace({ ws }: { ws: WorkspaceState | null | undefined }) {
  if (workspaceRuns(ws)) return <ClaudeAccounts />;
  if (!ws) {
    return (
      <div className="flex justify-center py-3 text-muted">
        <Loader2 size={14} className="animate-spin" aria-label="Reading your workspace" />
      </div>
    );
  }
  if (!ws.enabled || ws.allowed === false) {
    return (
      <p className="text-[12px] text-muted">
        {ws.detail || "This deployment does not offer a workspace to this account."} An account signs in inside a
        workspace, so none can be signed in here.
      </p>
    );
  }
  const said = workspaceWords(ws);
  const starting = !!ws.starting;
  const error = ws.start_error || ws.error;
  return (
    <div className="space-y-2">
      <p className="text-[12.5px] font-medium">{said.title}</p>
      <p className="text-[12px] text-muted">{said.body} An account signs in inside it.</p>
      {error && !starting && <p role="alert" className="text-[12px] text-fail">{error}</p>}
      <button className="btn btn-sm btn-primary" disabled={starting}
        onClick={() => void useStore.getState().startWorkspace()}>
        {starting ? <Loader2 size={12} className="animate-spin" /> : <Power size={12} />}{" "}
        {starting ? "Starting" : "Start the workspace"}
      </button>
    </div>
  );
}
