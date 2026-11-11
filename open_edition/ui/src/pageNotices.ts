// The page's own notifications, shown while its tab is in the background (App.tsx): in fixed words, as the server's
// push notices are (ade/push.py WORDS), never a session's title, its repository, a tool it wants, a command or an
// agent's words, which stay on the page; the notification centre, its history and a locked screen show only what kind
// of thing waits and where to look.

export const PAGE_NOTICES = {
  "session-waits": { title: "A session waits for you", body: "Open the ADE." },
  "session-finished": { title: "A session finished its turn", body: "Open the ADE." },
  "session-ask": { title: "An agent asks for a session", body: "Open Approvals." },
} as const;

export type PageNotice = keyof typeof PAGE_NOTICES;

/** Show the notification of `kind` in its fixed words, replacing an earlier one with the same `tag` (an id the page
 *  holds, never a name). A browser that shows notifications only from a service worker (nearly every mobile browser,
 *  MDN says) refuses the constructor with a TypeError: the notice is then not shown from the page, which goes on; a
 *  device that turned push notices on still gets those. */
export function pageNotify(kind: PageNotice, tag: string): void {
  const words = PAGE_NOTICES[kind];
  try {
    new Notification(words.title, { body: words.body, tag });
  } catch {
    // refused by this browser: nothing is shown from the page
  }
}
