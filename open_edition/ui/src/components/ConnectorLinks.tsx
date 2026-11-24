import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Api, ApiError, type ConnectorLink, type ConnectorLinkPost } from "../api";
import { fieldBlank, fieldItems, fieldText } from "../checkpointFields";
import { visible } from "../textRules";
import { dayTime, full, near } from "../when";
import { AppName } from "./AppName";
import { Confirm } from "./bits";
import { ago, lineText } from "./Channel";

// Linked sessions: the sessions linked through a connected app (the server's ade/connector_link.py). Such a session is
// no link of the session channel's: it holds no key, so it takes nothing the owner signs, and it is kept and read
// apart from the links the panel lists above it. Its card shows what it says of itself (its name, its checkpoint, its
// posts and its questions, each its own words) and asks of it only what carries no words: a question dismissed, a
// request to stop, its removal. So a card has no field to type in, and no request it makes has a body.

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
/** The server knows no such session, or no such open question of it: what the page lists is no longer so. */
const stale = (e: unknown) => e instanceof ApiError && e.status === 404;
/** A time as the server writes it, in seconds, as `ago` reads one. */
const iso = (at: number) => new Date(at * 1000).toISOString();

/** What these sessions are, said once over them: how they differ from a session linked with a ticket. */
export const WHICH_LINK = "These sessions linked themselves through an app you connected, with no ticket and no key of their own: the app's connection answers for every session linked through it, so any chat of that Claude account that uses the same app can post under a name linked through it, and each name is the session's own words. They post what they do and what they ask, and keep a checkpoint, which is shown here and never given back to them. Their questions raise no badge and no notice: you see them here. A link of this kind carries none of your words: it takes no instruction, no answer and no resume, so answer a session in its own chat; a stop is a request in fixed words, asked with your sign-in alone and not signed, and it only stops. A session that must take what you sign, send sealed, or read its own checkpoint back is linked with a ticket instead (Link a session).";
/** Said in the section where the list could not be read: a read that failed is not a list of none. */
export const NOT_READ = "The sessions linked through a connected app could not be read just now. What is listed here, if anything, is as it was last read.";
/** Said under each of its open questions, where the question of a session linked with a key has Answer. */
export const ANSWER_IN_CHAT = "Answer it in the session's own chat: this link carries no answer.";
export const DISMISS_SAID = "Dismissing closes the question here; it carries no answer. Its session is told that it was closed, in fixed words, if it calls link_session within a day.";
export const STOP_ASKS = "It reads the request in the answer to its next call, taken or refused, in fixed words: nothing here can hold it.";
export const REMOVE_SAID = "Removing ends its link and deletes its posts and checkpoint here. Its session can link again under the same name: to stop that, turn linking off for its app in Connected apps.";

/** The account's sessions linked through a connected app, read once as it mounts and again whenever `dep` is another
 *  (the panel gives the store's linked sessions, which are another each time the store reads them: as the stamp of
 *  the linked sessions moves, and on Read again); `reload` reads them at once, as a card does after a
 *  request of its own. Never on a timer of its own: what one of these sessions posts moves the stamp of the linked
 *  sessions (the server's ade/summary.py takes the sessions linked through an app into it), so the store reads its
 *  list again at its next poll, and this one is read with it. A read that fails keeps what was last read and says
 *  so (`failed`): the deployment's connector off answers 404. */
export function useConnectorLinks(dep: unknown): { links: ConnectorLink[]; failed: boolean; reload: () => Promise<void> } {
  const [links, setLinks] = useState<ConnectorLink[]>([]);
  // a read that failed is not a list of none: what was last read stays, and `failed` says so. Only the deployment's
  // connector off (404) is none, and says nothing.
  const [failed, setFailed] = useState(false);
  // the newest read's answer alone is kept: one asked before it and answered after it says what was, not what is
  const asked = useRef(0);
  const reload = useCallback(async () => {
    const mine = ++asked.current;
    let got: ConnectorLink[] | null = null;
    try {
      const r = await Api.connectorLinks();
      if (Array.isArray(r?.links)) got = r.links;
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) got = [];
    }
    if (asked.current !== mine) return;
    if (got) setLinks(got);
    setFailed(got === null);
  }, []);
  useEffect(() => { void reload(); }, [reload, dep]);
  return { links, failed, reload };
}

/** The sessions linked through a connected app, under those linked with a key; nothing where there is none, and where
 *  the list could not be read, that said (`failed`) over whatever was last read. */
