// A file or folder of a session's worktree dragged within the page (from its files or its changes) onto that session's
// agent: its path is typed in its terminal, or put in its chat's message, as a person would type it. Only onto the
// same session's: a path means nothing in another worktree.
import type { DragEvent as ReactDragEvent } from "react";

export const PATH_TYPE = "application/x-proofpane-path";
type AnyDrag = DragEvent | ReactDragEvent;

export function dragPath(e: ReactDragEvent, session: string, path: string) {
  e.dataTransfer.setData(PATH_TYPE, JSON.stringify({ session, path }));
  e.dataTransfer.setData("text/plain", path);
  e.dataTransfer.effectAllowed = "copy";
}

/** Whether a path of the page's is being dragged (while it is, only its type can be read, not what it holds). */
export const hasPath = (e: AnyDrag) => Array.from(e.dataTransfer?.types || []).includes(PATH_TYPE);

/** A control character (C0, DEL or C1): a file's name may hold one, and typed into a terminal it would act (a line
 *  ending runs what is typed so far, ESC starts a sequence that can end a bracketed paste early). */
export const CONTROL = /[\x00-\x1f\x7f-\x9f]/;

export const CONTROL_REFUSED = "That path has a control character in its name, so it is not typed: rename the file, or type its path yourself.";

/** The path dropped, when it is of `session`'s worktree: `{ path }`, or `{ refused }` with why it is not given when
 *  its name holds a control character; null for anything else. */
export function droppedPath(e: AnyDrag, session: string): { path: string } | { refused: string } | null {
  try {
    const got = JSON.parse(e.dataTransfer?.getData(PATH_TYPE) || "null");
    if (!got || got.session !== session || typeof got.path !== "string" || !got.path) return null;
    return CONTROL.test(got.path) ? { refused: CONTROL_REFUSED } : { path: got.path };
  } catch {
    return null;
  }
}
