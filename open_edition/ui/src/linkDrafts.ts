// What the owner has typed to a linked session and not signed yet, by link and by what it is for (an instruction, or
// the answer to one question), as drafts.ts keeps the Files tab's edits for the workspace's sessions: kept when Linked
// sessions closes or its composer goes, given back when it opens again, and asked about before the page is reloaded or
// closed. A draft goes once what it says is signed, or when the owner discards it; and all of them go at sign-out, so
// the next account to sign in on this browser never sees them. A draft to a link revoked meanwhile (on another
// device) stays until the owner has seen it, so its words are never lost without a word.
import { useSyncExternalStore } from "react";
import { useStore } from "./store";

/** A draft: its words, and the name of the link it is to, kept so it can be named once that link is gone. */
export interface LinkDraft { text: string; name: string; link: string }

const drafts = new Map<string, LinkDraft>();
const listeners = new Set<() => void>();
let version = 0;
// the drafts of the composers on the page now: Escape and a click outside leave Linked sessions open while one of
// these holds something typed
const mounted = new Map<string, number>();

const changed = () => {
  version += 1;
  for (const f of listeners) f();
};

/** The key of a link's instruction (a Check is one with fixed words: it has no draft). */
export const instructionDraft = (link: string) => `${link}\ninstruction`;
/** The key of the answer to question `seq` of a link. */
export const answerDraft = (link: string, seq: number) => `${link}\nanswer\n${seq}`;
const linkOf = (key: string) => key.split("\n")[0];

export function draftText(key: string): string {
  return drafts.get(key)?.text ?? "";
}

/** Keep what is typed (or forget it, once the field is empty). Those who read the drafts hear of a draft that comes or
 *  goes, not of each key pressed in one. */
export function keepDraft(key: string, text: string, name: string) {
  const had = drafts.get(key);
  if (!text) {
    if (had) { drafts.delete(key); changed(); }
    return;
  }
  drafts.set(key, { text, name, link: linkOf(key) });
  if (!had) changed();
}

export function dropDraft(key: string) {
  if (drafts.delete(key)) changed();
}

/** A composer on the page, from its mount to its unmount: while one holds something typed, the panel holds. */
export function composerShown(key: string): () => void {
  mounted.set(key, (mounted.get(key) ?? 0) + 1);
  return () => {
    const n = (mounted.get(key) ?? 1) - 1;
    if (n > 0) mounted.set(key, n);
    else mounted.delete(key);
  };
}

/** Whether a composer on the page holds something typed. */
export function composing(): boolean {
  for (const key of mounted.keys()) if (draftText(key)) return true;
  return false;
}

/** The drafts to links that are not live (revoked, on this device or another), each with its key. */
export function draftsToGone(live: ReadonlySet<string>): [string, LinkDraft][] {
  return [...drafts].filter(([, d]) => !live.has(d.link));
}

/** Whether any draft to `link` waits. */
export function hasDraftFor(link: string, key = instructionDraft(link)): boolean {
  return !!draftText(key);
}

/** The drafts, kept current on the page: a component that reads them renders again when one changes. */
export function useLinkDrafts(): number {
  return useSyncExternalStore((f) => { listeners.add(f); return () => { listeners.delete(f); }; }, () => version);
}

/** Every draft forgotten (a sign-out). */
export function forgetDrafts() {
  if (!drafts.size) return;
  drafts.clear();
  changed();
}

/** How many drafts wait: asked about before the page is left. */
export const draftCount = () => drafts.size;

// a sign-out, or another account in this tab: no draft of the last account's stays
useStore.subscribe((s, before) => {
  if ((s.user?.id ?? null) !== (before.user?.id ?? null)) forgetDrafts();
});

if (typeof window !== "undefined") {
  window.addEventListener("beforeunload", (e) => { if (drafts.size) { e.preventDefault(); e.returnValue = ""; } });
}
