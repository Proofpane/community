// What linked sessions send sealed, as the page shows it: every place a sealed field can arrive opens it with the keys
// this tab unlocked (e2e.ts), holds what opened to the server's rules for that field (textRules.ts) and shows it; while
// the keys are locked it shows "Encrypted" with Unlock, and where a text cannot be shown it says why, never showing the
// envelope. Opened text lives only in the state of the component that shows it, under the generation of the keys it
// was opened with: it is dropped at every lock and unlock, and never written to storage. What goes from one component
// to another is the text as served.
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { KeyRound, Loader2, Lock } from "lucide-react";
import { APP, server, type HistorySession, type HistoryTalk, type LinkEvent } from "../api";
import { eventSlot, historySlot, isSealed, openSealed, talkKeySlot, talkSlot, useE2E, type E2EState, type Opened } from "../e2e";
import type { TalkRef } from "../e2eKeys";
import { hostLabel } from "../places";
import { useStore } from "../store";
import { eventText, historyFields, questionRule, refusalWords, talkText, type Checked, type Refusal } from "../textRules";

/** The signed-in account's id, as signed payloads write it (the server's user id): the first part of every slot. */
export function useAccount(): string {
  return useStore((s) => s.user?.id ?? "");
}

/** Where a sealed text stands when it is not shown: the keys are not unlocked in this tab, it is being opened, it is
 *  refused (it could not be read, or what opened breaks a rule for its field; `why` is the opener's own word), or the
 *  page cannot open it: the desktop and phone apps, an account without encryption on, a browser without what it
 *  takes. */
export type Seal =
  | { at: "locked" }
  | { at: "opening" }
  | { at: "refused"; refusal: Refusal; why?: string }
  | { at: "app" | "off" | "unsupported" };

/** A text as the page shows it: in its place (opened, or as it came in the clear, `inClear` when it came so on a path
 *  encryption seals while encryption is on), or its seal. */
export type Shown<T> = { ok: true; value: T; inClear: boolean } | { ok: false; seal: Seal };

/** What opening a sealed text gave, by its envelope and its slot; undefined while it is being opened, and whenever the
 *  keys are not unlocked. */
export type Look = (envelope: string, slot: string) => Opened | undefined;

/** What useOpened gives the places that show sealed text: the tab's state, whether encryption is on now (a text in the
 *  clear on a path it seals is then marked; an account that turned it off but keeps its keys is locked, and nothing is
 *  marked), and what each opening gave. */
export interface Opener { state: E2EState; on: boolean; look: Look }

/** A seal in the words the page shows in the text's place. */
export function sealWords(seal: Seal): string {
  switch (seal.at) {
    case "locked": return "Encrypted";
    case "opening": return "Opening…";
    case "refused": return refusalWords(seal.refusal);
    case "app": return `Encrypted: read it in a browser at ${hostLabel(server()) || "the ADE's address"}`;
    case "off": return "Encrypted: encryption is not on for this account, so this page holds no key for it";
    case "unsupported": return "Encrypted: this browser cannot open it";
  }
}

const keyOf = (envelope: string, slot: string) => JSON.stringify([slot, envelope]);
const NOTHING: ReadonlyMap<string, Opened> = new Map();
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Open the sealed texts a component shows, while the keys are unlocked in this tab: each once, kept in this
 *  component's own state under the generation of the keys it was opened with (e2e.ts's, which rises at every lock and
 *  every unlock). What was opened under another generation is never given out and is dropped, even where a lock and an
 *  unlock both fall between two renders; an opening that answers after its generation has passed is not kept. */