export function ConnectorLinks({ links, failed = false, reload }: { links: ConnectorLink[]; failed?: boolean; reload: () => Promise<void> }) {
  if (links.length === 0 && !failed) return null;
  return (
    <section aria-label="Linked through a connected app" className="space-y-2 rounded-xl border border-line p-2">
      <h3 className="px-1 text-[11.5px] font-medium text-fg-dim">Linked through a connected app</h3>
      {failed && <p role="alert" className="px-1 text-[11.5px] text-fail">{NOT_READ}</p>}
      {links.length > 0 && <p role="note" className="px-1 text-[11.5px] text-muted">{WHICH_LINK}</p>}
      {links.map((l) => <ViaApp key={l.id} l={l} reload={reload} />)}
    </section>
  );
}

/** One of its open questions: its words, where to answer it, and Dismiss, which carries none of the owner's. */
function Asks({ q, busy, onDismiss }: { q: ConnectorLink["questions"][number]; busy: boolean; onDismiss: () => void }) {
  const said = useId();
  return (
    <div className="mt-2 rounded-md border border-wait/50 bg-wait/[0.05] px-2.5 py-2">
      <div className="mb-1 text-[11px] text-muted" title={full(q.at)}>It asks you{" · "}{ago(iso(q.at))}</div>
      <div className="whitespace-pre-wrap break-words text-[12.5px]">{visible(q.text)}</div>
      <p className="mt-1.5 text-[11.5px] text-fg-dim">{ANSWER_IN_CHAT}</p>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        <button className="btn btn-sm" aria-describedby={said} disabled={busy} onClick={onDismiss}>Dismiss question</button>
        <span id={said} className="text-[11px] text-muted">{DISMISS_SAID}</span>
      </div>
    </div>
  );
}

