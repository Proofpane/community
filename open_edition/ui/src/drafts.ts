// Edits not yet saved, by session and file (the Files tab's editor): kept when the person moves to another file, tab
// or session, and opened again, unsaved, when they come back; forgotten with their session, and asked about before
// the page is reloaded or closed.

export const drafts = new Map<string, { text: string; base: string }>();

export const draftKey = (session: string, path: string) => `${session}\n${path}`;

/** Forget the drafts of sessions that are gone. */
export function keepDrafts(sessions: Iterable<string>) {
  const keep = new Set(sessions);
  for (const key of [...drafts.keys()]) if (!keep.has(key.split("\n")[0])) drafts.delete(key);
}

if (typeof window !== "undefined") {
  window.addEventListener("beforeunload", (e) => { if (drafts.size) { e.preventDefault(); e.returnValue = ""; } });
}