export function useOpened(wanted: { envelope: string; slot: string; talk?: TalkRef }[]): Opener {
  const { state, on, generation } = useE2E();
  const unlocked = state === "unlocked" && !APP;
  const [got, setGot] = useState<{ generation: number; map: ReadonlyMap<string, Opened> }>(() => ({ generation, map: NOTHING }));
  const asked = useRef({ generation, keys: new Set<string>() });
  useEffect(() => {
    setGot((had) => (had.generation === generation ? had : { generation, map: NOTHING }));
  }, [generation]);
  useEffect(() => {
    if (!unlocked) return;
    if (asked.current.generation !== generation) asked.current = { generation, keys: new Set() };
    const todo = new Map<string, { envelope: string; slot: string; talk?: TalkRef }>();
    for (const w of wanted) {
      const k = keyOf(w.envelope, w.slot);
      if (isSealed(w.envelope) && !asked.current.keys.has(k)) todo.set(k, w);
    }
    if (!todo.size) return;
    for (const k of todo.keys()) asked.current.keys.add(k);
    const each = [...todo];
    void Promise.all(each.map(([, w]) => openSealed(w.envelope, w.slot, w.talk).catch((e): Opened => ({ ok: false, why: message(e) }))))
      .then((results) => {
        setGot((had) => {
          if (had.generation !== generation) return had;
          const next = new Map(had.map);
          each.forEach(([k], i) => next.set(k, results[i]));
          return { generation, map: next };
        });
      });
  });
  const look = useCallback<Look>((envelope, slot) =>
    (unlocked && got.generation === generation ? got.map.get(keyOf(envelope, slot)) : undefined), [unlocked, got, generation]);
  return useMemo(() => ({ state, on: !!on, look }), [state, on, look]);
}

/** Where a sealed text stands: open (the value it opened to), or its seal. */
export function sealOf(state: E2EState, opened: Opened | undefined): { open: unknown } | { seal: Seal } {
  if (APP) return { seal: { at: "app" } };
  if (state === "unlocked") {
    if (!opened) return { seal: { at: "opening" } };
    return opened.ok ? { open: opened.value } : { seal: { at: "refused", refusal: { why: "unreadable" }, why: opened.why } };
  }
  if (state === "locked") return { seal: { at: "locked" } };
  return { seal: { at: state } };
}

/** A text as the page shows it: a sealed one opened as `slot` binds it and held to `check`, which says what the opened
 *  value must be and gives its text; one in the clear as it came, marked where its path is one encryption seals
 *  (`sealedPath`) and encryption is on. */
export function shown(text: unknown, slot: string, check: (value: unknown) => Checked<string>, sealedPath: boolean,
                      { state, on, look }: Opener): Shown<string> {
  if (!isSealed(text)) return { ok: true, value: String(text ?? ""), inClear: sealedPath && on };
  const at = sealOf(state, look(text, slot));
  if ("seal" in at) return { ok: false, seal: at.seal };
  const c = check(at.open);
  return c.ok ? { ok: true, value: c.value, inClear: false } : { ok: false, seal: { at: "refused", refusal: c.refusal } };
}

// ---- the paths: events, conversations, History rows -----------------------------------------------------------------

const SEALED_KINDS = new Set(["step", "question", "waiting", "done", "note", "prompt", "proposal"]);
const STOP_ID = /^stopped:([0-9]{1,13})$/;
const LAST_TIME = 253402300799;            // the last second of the year 9999, past which no time is written
const REPORT_ID = /^report:[0-9]{1,19}$/;
/** What a session reports of its own prompt: its tool ran or failed, its
 *  turn ended, or a new prompt came; nothing of the call. */
const REPORTED = new Set(["PostToolUse", "PostToolUseFailure", "Stop", "StopFailure", "UserPromptSubmit"]);

/** The texts a link sends in the clear on purpose, as the server's link.fixed_text takes them: a waiting that says
 *  exactly "working on it"; the note a session's hook posts when it sees a stop, its client_id stopped:TIME and its
 *  text "stopped at" and that time, written YYYY-MM-DDTHH:MM:SSZ; and the note that reports what became of one of its
 *  prompts, its client_id report:SEQ and its text "reported: " and one of five events, with no mirror, session or
 *  time said. */
function fixedText(ev: LinkEvent): boolean {
  if (ev.kind === "waiting") return ev.text === "working on it";
  if (ev.kind === "note" && REPORT_ID.test(ev.client_id ?? "")) {
    return !ev.mirror && !ev.session && ev.said_at == null && ev.text.startsWith("reported: ") && REPORTED.has(ev.text.slice(10));
  }
  const stop = ev.kind === "note" ? STOP_ID.exec(ev.client_id ?? "") : null;
  if (!stop || Number(stop[1]) > LAST_TIME) return false;
  return ev.text === `stopped at ${new Date(Number(stop[1]) * 1000).toISOString().replace(/\.[0-9]{3}Z$/, "Z")}`;
}