function ViaApp({ l, reload }: { l: ConnectorLink; reload: () => Promise<void> }) {
  const [posts, setPosts] = useState<ConnectorLinkPost[] | null>(null);
  const [before, setBefore] = useState<number | null>(null);
  const [error, setError] = useState("");
  // one request of the card's at a time: its buttons wait while one is under way, so none is sent twice; and one
  // page of its posts at a time, so none is listed twice
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  // its whole checkpoint, shown on request: the document as text, whatever it holds
  const [whole, setWhole] = useState(false);
  const stops = useId();
  /** A request of the card's, then the list read again. A failure is said in the card, and the list left as it was
   *  read; but where the server answers that what was asked of is no longer there (`stale`), the list is read again
   *  too, so a session that has ended goes from it, and a question closed meanwhile goes from its card. */
  const act = async (request: () => Promise<unknown>) => {
    setBusy(true); setError("");
    try { await request(); await reload(); } catch (e) { setError(message(e)); if (stale(e)) await reload(); } finally { setBusy(false); }
  };
  const more = async (from: number | null) => {
    setReading(true);
    try {
      const r = await Api.connectorLinkPosts(l.id, from);
      // an older page answered after the list was put away does not open it again
      setPosts((cur) => (from === null ? r.posts : cur && [...cur, ...r.posts]));
      setBefore(r.before);
    } catch (e) { setError(message(e)); if (stale(e)) await reload(); } finally { setReading(false); }
  };
  // its checkpoint's fields as a linked session's card reads them (checkpointFields.ts): a list's items, text as one,
  // anything else as its JSON, and blank text as nothing
  const cp = l.checkpoint && typeof l.checkpoint === "object" ? l.checkpoint : null;
  const doing = cp && !fieldBlank(cp.doing) ? fieldText(cp.doing) : null;
  const next = fieldItems(cp?.next);
  const waiting = fieldItems(cp?.waiting_on);
  const [label, field] = ["w-[4.5rem] flex-none text-muted", "min-w-0 flex-1 whitespace-pre-wrap break-words text-fg-dim"];
  return (
    <div aria-label={`Linked through an app: ${l.name}`} className="rounded-lg border border-line p-3 text-[12.5px]">
      <div>
        &ldquo;<AppName name={l.name} className="font-semibold" />&rdquo; <span className="text-muted">(its own words)</span>
        {" · "}through &ldquo;<AppName name={l.app.name} className="font-medium" />&rdquo; <span className="text-muted">(its own word)</span>
      </div>
      <div className="mt-1 text-fg-dim" title={`Linked ${full(l.linked_at)}${l.heard_at !== null ? `; last heard ${full(l.heard_at)}` : ""}`}>
        Linked {ago(iso(l.linked_at))}. {l.heard_at !== null ? `Last heard ${ago(iso(l.heard_at))}` : "Not heard yet"}: it is heard only when it calls.
      </div>
      {cp && (
        <div role="group" aria-label="Its checkpoint" className="mt-1.5 text-[12px]">
          {/* any chat of the Claude account can have written it: it is the session's own words, drawn as text */}
          <div className="text-[11px] text-muted">Its checkpoint (its own words)</div>
          {doing === null && next.length === 0 && waiting.length === 0 ? (
            <div className="text-muted">(its checkpoint says nothing under doing, next or waiting_on, the fields shown here)</div>
          ) : (
            <dl className="space-y-0.5">
              {doing !== null && <div className="flex gap-2"><dt className={label}>Doing</dt><dd className={field}>{visible(doing)}</dd></div>}
              {next.length > 0 && (
                <div className="flex gap-2">
                  <dt className={label}>Next</dt>
                  <dd className="min-w-0 flex-1 text-fg-dim">
                    <ul className="list-disc pl-4">
                      {next.slice(0, 5).map((n, i) => <li key={i} className="whitespace-pre-wrap break-words">{visible(n)}</li>)}
                      {next.length > 5 && <li className="list-none text-muted">and {next.length - 5} more</li>}
                    </ul>
                  </dd>
                </div>
              )}
              {waiting.length > 0 && <div className="flex gap-2"><dt className={label}>Waiting on</dt><dd className={field}>{waiting.map(visible).join(" · ")}</dd></div>}
            </dl>
          )}
          {l.checkpoint_at !== null && (
            <div className="mt-0.5 text-[11px] text-faint" title={full(l.checkpoint_at)}>checkpoint version {l.checkpoint_version}, {ago(iso(l.checkpoint_at))}</div>
          )}
        </div>
      )}
      {l.last && (
        <div aria-label="Latest" className="mt-1.5">
          <div className="text-[11px] text-muted" title={full(l.last.at)}>Latest{" · "}{l.last.kind}{" · "}{ago(iso(l.last.at))}</div>
          <div className="whitespace-pre-wrap break-words text-fg-dim">{visible(l.last.text)}</div>
        </div>
      )}
      {l.questions.map((q) => <Asks key={q.seq} q={q} busy={busy} onDismiss={() => void act(() => Api.closeConnectorQuestion(l.id, q.seq))} />)}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {l.stop_asked_at === null ? (
          <>
            <button className="btn btn-sm" aria-describedby={stops} disabled={busy} onClick={() => void act(() => Api.stopConnectorLink(l.id, true))}>Ask it to stop</button>
            <span id={stops} className="text-[11px] text-muted">{STOP_ASKS}</span>
          </>
        ) : (
          <>
            {/* a request that has stood for more than half a day says its day too, which its clock alone would not */}
            <span className="text-wait" title={full(l.stop_asked_at)}>
              Asked to stop {near(l.stop_asked_at)}: it reads that in the answer to its next call, and is told to go on only on your word in its own chat.
            </span>
            <button className="btn btn-sm" disabled={busy} onClick={() => void act(() => Api.stopConnectorLink(l.id, false))}>Withdraw the request</button>
          </>
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <button className="btn btn-sm" aria-expanded={!!posts} disabled={!posts && reading} onClick={() => (posts ? setPosts(null) : void more(null))}>
          {posts ? "Hide its posts" : "Its posts"}
        </button>
        {cp && (
          <button className="btn btn-sm" aria-expanded={whole} onClick={() => setWhole(!whole)}>
            {whole ? "Hide its whole checkpoint" : "Its whole checkpoint"}
          </button>
        )}
        <Confirm label="Remove" again="Click again to remove it" disabled={busy} onConfirm={() => act(() => Api.removeConnectorLink(l.id))} />
        <span className="text-[11px] text-muted">{REMOVE_SAID}</span>
      </div>
      {cp && whole && (
        <section aria-label="Its whole checkpoint" className="mt-2 rounded bg-bg p-2">
          <div className="mb-1 text-[11px] text-muted">The whole checkpoint, as this page reads it (its session's own words): text only, and nothing in it is a link or an instruction. A browser does not keep every number as it was sent: one longer than it holds exactly is shown rounded, one too large as null; and names that are whole numbers come first.</div>
          {/* the document as JSON, each character that does not show itself written out (textRules.ts): a text node,
              so a link, an image or markup in it is the characters it is */}
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] text-fg-dim">{visible(JSON.stringify(cp, null, 2))}</pre>
        </section>
      )}
      {posts && (
        <div className="mt-2 max-h-72 overflow-y-auto rounded bg-bg p-2">
          {/* each post on one line, its line breaks written out, so one post cannot pass for two (lineText) */}
          <ul aria-label="Its posts" className="space-y-0.5 font-mono text-[11px] text-fg-dim">
            {posts.length === 0 && <li className="font-sans text-[11.5px] text-muted">It has posted nothing yet.</li>}
            {posts.map((p) => <li key={p.seq} className="whitespace-pre-wrap break-words" title={full(p.at)}>{dayTime(p.at)} {p.kind}: {lineText(p.text)}</li>)}
          </ul>
          {before !== null && <button className="btn btn-sm mt-1" disabled={reading} onClick={() => void more(before)}>More</button>}
        </div>
      )}
      {error && <div role="alert" className="mt-1 text-fail">{error}</div>}
    </div>
  );
}