/** Whether an event is on a path encryption seals: a link's step, question, waiting, done or note to the owner (to
 *  owner, to its own name, or to no one named, all of which the server takes as to the owner); but not the two texts
 *  sent in the clear on purpose (fixedText). Events to all or to another session stay in the clear. */
export function sealedEvent(ev: LinkEvent): boolean {
  if ((ev.from_id ?? ev.from) === "owner" || !SEALED_KINDS.has(ev.kind) || (!!ev.to && ev.to !== "owner" && ev.to !== ev.from)) return false;
  return !fixedText(ev);
}

/** The slot an event's text is sealed to: as `linkId`, the link that posted it, sealed it for this account. */
export const eventSlotOf = (ev: LinkEvent, linkId: string, account: string) =>
  eventSlot(account, linkId, ev.kind, ev.mirror, ev.ref, ev.client_id ?? "");

/** An event's text as the page shows it, sealed by `linkId` (the link that posted it): what it says of its session and
 *  of when it was said must be what the server serves beside it, and a question is held to the rules the server holds
 *  a question to (an answer carries its digest). */
export function eventShown(ev: LinkEvent, linkId: string, account: string, opener: Opener): Shown<string> {
  const check = (value: unknown): Checked<string> => {
    const t = eventText(value, ev);
    if (!t.ok || ev.kind !== "question") return t;
    const broken = questionRule(t.value);
    return broken ? { ok: false, refusal: broken } : t;
  };
  return shown(ev.text, eventSlotOf(ev, linkId, account), check, sealedEvent(ev), opener);
}

/** The slot of message `i` of a listed session's conversation: its place, the list's length, how many earlier
 *  messages stayed on the machine, who said it and its times, as served. */
export const talkSlotOf = (talk: HistoryTalk, i: number, s: Pick<HistorySession, "id" | "link_id">, account: string) =>
  talkSlot(account, s.link_id, s.id, i, talk.messages.length, talk.earlier, talk.messages[i].who, talk.messages[i].at,
           talk.messages[i].ms);

/** What a listed session's conversation holds for opening its messages: its key and key_id as served (a post-quantum
 *  account's conversation sealed under one key; none for one sealed message by message)
 *  and the slot that key is bound to (its list's length and how many earlier messages stayed on the machine). */
export const talkRefOf = (talk: HistoryTalk, s: Pick<HistorySession, "id" | "link_id">, account: string): TalkRef => ({
  key: talk.key ?? null, key_id: talk.key_id ?? null,
  keySlot: talkKeySlot(account, s.link_id, s.id, talk.messages.length, talk.earlier),
});

/** Message `i` of a listed session's conversation as the page shows it. Every message is on a path encryption seals. */
export function talkShown(talk: HistoryTalk, i: number, s: Pick<HistorySession, "id" | "link_id">, account: string,
                          opener: Opener): Shown<string> {
  return shown(talk.messages[i].text, talkSlotOf(talk, i, s, account), talkText, true, opener);
}

/** A History row as the page shows it. A sealed row's fields are those it opened to, and none at all while it is not
 *  open (`seal` says where it stands): the server's own fields of a sealed row are never shown. `raw` is the row as
 *  served, what goes on to whatever opens it, so no opened text leaves the component showing it. */
export interface ShownRow extends HistorySession {
  raw: HistorySession;
  seal: Seal | null;
  /** it came in the clear while encryption is on */
  inClear: boolean;
}

/** A row came sealed: the server served its fields as one sealed text. */
export const rowSealed = (s: HistorySession) => s.sealed !== undefined && s.sealed !== null;
const rowSlot = (s: HistorySession, account: string) => historySlot(account, s.link_id, s.id, !!s.withheld);

function rowShown(s: HistorySession, account: string, { state, on, look }: Opener): ShownRow {
  if (!rowSealed(s)) {
    const said = !!(s.title || s.project || s.branch || s.repositories?.length);
    return { ...s, raw: s, seal: null, inClear: said && on };
  }
  const none = { title: null, project: null, branch: null, repositories: undefined, raw: s, inClear: false };
  if (!isSealed(s.sealed)) return { ...s, ...none, seal: { at: "refused", refusal: { why: "unreadable" } } };
  const at = sealOf(state, look(s.sealed, rowSlot(s, account)));
  if ("seal" in at) return { ...s, ...none, seal: at.seal };
  const fields = historyFields(at.open, !!s.withheld);
  if (!fields.ok) return { ...s, ...none, seal: { at: "refused", refusal: fields.refusal } };
  return { ...s, ...fields.value, repositories: fields.value.repositories ?? undefined, raw: s, seal: null, inClear: false };
}

/** A History list as the page shows it (ShownRow), the same list as long as nothing it is made from changes. */
export function useShownRows(rows: HistorySession[]): ShownRow[] {
  const account = useAccount();
  const opener = useOpened(rows.filter(rowSealed).map((s) => ({ envelope: String(s.sealed), slot: rowSlot(s, account) })));
  return useMemo(() => rows.map((s) => rowShown(s, account, opener)), [rows, account, opener]);
}

// ---- on the page ----------------------------------------------------------------------------------------------------

/** Unlock: one touch of one of the account's passkeys or security keys, from the person's own click, nothing awaited
 *  before it. Offered while the keys are locked, in a browser. */
export function Unlock() {
  const e2e = useE2E();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (APP || e2e.state !== "locked") return null;
  const unlock = async () => {
    setBusy(true);
    setError("");
    try { await e2e.unlock(); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5 not-italic">
      <button className="btn btn-sm !h-6" disabled={busy} onClick={() => void unlock()}
        title="Open here what your linked sessions sent encrypted, with one of your passkeys">
        {busy ? <Loader2 size={12} className="animate-spin" /> : <KeyRound size={12} />} Unlock
      </button>
      {error && <span role="alert" className="text-[11.5px] text-fail">{error}</span>}
    </span>
  );
}

/** A seal in words, in the text's place: "Encrypted" and Unlock while the keys are locked (`unlock` false where Unlock
 *  is offered once for a whole list), else where it stands. */
export function SealWords({ seal, unlock = true }: { seal: Seal; unlock?: boolean }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span className="italic text-muted" title={seal.at === "refused" ? seal.why : undefined}>
        {seal.at !== "refused" && <Lock size={11} className="mr-1 inline" />}{sealWords(seal)}
      </span>
      {seal.at === "locked" && unlock && <Unlock />}
    </span>
  );
}

/** The mark on a text that came in the clear on a path encryption seals, while encryption is on, or the keys served
 *  are older than this browser saw (E2E.on): its words say nothing of whether the server says encryption is on. */
export function InClear() {
  return (
    <span className="pill text-wait" title="This came in the clear on a path encryption seals: the server holds it as it reads">
      sent in the clear
    </span>
  );
}

/** The mark on the owner's own commands in a conversation while encryption is on: signed with a passkey and checked by
 *  the server, which reads them. */
export function KeptClear() {
  return (
    <span className="pill text-muted" title="Your commands are signed with a passkey and checked by the server, which reads them: they are not encrypted">
      kept in the clear
    </span>
  );
}

/** Above a list some of whose texts are encrypted while the keys are locked: one Unlock for them all. */
export function EncryptedBar() {
  return (
    <div className="mb-2 flex flex-wrap items-center gap-2 rounded-md border border-line px-2.5 py-1.5 text-[11.5px] text-muted">
      <Lock size={12} className="flex-none" /> <span>Some of this is encrypted: unlock it with one of your passkeys to read it here.</span>
      <Unlock />
    </div>
  );
}

/** A text that may have come sealed, shown in its place by `children` (opened, or as it came, with "sent in the clear"
 *  beside one that came so), or its seal's words (`unlock` as SealWords takes it); never the envelope. Each place
 *  builds it from useOpened and its path's eventShown or talkShown; a History row comes from useShownRows. */
export function ShownText({ shown: s, unlock, children }: { shown: Shown<string>; unlock?: boolean; children: (text: string) => ReactNode }) {
  if (!s.ok) return <SealWords seal={s.seal} unlock={unlock} />;
  return <>{children(s.value)}{s.inClear && <> <InClear /></>}</>;
}
