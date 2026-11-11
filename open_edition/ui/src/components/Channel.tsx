import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowDown, ArrowLeft, AtSign, Check, Cloud, Copy, ExternalLink, Hourglass, KeyRound, Laptop, Link2, Loader2, Lock, MessagesSquare, MoreHorizontal, MoveRight, Pause, Play, Power, Radio, RefreshCw, Send, WifiOff, X } from "lucide-react";
import { api, Api, ApiError, APP, server, type HistorySession, type HistoryTalk, type LinkAsk, type LinkEvent, type LinkMove, type LinkView, type StorageOption } from "../api";
import { useE2E, useE2EStore, type E2EState } from "../e2e";
import { E2EApi } from "../e2eKeys";
import { confirmWays, MOVE_LIFE, passkeysHere, prepareSigned, signPrepared, textDigest, type Prepared, type Signed, type SignedPurpose } from "../passkey";
import { SignedBy, SignOnMac, useKnownPasskeys, useSignerPaired } from "./SignOnMac";
import type { SignedFacts } from "../secondScreen";
import { hostLabel } from "../places";
import { useOffline, useStore } from "../store";
import { useMedia } from "../useMedia";
import { codePoint, DRAWN_OVER, HIDDEN, SECRET_SHAPES, visible } from "../textRules";
import { ADE_URL, ASK_ADDRESS, CLIENT, FINGERPRINT, inForm, LINK_NAME, TICKET } from "../shellForms";
import { answerDraft, composerShown, composing, draftText, draftsToGone, dropDraft, instructionDraft, keepDraft, useLinkDrafts, type LinkDraft } from "../linkDrafts";
import { ConfirmIt, type Change } from "./AccountDialog";
import { Confirm, holdsText, timeAgo, useMenu } from "./bits";
import { focusables, useDialog } from "./Modal";
import { useSlashMenu } from "./SlashMenu";
import { CameBack, TakenLine, useAct, useActs, type Acts } from "./Acting";
import { fieldBlank, fieldItems, fieldText } from "../checkpointFields";
import { CLIENT_WORDS, MachineSessions, resumeCommand, sessionRepository, type Moved } from "./History";
import { MarkdownCode } from "./MarkdownCode";
import { KEEP } from "./StorageChoice";
import { OutLink } from "./OutLink";
import { EncryptedBar, eventShown, eventSlotOf, InClear, KeptClear, rowSealed, SealWords, ShownText, talkRefOf, talkShown, talkSlotOf, Unlock, useAccount, useOpened, useShownRows, type Seal, type Shown } from "./Sealed";
import { PromptCard, ProposalCard } from "./LinkInbox";
import { clock, dayTime, full } from "../when";

export { visible };

const isoOf = (unix: number | null | undefined) => (unix ? new Date(unix * 1000).toISOString() : undefined);
/** When a link was last heard, as its card and its conversation say it: the server's time heard, by any request the
 *  link signed; its heartbeat from a server that gives none. */
const heardAt = (l: Pick<LinkView, "last_heard_at" | "last_heartbeat_at">) =>
  (l.last_heard_at !== undefined ? l.last_heard_at : l.last_heartbeat_at);
// how the server says a link is heard, so that "silent" keeps its meaning (ade/link.py heard_through):
// through its hook, at each run of it; through its client alone, whose background heartbeat keeps it heard while its
// session runs; or through its client alone, only when its session calls it, so that not being heard does not say it
// stopped. Nothing where the server says nothing, or a word this page does not know
const HEARD_THROUGH: Record<string, string> = {
  hook: "through its hook",
  keeper: "through its client alone, which keeps it heard while its session runs",
  client: "through its client alone, when its session calls it",
};
/** What a keeper link's silence can mean, said beside it: its keeper beats while its session's
 *  process runs, so its session ended; or its container restarted, which ends the keeper, and its session has not
 *  called its client since, which would start another and be heard. */
export const KEEPER_SILENCE = ": its session ended, or its container restarted and it has not called its client since";
export const heardThrough = (l: Pick<LinkView, "heard_through">): string | null =>
  (typeof l.heard_through === "string" && Object.prototype.hasOwnProperty.call(HEARD_THROUGH, l.heard_through)
    ? HEARD_THROUGH[l.heard_through] : null);

/** A link's name as names are told apart, as the server's link.name_key has it: case folded, each run of spaces made
 *  one, the ends trimmed, so `My Mac`, `my  mac` and `my mac ` are one name. */
export const nameKey = (name: string) => name.split(/\s+/).filter(Boolean).join(" ").toLowerCase();

/** Whether a name has two spaces together or one at its end, as the server's link.spaced_badly has it: a new link's
 *  name may not, so no session can ask under a name kept so from before that rule. */
export const spacedBadly = (name: string) => / $| {2}/.test(name);

/** A link's name as the page shows it: its spaces as they are, so names that differ only there differ on the page. */
function Name({ name, className = "" }: { name: string; className?: string }) {
  return <bdi className={`whitespace-pre ${className}`}>{name}</bdi>;
}

/** An event's line up to its words: its seq, its kind and where it stands, who sent it and to whom. */
export function linkHead(ev: Pick<LinkEvent, "seq" | "kind" | "from" | "to"> & { state?: LinkEvent["state"] }): string {
  return `[${ev.seq}] ${ev.kind}${ev.state ? ` (${ev.state})` : ""} ${ev.from}>${ev.to}: `;
}

/** An event's words on one line, as linkLine writes them. */
export function lineText(text: string): string {
  return visible(String(text)).replace(/\\/g, "\\\\").replace(/\r/g, "\\r").replace(/\n/g, "\\n");
}

/** One event as one line, as the session posted it, its line breaks written as \n so a post cannot pass for two. */
export function linkLine(ev: Pick<LinkEvent, "seq" | "kind" | "from" | "to" | "text"> & { state?: LinkEvent["state"] }): string {
  return linkHead(ev) + lineText(ev.text);
}

/** How long ago, as words: "just now" under 45 seconds, else "5m ago" (timeAgo alone says "now" there). */
export function ago(iso?: string, now = Date.now()): string {
  const t = timeAgo(iso, now);
  return t === "now" ? "just now" : t ? `${t} ago` : "";
}

/** Signing works where the account's passkeys do: a browser at the server's own address. */
export const signsHere = () => !APP && passkeysHere;
export const SIGN_ELSEWHERE = () => `Commands are signed with one of your passkeys, which work only in a browser at ${hostLabel(server())}: send them from there.`;
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The text rules the server holds commands to, said before signing rather than after, on the text as it will be
 *  signed (in normal form C, which can change its length). */
export function commandProblem(text: string): string | null {
  if (!text.trim()) return "Write what the session should do.";
  const nfc = text.normalize("NFC");
  if ([...nfc].length > 4000) return "A command is at most 4,000 characters.";
  if (nfc.startsWith("e2e1.")) return "The text begins e2e1., as an encrypted text does: one in the clear may not, so it is never read as one.";
  const hidden = HIDDEN.exec(nfc);
  if (hidden) return `The text holds ${codePoint([...hidden[0]].pop() ?? "")}, which is not shown as itself, so it could change how it reads.`;
  const over = DRAWN_OVER.exec(nfc);
  if (over) return `The text holds ${codePoint(over[0])}, a mark drawn over or around the character before it, so it could change how it reads.`;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u0008\u000B-\u001F\u007F-\u009F]/.test(text)) return "The text holds a control character; only line breaks and tabs are kept.";
  const secret = SECRET_SHAPES.find(([, shape]) => shape.test(nfc));
  if (secret) return `The text holds something shaped like ${secret[0]}; the ADE keeps no secret.`;
  return null;
}

const shellQuote = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`;

/** What the pair command's --e2e says of the account's newest statement of its encryption keys, as the ticket's answer
 *  gives it ({seq, key_id}, key_id null once encryption is off; null with none): SEQ:KEY_ID, SEQ:off or 0:none, so a new
 *  link refuses any statement older. Null when the answer says nothing, or nothing in that form: the line goes into a
 *  shell. */
export function e2eFlag(stated: { seq?: unknown; key_id?: unknown } | null | undefined): string | null {
  if (stated === undefined) return null;
  if (stated === null) return "0:none";
  const { seq, key_id: keyId } = stated;
  if (typeof seq !== "number" || !Number.isSafeInteger(seq) || seq < 1) return null;
  if (keyId === null) return `${seq}:off`;
  return typeof keyId === "string" && /^[0-9a-f]{32}$/.test(keyId) ? `${seq}:${keyId}` : null;
}

/** The link client as SETUP_A.md installs it (shellForms, where Account and History take it from too). */
export { CLIENT };

/** Where a session to link runs, as the page asks it: this Mac (a local link) or claude.ai (a cloud one). */
export type Where = "local" | "cloud";

/** A claude.ai session's page, as its own shell writes it: its CLAUDE_CODE_REMOTE_SESSION_ID less the cse_ (the
 *  manual). */
const CLOUD_REF = `"https://claude.ai/code/session_\${CLAUDE_CODE_REMOTE_SESSION_ID#cse_}"`;

/** The ticket's answer, as the server gives it (Api.linkTicket): with `ticket_id`, which the pairing it makes is listed
 *  under (LinkAsk.ticket_id), and `pair_hold`, the seconds that pairing then waits before the link is made. */
export interface TicketOut {
  ticket: string; expires_at: number; url: string; keys?: string[]; high_security?: boolean;
  e2e?: { seq: number; key_id: string | null } | null; ticket_id?: string; pair_hold?: number;
}

/** The fingerprints a pair line may carry: those in the form the server writes them, as the line goes into a shell. */
const pinnable = (keys?: string[]) => (keys ?? []).filter((k) => inForm(FINGERPRINT, k));

/** The one line that links a session (or links it again, keeping its cursor and checkpoint) and, on a machine, puts
 *  its hooks in its .claude/settings.local.json (ade_link.py setup), with nothing to fill in:
 *  the client where SETUP_A.md installs it; the address the ADE says it signs for, not the one this page happens to
 *  be at; the ticket; the account's passkeys by their fingerprints (--keys), so the session pins them as it links and
 *  verifies what the owner signs from the start (while high security is on, the account's hardware keys alone); the
 *  account's newest statement of its encryption keys (--e2e), when the ticket's answer says it; and the session's
 *  name and kind as the owner gave them; on this Mac, --mirror, so its conversation comes to this page (the card says
 *  so, and that leaving it out links without it). A claude.ai session's line names its page from its own shell. Null
 *  when the answer's address or ticket is not in the form the server writes it (shellForms): no line is given then. */
export function pairLine(out: TicketOut, to: { name: string; kind: Where }): string | null {
  if (!inForm(ADE_URL, out.url) || !inForm(TICKET, out.ticket)) return null;
  const keys = pinnable(out.keys);
  const e2e = e2eFlag(out.e2e);
  return `${CLIENT} setup --url ${out.url} --ticket ${out.ticket}${keys.length ? ` --keys ${keys.join(",")}` : ""}${e2e ? ` --e2e ${e2e}` : ""}`
    + ` --name ${shellQuote(to.name)} --kind ${to.kind}${to.kind === "cloud" ? ` --ref ${CLOUD_REF}` : " --mirror"}`;
}

/** The line that pins passkeys in a linked session, run in that session's own window: its link's directory named by
 *  the link's name, as pairing made it (~/.proofpane-ade/NAME), and each fingerprint given to --add. Null when a
 *  fingerprint is not in the form the server writes it. */
export function pinLine(name: string, fingerprints: string[]): string | null {
  if (!fingerprints.every((f) => inForm(FINGERPRINT, f))) return null;
  return `${CLIENT} --dir "$HOME/.proofpane-ade/"${shellQuote(name)} pin${fingerprints.map((f) => ` --add ${f}`).join("")}`;
}

/** Why a name will not do for a new link, as the server's link.check_new_link and the live links' names have it, said
 *  before a ticket is made: letters, digits, spaces, dots, dashes and underscores, starting with a letter or a digit,
 *  at most 60; no two spaces together and none at its end; not owner or all; nothing shaped like a secret; and no live
 *  link's name, told apart as names are (nameKey). Null when it will do. */
export function linkNameProblem(name: string, live: Pick<LinkView, "name">[]): string | null {
  if (!name.trim()) return "Give the session a name, such as MacBook Pro.";
  if (!LINK_NAME.test(name)) {
    return "A name is letters, digits, spaces, dots, dashes and underscores, at most 60, starting with a letter or a digit.";
  }
  if (spacedBadly(name)) return "A name has no two spaces together and no space at its end.";
  if (["owner", "all"].includes(name.toLowerCase())) return "owner and all are not names a session can take.";
  const shape = SECRET_SHAPES.find(([, s]) => s.test(name));
  if (shape) return `The name is shaped like ${shape[0]}: choose another.`;
  const taken = live.find((l) => nameKey(l.name) === nameKey(name));
  if (taken) return `A linked session is named ${taken.name} already: choose another name, or link that one again from its card.`;
  return null;
}

/** The command a session runs to ask to be linked: the address the ADE signs for and the account's ask address, which
 *  is no secret. The session asks, prints its key's fingerprint and waits; it is linked only once the owner approves
 *  the request here and the approval's hold has ended. Null when the address or the ask address is not in the form
 *  the server writes it. */
export function askLines(url: string, address: string): string[] | null {
  if (!inForm(ADE_URL, url) || !inForm(ASK_ADDRESS, address)) return null;
  return [
    `${CLIENT} pair --url ${url} --ask ${address} --name NAME --kind cloud --ref REF`,
    "# --kind local on your own machine; REF as for a ticket",
    "# it asks, prints its key's fingerprint and waits up to 30 minutes: approve here the request with that fingerprint",
    "# the owner keys it pins come from the ADE as it is linked: compare the fingerprints it prints with Account's",
  ];
}

/** The session channel (the open edition's ade/link.py): the owner's coding sessions linked to the ADE, each with what
 *  it is doing, the newest thing it posted, the questions it asked and what it did. Everything a session posts is
 *  data, shown as text; the one link made from it is its own page on claude.ai. Read every ten seconds while open. */
/** A linked machine: its links (those its machine's own id puts together; a link whose list named no machine is one
 *  of its own) and the Claude Code sessions their lists name. */
export interface MachineGroup { key: string; links: LinkView[]; sessions: HistorySession[] }

/** The linked machines, in the order their links were made, each session of one shown once: where two of a machine's
 *  links list it, as the link that listed it last has it (another may be a link no longer run there). Sessions stay
 *  newest first. */
export function machines(links: LinkView[], sessions: HistorySession[]): MachineGroup[] {
  const keyOf = (l: LinkView) => (l.machine ? `machine:${l.machine}` : `link:${l.id}`);
  const out = new Map<string, MachineGroup>();
  for (const l of links) {
    const k = keyOf(l);
    const g = out.get(k);
    if (g) g.links.push(l);
    else out.set(k, { key: k, links: [l], sessions: [] });
  }
  const where = new Map(links.map((l) => [l.id, keyOf(l)]));
  const better = (a: HistorySession, b: HistorySession) => a.listed_at > b.listed_at;
  for (const s of sessions) {
    const g = out.get(where.get(s.link_id) ?? "");
    if (!g) continue;
    const i = g.sessions.findIndex((o) => o.id === s.id);
    if (i < 0) g.sessions.push(s);
    else if (better(s, g.sessions[i])) g.sessions[i] = s;
  }
  return [...out.values()];
}

/** Below this width Linked sessions fills the screen: a phone's. */
export const NARROW = "(max-width: 639px)";

/** The panel's own header buttons: an icon button, at least 44 pixels square on a narrow screen, where it is touched. */
const headerIcon = (narrow: boolean) => `icon-btn ${narrow ? "!h-11 !w-11" : ""}`;

/** Linked sessions. `linking` opens it on "Link a session" (the shell's "Link your Mac"): the session's name and where
 *  it runs asked first, as the panel's own button asks them; `reading` opens it on one of a machine's sessions. */
export function Channel({ onClose, reading: first = null, linking = false }: { onClose: () => void; reading?: HistorySession | null; linking?: boolean }) {
  // the linked sessions and each machine's sessions as the page's store keeps them, current through the shell's
  // summary (store.readSummary, which reads a list again only once its stamp moves): the panel reads neither on a
  // timer of its own, and Read again has the store read both at once
  const links = useStore((s) => s.links);
  const linksAt = useStore((s) => s.linksAt);
  const listed = useStore((s) => s.history);
  const refreshLinks = useStore((s) => s.refreshLinks);
  const refreshHistory = useStore((s) => s.refreshHistory);
  const setError = useStore((s) => s.setError);
  // linking a session: its name and where it runs, then the account's word, then the one line to run there
  const [pairing, setPairing] = useState<Pairing | null>(linking && !first ? { step: "name" } : null);
  // what just happened, said in the panel until it is put away: a link made, a hold cancelled
  const [notices, setNotices] = useState<{ id: number; text: string }[]>([]);
  const notice = useCallback((text: string) => setNotices((n) => [...n.slice(-2), { id: Date.now() + Math.random(), text }]), []);
  const [addressShown, setAddressShown] = useState(false);
  // what is typed to a session is never lost on the way out: Escape and a click outside leave the panel open while a
  // composer holds words (or the key came from a field that does), saying why; Close puts it away, and the words are
  // kept as a draft, there again when the panel opens
  const [held, setHeld] = useState(false);
  const accidental = useCallback(() => {
    if (composing() || holdsText(document.activeElement)) {
      setHeld(true);
      return;
    }
    onClose();
  }, [onClose]);
  useEffect(() => {
    if (!held) return;
    const t = setTimeout(() => setHeld(false), 6000);
    return () => clearTimeout(t);
  }, [held]);
  // a dialog, as Modal's useDialog keeps every dialog of the page: the keyboard stays in it, Escape closes it (one layer
  // at a time), and the keyboard goes back to what opened it. It starts on the panel's title (a static element at its
  // top, as for a dialog whose content is long), so its name is read and what is in it is read from the start
  const panel = useRef<HTMLDivElement>(null);
  useDialog(panel, accidental, "[data-dialog-start]");
  const narrow = useMedia(NARROW);
  useLinkDrafts();
  // the page cannot reach its server: said in the panel, whose dialog hides the shell's own line from a screen reader
  // (and covers it on a phone), once, as it happens; signing and Cancel wait meanwhile, each saying so
  const offline = useOffline();

  // a list the store has not read yet (the panel opened before the shell's first summary had it read): read once as the
  // panel opens, rather than waiting for the summary; never again from here but on Read again
  useEffect(() => {
    const st = useStore.getState();
    if (st.links === null) void st.refreshLinks();
    if (st.history === null) void st.refreshHistory();
  }, []);
  // where the account's newest moves left each machine session (one moved into the workspace is marked so in its row):
  // not in the store's summary, so read here, and only as something may have moved them: as the panel opens with a
  // machine's session listed, as the store reads the linked sessions or the machines' lists again (their stamps moved:
  // a move's hold ended, its machine took it, its list changed), while one is under way as the workspace's sessions
  // change (the moved one starting there), on Read again and back from a session's view. Never on a timer.
  const [moved, setMoved] = useState<ReadonlyMap<string, Moved>>(new Map());
  const loadMoves = useCallback(async () => {
    try { setMoved(movedSessions((await api<{ moves: LinkMove[] }>("/api/ade/link/moves")).moves ?? [])); } catch { /* none marked */ }
  }, []);
  const local = !!listed?.some((s) => s.kind === "local");
  useEffect(() => {
    if (local) void loadMoves();
  }, [local, loadMoves, links, listed]);
  const underWay = useMemo(() => [...moved.values()].includes("moving"), [moved]);
  // subscribed as the render that first shows a move under way is put on the page, not after it (a layout effect):
  // a change of the workspace's sessions that comes in between is not missed
  useLayoutEffect(() => {
    if (!underWay) return;
    return useStore.subscribe((st, was) => { if (st.records !== was.records) void loadMoves(); });
  }, [underWay, loadMoves]);

  // the account's word for a ticket, for a new link (named, and where it runs) or for one linked again
  const confirmTicket = async (to: { name: string; kind: Where; link?: LinkView }) => {
    try {
      const change: Change = { what: "ticket", label: to.link ? `Linking ${to.link.name} again` : `Linking “${to.name}”`, action: "Make the ticket", subject: `link-ticket:${to.link ? to.link.id : "new"}`, ways: await confirmWays() };
      setPairing({ step: "confirm", ...to, change });
    } catch (e) { setError(e); }
  };
  const ticket = (l?: LinkView) => {
    if (l) void confirmTicket({ name: l.name, kind: l.kind === "cloud" ? "cloud" : "local", link: l });
    else setPairing({ step: "name" });
  };
  const ticketConfirmed = async (confirmation?: string) => {
    if (pairing?.step !== "confirm") return;
    const { name, kind, link } = pairing;
    const out = await Api.linkTicket(link?.id, confirmation);
    setPairing({ step: "line", name, kind, link, out, line: pairLine(out, { name, kind }) });
  };
  // the server's clock less this browser's, as of the read that carried its time (when it went out, not when the panel
  // shows it, which can be minutes later): the countdowns run by the server's
  const skew = useMemo(() => skewOf(links?.server_time, readAtOf(linksAt)), [links, linksAt]);
  // the ticket's pairing once its session has paired (it waits on its hold): the ticket's box becomes its
  // card, and the requests leave it out; kept as last seen, so a pairing whose hold has just ended still shows
  const ticketId = pairing?.step === "line" ? pairing.out.ticket_id ?? null : null;
  const listedPairing = ticketId ? (links?.asks ?? []).find((a) => a.via === "ticket" && a.ticket_id === ticketId) ?? null : null;
  const [seenPairing, setSeenPairing] = useState<LinkAsk | null>(null);
  useEffect(() => { setSeenPairing(null); }, [ticketId]);
  useEffect(() => { if (listedPairing) setSeenPairing(listedPairing); }, [listedPairing]);
  const boxPairing = listedPairing ?? seenPairing;
  const listedAsks = (links?.asks ?? []).filter((a) => a.id !== boxPairing?.id);
  // what became of the ticket's pairing once it is no longer listed, by the server's own word (links.ended_pairings:
  // a ticket's pairings linked or cancelled in the last ten minutes, by their ticket's id), whether or not the page saw
  // it listed (one held for no time, or ended between two reads): the ticket's card goes, and the panel says how. The
  // links alone cannot tell it, since a link linked again with its own key has that key whether its re-pair was linked
  // or cancelled. One that failed stays on its card, saying why, until it is put away. Judged at each read of the
  // links, and at no other change
  useEffect(() => {
    if (!links || pairing?.step !== "line" || !ticketId || listedPairing) return;
    const ended = (links.ended_pairings ?? []).find((e) => e.ticket_id === ticketId);
    if (!ended) return;
    const name = seenPairing?.name ?? pairing.name;
    setPairing(null);
    if (pairing.link) {
      const linked = links.links.find((l) => l.id === pairing.link?.id);
      notice(ended.state === "cancelled" ? `The pairing of “${name}” was cancelled: it stays on its old key.`
        : linked && linked.fingerprint === pairing.link.fingerprint ? `“${name}” is linked again, with the same key.`
          : `“${name}” is linked again: its new key works now.`);
    } else {
      notice(ended.state === "cancelled" ? `The pairing of “${name}” was cancelled: it is not linked.` : `“${name}” is linked.`);
    }
  }, [links]); // eslint-disable-line react-hooks/exhaustive-deps
  // while the ticket is out and no session has paired with it, the links are read every 2 seconds, not at the
  // shell's own pace, so the box becomes the pairing's card as it pairs
  const ticketOut = pairing?.step === "line" && !!ticketId && !boxPairing && pairing.out.expires_at * 1000 > Date.now() + skew;
  useEffect(() => {
    if (!ticketOut) return;
    const t = setInterval(() => { void refreshLinks(); }, 2000);
    return () => clearInterval(t);
  }, [ticketOut, refreshLinks]);
  const live = new Set((links?.links ?? []).map((l) => l.id));
  const others = (links?.questions ?? []).filter((q) => !live.has(q.link_id));
  const [talking, setTalking] = useState<string | null>(null);
  const talk = links?.links.find((l) => l.id === talking) ?? null;
  const [reading, setReading] = useState<HistorySession | null>(first);
  // back from a session's view (where it may have moved): its row as it stands now
  const wasReading = useRef(!!first);
  useEffect(() => {
    if (wasReading.current && !reading && local) void loadMoves();
    wasReading.current = !!reading;
  }, [reading, local, loadMoves]);
  const groups = useMemo(() => machines(links?.links ?? [], listed ?? []), [links, listed]);

  // on a phone the panel is the whole screen, its header held at the top: Close first, where a thumb finds it, and the
  // panel's two actions on a row of their own; elsewhere it is a panel at the top right, over the page
  const frame = narrow
    ? "fixed inset-0 h-[100dvh] w-full"
    : `absolute right-2 top-12 max-h-[85vh] rounded-xl border border-line-strong ${talk || reading ? "h-[85vh] w-[min(760px,calc(100vw-1rem))]" : "w-[min(560px,calc(100vw-1rem))]"}`;
  const linkButton = (
    <button className={narrow ? "btn h-11 flex-1 justify-center" : "btn btn-sm"} onClick={() => ticket()}
      title="A one-time ticket a coding session links itself with"><Link2 size={13} /> Link a session</button>
  );
  const addressButton = (
    <button className={narrow ? "btn h-11 flex-1 justify-center" : "btn btn-sm"} aria-expanded={addressShown}
      onClick={() => setAddressShown(!addressShown)}
      title="The address a session asks to be linked at, for you to approve here"><AtSign size={13} /> Ask address</button>
  );
  const close = <button className={headerIcon(narrow)} onClick={onClose} title="Close (Esc)" aria-label="Close Linked sessions" data-modal-close><X size={narrow ? 18 : 15} /></button>;
  // Read again: the store reads the links and each machine's sessions at once, whatever their stamps; the moves are
  // read again as the lists it reads come (above)
  const readAgain = () => { void refreshLinks(); void refreshHistory(); };
  const again = <button className={headerIcon(narrow)} onClick={readAgain} title="Read again" aria-label="Read again"><RefreshCw size={14} /></button>;
  const title = (
    <h2 tabIndex={-1} data-dialog-start className="flex min-w-0 items-center gap-2 rounded text-[13px] font-semibold outline-none focus-visible:ring-1 focus-visible:ring-accent">
      <Radio size={15} className="flex-none text-accent" aria-hidden /> Linked sessions
    </h2>
  );

  // the drafts to links that went (revoked here, or on another device meanwhile): their words, never sent, shown
  const gone = links ? draftsToGone(live) : [];

  return (
    <div className="fixed inset-0 z-40" onMouseDown={accidental}>
      <div ref={panel} role="dialog" aria-modal="true" aria-label="Linked sessions" tabIndex={-1} data-narrow={narrow || undefined}
        className={`${frame} flex flex-col overflow-hidden bg-panel shadow-2xl shadow-black/60 outline-none`} onMouseDown={(e) => e.stopPropagation()}>
        <div role="status" aria-live="polite" className={held ? "flex-none border-b border-wait/50 bg-wait/[0.06] px-4 py-1.5 text-[12px] text-wait" : "sr-only"}>
          {held ? "Linked sessions stays open while you have words in it: send them, or Cancel them. Close puts the panel away and keeps them for when it opens again." : ""}
        </div>
        <div role="status" aria-live="polite" className={offline ? "flex flex-none items-center gap-2 border-b border-wait/30 bg-wait/[0.07] px-4 py-1.5 text-[12px] text-fg" : "sr-only"}>
          {offline && <><WifiOff size={13} className="flex-none text-wait" aria-hidden />{PANEL_OFFLINE}</>}
        </div>
        {talk ? <Conversation key={talk.id} l={talk} onBack={() => setTalking(null)} onClose={onClose} narrow={narrow} />
          : reading ? <SessionConversation key={`${reading.link_id}/${reading.id}`} s={reading} onBack={() => setReading(null)} onClose={onClose} narrow={narrow}
              onTalk={(id) => { setReading(null); setTalking(id); }} /> : <>
        {narrow ? (
          <div className="sticky top-0 z-10 flex-none border-b border-line bg-panel">
            <div className="flex h-12 items-center gap-1 px-1">{close}<div className="min-w-0 flex-1">{title}</div>{again}</div>
            <div className="flex gap-2 px-3 pb-2">{linkButton}{addressButton}</div>
          </div>
        ) : (
          <div className="sticky top-0 z-10 flex h-11 flex-none items-center justify-between gap-2 border-b border-line bg-panel px-4">
            {title}
            <span className="flex items-center gap-1">{linkButton}{addressButton}{again}{close}</span>
          </div>
        )}
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
          {gone.map(([key, d]) => <GoneDraft key={key} draftKey={key} d={d} />)}
          {/* one polite live region from the start, so each notice put in it is said */}
          <div role="status" aria-live="polite" className="space-y-2">
            {notices.map((n) => (
              <div key={n.id} className="flex items-start gap-2 rounded-lg border border-done/50 bg-done/[0.05] px-3 py-2 text-[12.5px]">
                <Check size={14} className="mt-0.5 flex-none text-done" />
                <span className="min-w-0 flex-1">{n.text}</span>
                <button className="icon-btn !h-6 !w-6 flex-none" title="Put away" aria-label="Put away"
                  onClick={() => setNotices((all) => all.filter((x) => x.id !== n.id))}><X size={13} /></button>
              </div>
            ))}
          </div>
          {pairing?.step === "name" && (
            <PairStart live={links?.links ?? []} onGo={(name, kind) => void confirmTicket({ name, kind })} onCancel={() => setPairing(null)} />
          )}
          {pairing?.step === "confirm" && (
            <NextStep key={pairing.change.label} say={`${pairing.change.label}: confirm it, and the ticket is made.`}>
              <ConfirmIt change={pairing.change} onConfirmed={ticketConfirmed} onCancel={() => setPairing(null)} />
            </NextStep>
          )}
          {pairing?.step === "line" && (boxPairing
            ? <PairingCard a={boxPairing} skew={skew} onNotice={notice} onCancelled={() => setPairing(null)} onPut={() => setPairing(null)} />
            : <PairLine p={pairing} hold={links?.connect_hold} onPut={() => setPairing(null)}
                onAgain={() => void confirmTicket({ name: pairing.name, kind: pairing.kind, link: pairing.link })} />)}
          {addressShown && <AskAddress onClose={() => setAddressShown(false)} />}
          {links && listedAsks.length > 0 && (
            <section aria-label="Asking to be linked" className="space-y-2">
              {listedAsks.map((a) => (
                <AskCard key={a.id} a={a} links={links.links} hold={links.connect_hold} onNotice={notice} skew={skew}
                  twins={(links.asks ?? []).filter((b) => (b.state === "pending" || b.state === "held") && nameKey(b.name) === nameKey(a.name)).length} />
              ))}
            </section>
          )}
          {links && (links.held_moves ?? []).length > 0 && (
            <section aria-label="Moves that wait" className="space-y-2">
              {(links.held_moves ?? []).map((m) => <HeldMoveCard key={m.hold_id} m={m} onNotice={notice} />)}
            </section>
          )}
          {links === null && <div className="flex justify-center py-8 text-muted"><Loader2 size={16} className="animate-spin" /></div>}
          {links && links.links.length === 0 && !pairing && (
            <div className="px-2 py-8 text-center text-[12.5px] text-muted">
              <div>
                No session is linked yet. Link one: you name it, say whether it is on this Mac or on claude.ai, and get one
                line to run there. A linked session shows here what it is doing and what it asks you, and takes what you sign.
              </div>
              <button className="btn btn-sm btn-primary mt-3" onClick={() => ticket()}><Link2 size={13} /> Link your first session</button>
            </div>
          )}
          {links && groups.map((g) => {
            const cloud = g.links.every((l) => l.kind === "cloud");
            return (
              <section key={g.key} aria-label={`${cloud ? "In the cloud" : "A machine"}: ${g.links.map((l) => l.name).join(", ")}`} className="space-y-2 rounded-xl border border-line p-2">
                <div className="flex items-center gap-1.5 px-1 text-[11.5px] font-medium text-fg-dim">
                  {cloud ? <Cloud size={12} /> : <Laptop size={12} />}
                  <span className="truncate">{cloud ? "In the cloud" : "A machine"} · {g.links.map((l) => l.name).join(", ")}</span>
                </div>
                {g.links.map((l) => (
                  <LinkCard key={l.id} l={l} questions={links.questions.filter((q) => q.link_id === l.id)}
                    waitings={(links.waitings ?? []).filter((w) => w.link_id === l.id)} onTicket={() => void ticket(l)}
                    onTalk={() => setTalking(l.id)} sessionsBelow={g.sessions.some((s) => s.link_id === l.id && (s.notes ?? 0) > 0)} />
                ))}
                {listed === null ? <div className="flex justify-center text-muted"><Loader2 size={14} className="animate-spin" /></div>
                  : <MachineSessions sessions={g.sessions} onRead={setReading} moved={moved} link={g.links[0]?.name} cloud={cloud} />}
              </section>
            );
          })}
          {others.length > 0 && (
            <div className="rounded-lg border border-line p-3">
              <div className="mb-1 text-[11.5px] text-muted">Questions from sessions no longer linked</div>
              {others.map((q) => <Question key={q.seq} q={q} />)}
            </div>
          )}
        </div>
        </>}
      </div>
    </div>
  );
}

/** Linking a session, step by step: its name and where it runs (a new link); the account's word for the ticket; then
 *  the line to run there, until the link appears (`before`: the live links when the ticket was made; `line` null when
 *  the ticket's answer is not in the form the server writes it, and no line is given). */
type Pairing =
  | { step: "name" }
  | { step: "confirm"; name: string; kind: Where; link?: LinkView; change: Change }
  | { step: "line"; name: string; kind: Where; link?: LinkView; out: TicketOut; line: string | null };

/** Why a line to run is not given: a value the server sent for it is not in the form the server writes it. */
export const NO_LINE = "No line to run is given: the server's answer is not in the form the ADE writes it, and a line made "
  + "from it could run a command the server chose. Check that this page talks to your own ADE.";

/** The time now, by this browser's clock, again every `every` milliseconds while it is shown. */
export function useNow(every = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), every);
    return () => clearInterval(t);
  }, [every]);
  return now;
}

/** Seconds as a clock reads a span: "0:42", "12:05", "1:02:03". */
export function span(seconds: number): string {
  const s = Math.max(0, Math.ceil(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${r}` : `${m}:${r}`;
}

/** The first step of linking a session: what to call it, and whether it is on this Mac or on claude.ai, asked before
 *  any ticket is made, so the line that follows has nothing left to fill in. The name is held to the rules the server
 *  holds a new link's name to (linkNameProblem). */
function PairStart({ live, onGo, onCancel }: { live: LinkView[]; onGo: (name: string, kind: Where) => void; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<Where>("local");
  const [problem, setProblem] = useState("");
  const go = (e: FormEvent) => {
    e.preventDefault();
    const p = linkNameProblem(name, live);
    setProblem(p ?? "");
    if (!p) onGo(name, kind);
  };
  const radio = "flex cursor-pointer items-start gap-2 rounded-md border border-line px-2.5 py-2 has-[:checked]:border-accent/60 has-[:checked]:bg-accent/[0.06]";
  return (
    <form aria-label="Link a session" onSubmit={go} className="rounded-lg border border-accent/40 bg-accent/[0.05] p-3 text-[12.5px]">
      <div className="mb-2 font-medium">Link a session</div>
      <label className="block text-[12px] text-fg-dim" htmlFor="pair-name">What to call it</label>
      <input id="pair-name" className="input mt-1 h-9 w-full" autoFocus autoComplete="off" spellCheck={false} placeholder="MacBook Pro"
        value={name} onChange={(e) => { setName(e.target.value); setProblem(""); }} aria-invalid={!!problem || undefined}
        aria-describedby={problem ? "pair-name-problem" : undefined} />
      {problem && <div id="pair-name-problem" role="alert" className="mt-1 text-[12px] text-fail">{problem}</div>}
      <fieldset className="mt-2.5">
        <legend className="text-[12px] text-fg-dim">Where it runs</legend>
        <div className="mt-1 grid gap-1.5 sm:grid-cols-2">
          <label className={radio}>
            <input type="radio" name="pair-where" className="mt-0.5" checked={kind === "local"} onChange={() => setKind("local")} />
            <span><span className="font-medium">This Mac</span><span className="block text-[11.5px] text-muted">Claude Code on your own machine</span></span>
          </label>
          <label className={radio}>
            <input type="radio" name="pair-where" className="mt-0.5" checked={kind === "cloud"} onChange={() => setKind("cloud")} />
            <span><span className="font-medium">claude.ai</span><span className="block text-[11.5px] text-muted">A Claude Code session in the cloud</span></span>
          </label>
        </div>
      </fieldset>
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        <button type="submit" className="btn btn-sm btn-primary">Continue</button>
        <button type="button" className="btn btn-sm" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

/** The last step of linking a session: the one line to run where it runs, Copy, how long its ticket works (and a new
 *  one once it has expired), and what it pins as it links. A new link's card goes once the link is there. `hold`: how
 *  long a pairing waits now, as the latest read of the links says it (high security, turned on since the ticket was
 *  made, lengthens it); the ticket's own word until there is one. */
function PairLine({ p, hold, onPut, onAgain }: { p: Extract<Pairing, { step: "line" }>; hold?: number; onPut: () => void; onAgain: () => void }) {
  const now = useNow();
  const [copied, setCopied] = useState(false);
  const left = p.out.expires_at - now / 1000;
  const keys = pinnable(p.out.keys).length;
  const what = p.out.high_security ? "hardware key" : "passkey";
  // setup puts the hooks in the settings of the folder it runs in, so a local line says where to run it;
  // inside a Claude Code session it outlives the Bash tool's limit while it waits out the hold, so in the background
  const where = p.kind === "cloud" ? "in the claude.ai session" : "on this Mac, in a terminal in the folder the session runs in (in the Claude Code session itself, in the background: it waits out the hold)";
  // the ticket made: the keyboard goes to Copy, the step's own button
  const copyButton = useRef<HTMLButtonElement>(null);
  useEffect(() => { copyButton.current?.focus(); }, []);
  const line = p.line;
  if (line === null) {
    return (
      <section aria-label={`Linking ${p.name}`} className="rounded-lg border border-fail/40 bg-fail/[0.05] p-3 text-[12.5px]">
        <div className="flex items-start justify-between gap-2">
          <div role="alert" className="text-fail">{NO_LINE}</div>
          <button className="icon-btn !h-6 !w-6 flex-none" title="Put away" aria-label="Put away" onClick={onPut}><X size={13} /></button>
        </div>
      </section>
    );
  }
  const copy = () => { void navigator.clipboard?.writeText(line); setCopied(true); setTimeout(() => setCopied(false), 1500); };
  return (
    <section aria-label={`Linking ${p.name}`} className="rounded-lg border border-accent/40 bg-accent/[0.05] p-3 text-[12.5px]">
      <div className="flex items-start justify-between gap-2">
        <div className="font-medium">
          {p.link ? <>To link <Name name={p.name} /> again, run this line {where}:</> : <>To link &ldquo;<Name name={p.name} />&rdquo;, run this line {where}:</>}
        </div>
        <button className="icon-btn !h-6 !w-6 flex-none" title="Put away" aria-label="Put away" onClick={onPut}><X size={13} /></button>
      </div>
      <pre aria-label="The line to run" className="mt-1.5 overflow-x-auto whitespace-pre-wrap break-all rounded bg-bg px-2.5 py-2 font-mono text-[11.5px] text-fg">{line}</pre>
      <div className="mt-1.5 flex flex-wrap items-center gap-2">
        <button ref={copyButton} className="btn btn-sm btn-primary" onClick={copy}>{copied ? <Check size={12} /> : <Copy size={12} />} {copied ? "Copied" : "Copy the line"}</button>
        {left > 0
          ? <span className="text-[12px] text-fg-dim" title={full(p.out.expires_at)}>Waiting for the session to pair: the ticket works once, until {clock(p.out.expires_at)}: {span(left)} left.</span>
          : <>
              <span className="text-[12px] text-wait" title={full(p.out.expires_at)}>The ticket expired at {clock(p.out.expires_at)}.</span>
              <button className="btn btn-sm" onClick={onAgain}>Make a new ticket</button>
            </>}
      </div>
      <div className="mt-1.5 text-[11.5px] text-muted">
        {keys ? `It pins your ${keys === 1 ? what : `${keys} ${what}s`} as it links, so it acts only on what ${keys === 1 ? "that key signs" : "they sign"}.`
          : "You have no passkey yet: add one in Account, then give the session its pin line."}
        {pairWait(hold ?? p.out.pair_hold, !!p.link)}
        {p.link ? " Its cursor and checkpoint stay, and its old key works until then."
          : ` This card becomes the pairing's as it pairs, and goes once ${p.name} is linked.`}
      </div>
      {p.kind === "cloud" && (
        <div className="mt-1 text-[11.5px] text-muted">The line runs the link client at ~/.proofpane-ade/ade_link.py: in a claude.ai session, put it there first. A session on claude.ai runs no hook of the ADE's: the link client keeps its link heard with a heartbeat at the link's interval while the session runs, which ends when the session ends; after its container restarts, the session's next post through the client starts it again.</div>
      )}
      {p.kind === "local" && (
        <div className="mt-1 text-[11.5px] text-muted">It also puts the session&apos;s hooks in the settings file Claude Code reads for that folder (at the repository&apos;s root, or the main checkout&apos;s for a worktree), and says which file and what it changed. --mirror adds the mirror too: each turn&apos;s words and each tool call&apos;s line come to this page, sealed once Encryption is on; take it out of the line to link without them.</div>
      )}
      <Said text={`The ticket is made. Copy the line and run it ${where}; the ticket works once, until ${clock(p.out.expires_at)}.`} />
    </section>
  );
}

/** What a step says to a screen reader as it comes, politely (after what is being read): a live region on the page
 *  from the start, its words put in once it is there, so they are announced. */
export function Said({ text }: { text: string }) {
  const [said, setSaid] = useState("");
  useEffect(() => { setSaid(text); }, [text]);
  return <div role="status" aria-live="polite" className="sr-only">{said}</div>;
}

/** What Linked sessions says while the page cannot reach its server. */
export const PANEL_OFFLINE = "Offline — signing and Cancel wait until this page reaches the server again.";
/** Why signing waits while the page cannot reach its server (the store's offline). */
export const SIGN_OFFLINE = "Offline: signing waits until this page reaches the server again.";
/** Why a hold's Cancel waits then, and what goes on meanwhile. */
export const CANCEL_OFFLINE = "Offline: Cancel waits until this page reaches the server again, and the hold runs on meanwhile.";

/** A button whose action needs the server, while the page cannot reach it (useOffline): marked unavailable with
 *  aria-disabled, not disabled, so it stays where the keyboard reaches it (a step that moved the keyboard to it does not
 *  throw it out) and is read as unavailable with `why`, its description, which is shown under it too. Its action
 *  checks `off` and does nothing meanwhile. */
export function useNeedsServer(why: string): { off: boolean; attrs: { "aria-disabled"?: true; "aria-describedby"?: string }; note: ReactNode } {
  const off = useOffline();
  const id = useId();
  return {
    off,
    attrs: off ? { "aria-disabled": true, "aria-describedby": id } : {},
    note: off ? <div id={id} className="mt-1 text-[11px] text-wait">{why}</div> : null,
  };
}

/** The look of a button marked unavailable that way, as a disabled one looks. */
export const UNAVAILABLE = "aria-disabled:cursor-not-allowed aria-disabled:opacity-50";

/** A move's ask where the account keeps its work elsewhere than the workspace this deployment runs for it. */
const MOVE_ELSEWHERE = "A move goes into the workspace this deployment runs for you, which it starts only where you keep "
  + "your work in it: you keep it elsewhere now.";

/** The next step of a two-step action, as it appears: the keyboard goes to its first field or button (its main one),
 *  and `say` is said politely. */
export function NextStep({ say, children }: { say: string; children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = box.current;
    if (el) focusables(el)[0]?.focus();
  }, []);
  return <div ref={box}>{children}<Said text={say} /></div>;
}

/** What was typed to a link that went while it was typed (revoked, here or on another device): said so, its words kept
 *  to read and copy, never sent; Discard lets them go. */
function GoneDraft({ draftKey, d }: { draftKey: string; d: LinkDraft }) {
  const [copied, setCopied] = useState(false);
  const what = draftKey.split("\n")[1] === "answer" ? "Your answer" : "Your instruction";
  return (
    <div role="alert" className="rounded-lg border border-wait/50 bg-wait/[0.05] p-3 text-[12.5px]">
      <div><Name name={d.name} className="font-medium" /> is no longer linked: it was revoked, here or on another device. {what} to it was not sent; here it is, to copy.</div>
      <pre className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-bg px-2.5 py-2 font-mono text-[12px] text-fg">{d.text}</pre>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        <button className="btn btn-sm" onClick={() => { void navigator.clipboard?.writeText(d.text); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
          {copied ? <Check size={12} /> : <Copy size={12} />} {copied ? "Copied" : "Copy the words"}
        </button>
        <button className="btn btn-sm" onClick={() => dropDraft(draftKey)}>Discard</button>
      </div>
    </div>
  );
}

/** What the ticket's box says of the wait after a session pairs (`hold`, the ticket's pair_hold; `again`, a ticket that
 *  links a link again), with a space before it; nothing where the server said no length. */
export function pairWait(hold: number | undefined, again: boolean): string {
  if (typeof hold !== "number") return "";
  const what = again ? "it moves to the new key" : "it is linked";
  return hold > 0 ? ` Once the session pairs, ${what} ${waitWords(hold)} later, unless you cancel it here.`
    : ` Once the session pairs, ${what} at once (this deployment holds no pairing).`;
}

/** A hold's length in words: "3 minutes", "90 seconds". */
export const waitWords = (s: number) => (s >= 60 && s % 60 === 0 ? `${s / 60} minute${s === 60 ? "" : "s"}` : `${s} second${s === 1 ? "" : "s"}`);

/** When a hold ends: its time, and its day too when that is not within the next twelve hours. */
export const holdEnds = (at: number | null | undefined) =>
  (at && Math.abs(at * 1000 - Date.now()) < 12 * 3600 * 1000 ? clock(at) : dayTime(at));

/** When a hold takes effect, from `now` (this browser's milliseconds): "Takes effect in 0:42 (02:59)", the time left
 *  as a clock reads it and the time it ends; once that has come, that it takes effect now. */
export function untilWords(at: number | null | undefined, now = Date.now()): string {
  if (!at) return "";
  const left = at - now / 1000;
  return left > 0 ? `Takes effect in ${span(left)} (${holdEnds(at)})` : `Taking effect now (${holdEnds(at)})`;
}

/** A hold's untilWords, counting down by the second; never in a live region, so a reader is not told every second. */
function Countdown({ at }: { at: number | null | undefined }) {
  const now = useNow();
  return <span title={full(at)}>{untilWords(at, now)}</span>;
}

/** The server's clock less this browser's, in milliseconds, as of the read that carried the server's time
 *  (LinkSessions.server_time, read at `readAt`): what a countdown adds to this browser's clock to run by the server's,
 *  whose time ends a hold. 0 without one. */
export function skewOf(serverTime: number | null | undefined, readAt = Date.now()): number {
  return serverTime ? serverTime * 1000 - readAt : 0;
}

/** When a read was made, by this browser's clock as it reads now (`wall`), from the time the store kept for it by the
 *  page's monotonic clock (store.linksAt, by performance.now, read now as `mono`): what skewOf takes as `readAt`, so a
 *  panel opened long after the read counts from the read, and a change to this browser's clock in between does not
 *  move it. None kept (links given the store some other way): now, as before. */
export function readAtOf(at: number | null | undefined, wall = Date.now(), mono = performance.now()): number {
  return at == null ? wall : wall - (mono - at);
}

/** A pairing's countdown, by the server's clock (this browser's `now`, in milliseconds, corrected by `skew`): "linked
 *  in 2:41, at 14:03, unless you cancel it." (`verb` "moved" for a link moving to a new key) while its hold runs;
 *  "Linking…" once its time has come, until a read shows the link. */
export function linkingWords(at: number | null | undefined, now: number, skew = 0, verb = "linked"): string {
  if (!at) return "";
  const left = at - (now + skew) / 1000;
  return left > 0 ? `${verb} in ${span(left)}, at ${holdEnds(at)}, unless you cancel it.` : "Linking…";
}

/** An approved request's countdown, by the server's clock as linkingWords counts: untilWords while its hold runs,
 *  "Linking…" once its time has come. */
export function approvedWords(at: number | null | undefined, now: number, skew = 0): string {
  if (!at) return "";
  return at - (now + skew) / 1000 > 0 ? untilWords(at, now + skew) : "Linking…";
}

/** A countdown to a link, by the second and by the server's clock; never in a live region either. */
function LinkCountdown({ at, words }: { at: number | null | undefined; words: (now: number) => string }) {
  useNow();
  return <span title={full(at)}>{words(Date.now())}</span>;
}

/** How many devices a hold's notice was sent toward (how many it reached is not known), as a hold's card says it; -1:
 *  it could not be sent (the account's devices could not be read, or its words made, or its thread started). */
export const toldWords = (told: number) => (told > 0
  ? `Its notice was sent toward ${told} ${told === 1 ? "device" : "devices"} with notifications on.`
  : told < 0 ? "Its notice could not be sent. Only this page shows it."
    : "No device of this account has notifications on: only this page shows it.");

/** The account's ask address, the command a session asks with, and Replace, which refuses what waits on the old one. */
export function AskAddress({ onClose }: { onClose: () => void }) {
  const setError = useStore((s) => s.setError);
  const refresh = useStore((s) => s.refreshLinks);
  const [got, setGot] = useState<{ address: string; url: string } | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    let live = true;
    Api.askAddress().then((a) => { if (live) setGot(a); }).catch((e) => setError(e));
    return () => { live = false; };
  }, [setError]);
  const lines = got ? askLines(got.url, got.address) : null;
  return (
    <div className="rounded-lg border border-accent/40 bg-accent/[0.05] p-3">
      <div className="mb-1.5 flex items-center justify-between gap-2 text-[12px] text-fg-dim">
        <span>A session asks to be linked at this address, and you approve it here. It names your account and grants nothing.</span>
        <span className="flex flex-none gap-1">
          {lines && (
            <button className="icon-btn !h-6 !w-6" title="Copy the command" onClick={() => { void navigator.clipboard?.writeText(lines.join("\n")); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
              {copied ? <Check size={13} /> : <Copy size={13} />}
            </button>
          )}
          <button className="icon-btn !h-6 !w-6" title="Put away" onClick={onClose}><X size={13} /></button>
        </span>
      </div>
      {!got ? <div className="flex justify-center text-muted"><Loader2 size={14} className="animate-spin" /></div>
        : !lines ? <div role="alert" className="text-[12px] text-fail">{NO_LINE}</div> : (
        <>
          <div className="font-mono text-[12.5px] text-fg" aria-label="Your ask address">{got.address}</div>
          <pre className="mt-1.5 overflow-x-auto whitespace-pre-wrap break-all rounded bg-bg px-2.5 py-2 font-mono text-[11px] text-fg">{lines.join("\n")}</pre>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <Confirm label="Replace" again="Click again: the requests waiting on it are refused"
              onConfirm={async () => { try { setGot(await Api.newAskAddress()); await refresh(); } catch (e) { setError(e); } }} />
            <span className="text-[11px] text-muted">Whoever has it can only make requests you refuse; a new one ends theirs.</span>
          </div>
        </>
      )}
    </div>
  );
}

/** One session's request to be linked: what it calls itself (its own word), its key's fingerprint, to compare with the
 *  one the session printed, and the address it asked from and when; Approve, with the account's own word as a ticket
 *  takes, or Refuse. A cloud session's request under the name of a live cloud link shows that link beside it, and
 *  Approve and replace (a second click, then the account's word) revokes that link when the hold ends. Approved, its
 *  hold: when the link is made, counted down by the server's clock (`skew`, skewOf), and Cancel until then. `twins`:
 *  the requests waiting under the same name; `hold`: the seconds an approval waits, as the server says. A session that
 *  paired by a ticket is shown as its pairing (PairingCard). */
export function AskCard({ a, links, twins, hold, onNotice, skew = 0 }: { a: LinkAsk; links: LinkView[]; twins: number; hold?: number; onNotice?: (text: string) => void; skew?: number }) {
  if (a.via === "ticket") return <PairingCard a={a} skew={skew} onNotice={onNotice} />;
  return <AskedCard a={a} links={links} twins={twins} hold={hold} onNotice={onNotice} skew={skew} />;
}

/** A session that paired by one of the account's tickets: linked, or moved to its new key for a ticket
 *  that links a link again, when its hold ends, counted down by the server's clock (`skew`, skewOf); its key's
 *  fingerprint, to compare with the one the session printed, the address it came from, how many devices its notice
 *  went toward, and Cancel until then, which takes this sign-in alone; one whose hold ended without the link, why.
 *  It is the ticket's own box once the session pairs, and the same card among the requests. `onPut`, the box's: Put
 *  away, which leaves a pairing that waits to the requests, and one that failed to the requests while they show it. */
export function PairingCard({ a, skew = 0, onNotice, onCancelled, onPut }: { a: LinkAsk; skew?: number; onNotice?: (text: string) => void; onCancelled?: () => void; onPut?: () => void }) {
  const refresh = useStore((s) => s.refreshLinks);
  const setError = useStore((s) => s.setError);
  const cancelling = useNeedsServer(CANCEL_OFFLINE);
  // Cancel shows itself taken at once, and comes back with the reason if the server does not take it
  const cancelled = useAct(holdKey(a.hold_id ?? a.id), setError);
  const said = <Name name={a.name} className="font-medium" />;
  const old = a.replaces ?? null;
  const putAway = onPut && (
    <button className="icon-btn !h-6 !w-6 flex-none" title="Put away" aria-label="Put away" onClick={onPut}><X size={13} /></button>
  );
  if (a.state === "failed") {
    return (
      <div role="group" aria-label={`Not linked: ${a.name}`} className="flex items-start gap-2 rounded-lg border border-fail/50 bg-fail/[0.04] p-3 text-[12.5px]">
        <span className="min-w-0 flex-1">
          &ldquo;{said}&rdquo; ({a.kind}) {old ? "was to move to a new key by your ticket, and could not" : "paired by your ticket, and the link could not be made"}: {visible(a.failed ?? "")}
        </span>
        {putAway}
      </div>
    );
  }
  const cancel = () => void cancelled.act("Cancelled", () => Api.cancelHold(a.hold_id ?? ""), async () => {
    // a re-pair cancelled leaves its link as it was, on its old key: only a new link's pairing is "not linked"
    onNotice?.(old ? `Cancelled — “${a.name}” stays on its old key.` : `Cancelled — “${a.name}” won't be linked.`);
    onCancelled?.();
    await refresh();
  });
  return (
    <div role="group" aria-label={`Pairing: ${a.name}`} className="rounded-lg border border-wait/50 bg-wait/[0.05] p-3 text-[12.5px]">
      <div className="flex items-start gap-2">
        <Hourglass size={14} className="mt-0.5 flex-none text-wait" />
        <div className="min-w-0 flex-1">
          <div>
            &ldquo;{said}&rdquo; ({a.kind}) {old ? "moves to a new key" : "pairs"} by your ticket:{" "}
            <span className="font-medium text-wait"><LinkCountdown at={a.takes_effect_at} words={(now) => linkingWords(a.takes_effect_at, now, skew, old ? "moved" : "linked")} /></span>
          </div>
          {old && <div className="mt-0.5 font-mono text-[11px] text-muted">old key {old.fingerprint}</div>}
          <div className="mt-0.5 font-mono text-[11px]">{old ? "new key" : "key"} {a.fingerprint}</div>
          <div className="mt-0.5 text-[11.5px] text-muted" title={full(isoOf(a.created_at))}>
            Compare it with the fingerprint the session printed: a session the ticket was refused to prints its own,
            and one that differs means this pairing is not that session&rsquo;s. From {a.asked_from ?? "an address the ADE could not read"}, {ago(isoOf(a.created_at))}.
          </div>
          {a.told != null && <div className="mt-0.5 text-[11px] text-muted">{toldWords(a.told)}</div>}
          {cancelling.note}
          {cancelled.taken && <TakenLine said={old ? `${cancelled.taken}: it stays on its old key` : `${cancelled.taken}: it will not be linked`} sure={cancelled.sure} />}
          <CameBack why={cancelled.failed} />
        </div>
        <button className={`btn btn-sm btn-danger flex-none ${UNAVAILABLE}`} disabled={!a.hold_id || !!cancelled.taken} {...cancelling.attrs}
          onClick={() => { if (!cancelling.off) cancel(); }}>Cancel</button>
        {putAway}
      </div>
    </div>
  );
}

/** A request to an ask address (AskCard's, for a request no ticket made). */
function AskedCard({ a, links, twins, hold, onNotice, skew }: { a: LinkAsk; links: LinkView[]; twins: number; hold?: number; onNotice?: (text: string) => void; skew: number }) {
  const refresh = useStore((s) => s.refreshLinks);
  const setError = useStore((s) => s.setError);
  // the link to replace is the one shown when the owner clicked, not whatever a later read shows
  const [approving, setApproving] = useState<(Change & { replace?: string }) | null>(null);
  // Refuse, and a held request's Cancel, show themselves taken at once, and come back with the reason if the server does
  // not take them
  const refusal = useAct(`refuse-ask:${a.id}`, setError);
  const cancelled = useAct(holdKey(a.hold_id ?? a.id), setError);
  // approved here: once the read shows its hold, the keyboard goes to its card, and what follows is said
  const [approvedSaid, setApprovedSaid] = useState("");
  const heldBox = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (a.state === "held" && approvedSaid) heldBox.current?.focus();
  }, [a.state, approvedSaid]);
  const said = <Name name={a.name} className="font-medium" />;
  const old = a.replaces ?? null;
  const cancelling = useNeedsServer(CANCEL_OFFLINE);
  if (a.state === "held") {
    const at = a.takes_effect_at ?? undefined;
    return (
      <div ref={heldBox} tabIndex={-1} role="group" aria-label={`Approved: ${a.name}`} className="rounded-lg border border-wait/50 bg-wait/[0.05] p-3 text-[12.5px] outline-none focus-visible:ring-1 focus-visible:ring-accent">
        <Said text={approvedSaid} />
        <div className="flex items-start gap-2">
          <Hourglass size={14} className="mt-0.5 flex-none text-wait" />
          <div className="min-w-0 flex-1">
            <div>Approved: &ldquo;{said}&rdquo; ({a.kind}) {old ? "replaces the link of that name" : "is linked"} unless you cancel it first.</div>
            <div className="mt-0.5 font-medium text-wait"><LinkCountdown at={at} words={(now) => approvedWords(at, now, skew)} /></div>
            {old && (
              <>
                <div className="mt-0.5 text-[11px] text-muted">
                  The link it replaces was linked {ago(isoOf(old.created_at))}; {old.revoked_at ? "it was revoked since." : "it stops working then."}
                </div>
                <div className="mt-0.5 font-mono text-[11px] text-muted">old key {old.fingerprint}</div>
              </>
            )}
            <div className="mt-0.5 font-mono text-[11px] text-muted">{old ? "new key" : "key"} {a.fingerprint}</div>
            {a.told != null && <div className="mt-0.5 text-[11px] text-muted">{toldWords(a.told)}</div>}
            {cancelling.note}
            {cancelled.taken && <TakenLine said={`${cancelled.taken}: it will not be linked`} sure={cancelled.sure} />}
            <CameBack why={cancelled.failed} />
          </div>
          <button className={`btn btn-sm btn-danger flex-none ${UNAVAILABLE}`} disabled={!a.hold_id || !!cancelled.taken} {...cancelling.attrs}
            onClick={() => {
              if (cancelling.off) return;
              void cancelled.act("Cancelled", () => Api.cancelHold(a.hold_id ?? ""), async () => { onNotice?.(`Cancelled — “${a.name}” won't be linked.`); await refresh(); });
            }}>Cancel</button>
        </div>
      </div>
    );
  }
  if (a.state === "failed") {
    return (
      <div className="rounded-lg border border-fail/50 bg-fail/[0.04] p-3 text-[12.5px]">
        &ldquo;{said}&rdquo; ({a.kind}) was approved{old ? " to replace the link of that name" : ""}, and the link could not be made: {visible(a.failed ?? "")}
      </div>
    );
  }
  const ref = a.session_ref && a.session_ref.startsWith("https://claude.ai/") ? a.session_ref : null;
  // a live link of its name that it may not replace: the server names the one it may (old)
  const holder = old ? undefined : links.find((l) => nameKey(l.name) === nameKey(a.name));
  const seen = old?.last_seen_at ? ago(isoOf(old.last_seen_at)) : "never";
  return (
    <div className="rounded-lg border border-wait/50 bg-wait/[0.05] p-3 text-[12.5px]">
      <div>A session that calls itself &ldquo;{said}&rdquo; asks to be linked <span className="text-muted">({a.kind})</span></div>
      <div className="mt-1 font-mono text-[11.5px]" title="Compare it with the fingerprint the session printed">key fingerprint {a.fingerprint}</div>
      <div className="mt-0.5 text-[11.5px] text-muted" title={full(isoOf(a.created_at))}>
        from {a.asked_from ?? "an address the ADE could not read"}, {ago(isoOf(a.created_at))}
      </div>
      {a.session_ref && (
        <div className="truncate text-[11.5px] text-muted" title="The request's own word: nothing has checked it">
          says its session is {ref ? <a className="text-accent hover:underline" href={ref} target="_blank" rel="noopener noreferrer">{visible(ref)}</a> : visible(a.session_ref)}
        </div>
      )}
      {old && (
        <div className="mt-1.5 rounded border border-line px-2 py-1.5 text-[11.5px]">
          <div>
            A live cloud link is named <Name name={old.name} className="font-medium" />: linked <span title={full(isoOf(old.created_at))}>{ago(isoOf(old.created_at))}</span>,
            last seen <span title={full(isoOf(old.last_seen_at))}>{seen}</span>.
          </div>
          <div className="mt-0.5 font-mono text-[11px]">its key {old.fingerprint}</div>
          <div className="font-mono text-[11px]">new key {a.fingerprint}</div>
          <div className="mt-0.5 text-[11px] text-muted">If you approve and replace, that link stops working when the hold ends, and this session takes its name.</div>
        </div>
      )}
      {twins > 1 && <div className="mt-1 text-[11.5px] text-wait">{twins} requests call themselves <Name name={a.name} />: approve only the one whose fingerprint the session printed.</div>}
      {holder && (
        <div className="mt-1 text-[11.5px] text-wait">
          {holder.kind === "local"
            ? <>A live local link is named <Name name={holder.name} /> already; a local link is never replaced. Revoke it before approving this, or refuse this.</>
            // messages go by exact name, so a replacement keeps the link's name as it is
            : holder.kind === "cloud" && a.kind === "cloud" && holder.name !== a.name
              ? <>A live cloud link is named &ldquo;<Name name={holder.name} />&rdquo;: the names differ only in case or spacing. {spacedBadly(holder.name)
                ? "A name spaced so cannot be asked for: revoke it before approving this, or refuse this."
                : "To replace it, the session asks under the link's own name."}</>
              : <>A live link is named <Name name={holder.name} /> already: revoke it before approving this, or refuse this.</>}
        </div>
      )}
      <div className="mt-1 text-[11px] text-faint">
        Approve only a request whose fingerprint is the one the session printed.
        {hold === undefined ? "" : hold > 0 ? ` The link is made ${waitWords(hold)} after you approve it; your devices with notifications are told, and you can cancel it until then.`
          : " The link is made as you approve it (this deployment holds no approval); your devices with notifications are told."}
      </div>
      {!approving && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {old ? (
            <Confirm label="Approve and replace" again={`Click again to replace ${old.name}`} disabled={!!refusal.taken}
              onConfirm={async () => { try { setApproving({ what: "approve", label: `Replacing “${old.name}”`, action: "Approve and replace", subject: `link-approve:${a.id}:${old.id}`, ways: await confirmWays(), replace: old.id }); } catch (e) { setError(e); } }} />
          ) : (
            // a name a live link has cannot be approved: the server would refuse it after the confirmation
            <button className="btn btn-sm btn-primary" disabled={!!holder || !!refusal.taken}
              title={holder ? "A live link has this name: revoke it first, or refuse this request" : undefined}
              onClick={async () => { try { setApproving({ what: "approve", label: `Approving “${a.name}”`, action: "Approve it", subject: `link-approve:${a.id}`, ways: await confirmWays() }); } catch (e) { setError(e); } }}>
              Approve
            </button>
          )}
          <button className="btn btn-sm" disabled={!!refusal.taken}
            onClick={() => void refusal.act("Refused", () => Api.refuseLinkAsk(a.id), refresh)}>Refuse</button>
        </div>
      )}
      {refusal.taken && <TakenLine said={`${refusal.taken}: it is not linked`} sure={refusal.sure} />}
      <CameBack why={refusal.failed} />
      {approving && (
        <NextStep say={`${approving.label}: confirm it, and its hold begins.`}>
          <ConfirmIt change={approving} onCancel={() => setApproving(null)}
            onConfirmed={async (confirmation?: string) => {
              const r = await Api.approveLinkAsk(a.id, confirmation, approving.replace);
              setApprovedSaid(`Approved: “${a.name}” is linked unless you cancel it first. ${untilWords(r.hold?.takes_effect_at)}.`);
              setApproving(null);
              await refresh();
            }} />
        </NextStep>
      )}
    </div>
  );
}

/** A move into the workspace that waits on its hold, in Linked sessions, where its notice opens: what moves and from
 *  where, when its machine may take it and how many devices were told, with Cancel until then; one whose hold ended
 *  without its being sent, why. */
export function HeldMoveCard({ m, onNotice }: { m: LinkMove; onNotice?: (text: string) => void }) {
  const refresh = useStore((s) => s.refreshLinks);
  const setError = useStore((s) => s.setError);
  const cancelling = useNeedsServer(CANCEL_OFFLINE);
  // Cancel shows itself taken at once, and comes back with the reason if the server does not take it
  const cancelled = useAct(holdKey(m.hold_id ?? m.session), setError);
  const named = visible(m.title ?? m.session);
  const what = <>moving &ldquo;<bdi className="font-medium">{named}</bdi>&rdquo; from {m.link} into the workspace</>;
  if (m.state !== "held") {
    return (
      <div className="rounded-lg border border-fail/50 bg-fail/[0.04] p-3 text-[12.5px]">
        You signed {what}, and it was not sent to {m.link}: {visible(m.reason ?? "")}
      </div>
    );
  }
  const cancel = () => {
    if (cancelling.off) return;
    void cancelled.act("Cancelled", () => Api.cancelHold(m.hold_id ?? ""), async () => {
      onNotice?.(`Cancelled — “${named}” won't move into the workspace.`);
      await refresh();
    });
  };
  return (
    <div className="rounded-lg border border-wait/50 bg-wait/[0.05] p-3 text-[12.5px]">
      <div className="flex items-start gap-2">
        <Hourglass size={14} className="mt-0.5 flex-none text-wait" />
        <div className="min-w-0 flex-1">
          <div>You signed {what}. {m.link}&apos;s machine may take it unless you cancel it first.</div>
          <div className="mt-0.5 font-medium text-wait"><Countdown at={m.takes_effect_at} /></div>
          {m.told != null && <div className="mt-0.5 text-[11px] text-muted">{toldWords(m.told)}</div>}
          {cancelling.note}
          {cancelled.taken && <TakenLine said={`${cancelled.taken}: it will not move into the workspace`} sure={cancelled.sure} />}
          <CameBack why={cancelled.failed} />
        </div>
        <button className={`btn btn-sm btn-danger flex-none ${UNAVAILABLE}`} disabled={!m.hold_id || !!cancelled.taken} {...cancelling.attrs}
          onClick={cancel}>Cancel</button>
      </div>
    </div>
  );
}

/** An open question, with Answer where it is to the owner (or to all) and its words are shown: a sealed question once
 *  opened and held to the rules the server holds a question to, never one the page cannot show. `linkId`: the live
 *  link that asked it; a question of a link no longer linked carries its asker's id (`link_id`) for its slot alone. */
export function Question({ q, linkId }: { q: LinkEvent & { link_id?: string }; linkId?: string }) {
  const account = useAccount();
  const asker = linkId ?? q.link_id ?? q.from_id ?? "";
  const opener = useOpened([{ envelope: q.text, slot: eventSlotOf(q, asker, account) }]);
  const words = eventShown(q, asker, account, opener);
  // dismissed: the card closes at once, and comes back with the reason if the server does not take it
  const acts = useActs(useStore((s) => s.setError));
  if (acts.taken(closeKey(q.seq))) return null;
  return (
    <div className="mt-2 rounded-md border border-wait/50 bg-wait/[0.05] px-2.5 py-2">
      <div className="mb-1 text-[11px] text-muted" title={full(q.time)}>
        {q.from} asks {q.to === "owner" ? "you" : q.to === "all" ? "every session" : q.to} · {ago(q.time)}
      </div>
      <div className="whitespace-pre-wrap break-words text-[12.5px]"><ShownText shown={words}>{(t) => visible(t)}</ShownText></div>
      <QuestionActions q={q} linkId={linkId} words={words} acts={acts} />
    </div>
  );
}

/** The keys a press acts by, by the item it acts on, for the page (Acting.tsx): a question's, a waiting's, a prompt's or a
 *  proposal's close; a command's withdrawal; a hold's cancel. */
export const closeKey = (seq: number) => `close:${seq}`;
export const withdrawKey = (seq: number) => `withdraw:${seq}`;
export const holdKey = (hold: string) => `hold:${hold}`;

/** Words on their way, from their signature to the server's answer, said as sending on the card they went from;
 *  `what` names them as the card's list does ("Your instruction", "Your answer"). */
function SendingLine({ words, what }: { words: string; what: string }) {
  return (
    <div data-sending className="mt-2 flex items-start gap-2 text-[11.5px]">
      <span className="pill flex items-center gap-1 text-wait"><Loader2 size={11} className="animate-spin" /> sending</span>
      <span className="min-w-0 flex-1 truncate" title={words}><span className="text-muted">{what}:</span> {plainLine(words)}</span>
    </div>
  );
}

/** An open waiting of a linked session's to the owner, or to all, that refers to no command: its session
 *  says it is blocked on them, and their devices were told one waits. Dismiss closes it as a question is closed,
 *  telling its session in the same fixed words; its session's next post ends it too. */
export function Waiting({ w, linkId }: { w: LinkEvent; linkId: string }) {
  const account = useAccount();
  const refresh = useStore((s) => s.refreshLinks);
  const opener = useOpened([{ envelope: w.text, slot: eventSlotOf(w, linkId, account) }]);
  const words = eventShown(w, linkId, account, opener);
  const described = useId();
  // dismissed: the card closes at once, and comes back with the reason if the server does not take it
  const dismissed = useAct(closeKey(w.seq), useStore((s) => s.setError));
  if (dismissed.taken) return null;
  return (
    <div className="mt-2 rounded-md border border-wait/50 bg-wait/[0.05] px-2.5 py-2">
      <div className="mb-1 text-[11px] text-muted" title={full(w.time)}>
        {w.from} waits for you{" \u00b7 "}{ago(w.time)}
      </div>
      <div className="whitespace-pre-wrap break-words text-[12.5px]">
        <ShownText shown={words}>{(t) => visible(t)}</ShownText>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        <button className="btn btn-sm" aria-describedby={described}
          onClick={() => void dismissed.act("Dismissed", () => Api.closeLinkQuestion(w.seq), refresh)}>
          Dismiss
        </button>
        <span id={described} className="text-[11px] text-muted">Dismissing tells {w.from} you closed it.</span>
      </div>
      <CameBack why={dismissed.failed} />
    </div>
  );
}

/** What is done with an open question where it is read (its card, or its row in the conversation): Answer, where it is
 *  to the owner (or to all), from a live link, and its words are shown (`words`, as the place opened them); and
 *  Dismiss, which tells its session the owner closed it without an answer. */
export function QuestionActions({ q, linkId, words, acts: given, onSending, onSent }: {
  q: LinkEvent; linkId?: string; words: Shown<string>;
  /** the card's presses, where the card closes on a dismissal (Question's); else its own, and only its buttons go */
  acts?: Acts;
  /** an answer signed here, from its signature to the server's answer: given (by a conversation), the place it is in
   *  shows it as sending; else this card does, under its question */
  onSending?: (words: string | null) => void;
  /** what is read again once an answer was taken, before its sending line goes (the conversation's own lines) */
  onSent?: () => Promise<void>;
}) {
  const refresh = useStore((s) => s.refreshLinks);
  const own = useActs(useStore((s) => s.setError));
  const acts = given ?? own;
  const closing = closeKey(q.seq);
  const dismissed = acts.taken(closing);
  // an answer begun and not sent opens again with its words
  const [answering, setAnswering] = useState(() => !!linkId && !!draftText(answerDraft(linkId, q.seq)));
  // an answer on its way, said on this card where no conversation says it
  const [sendingHere, setSendingHere] = useState<string | null>(null);
  const sendingTo = onSending ?? setSendingHere;
  const described = useId();
  const toOwner = q.to === "owner" || q.to === "all";
  return (
    <>
      {dismissed ? <TakenLine said={`${dismissed}: ${q.from} is told you closed it`} sure={acts.sure(closing)} /> : (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          {toOwner && linkId && words.ok && !answering && signsHere() && <button className="btn btn-sm btn-primary" onClick={() => setAnswering(true)}><KeyRound size={12} /> Answer</button>}
          <button className="btn btn-sm" aria-describedby={described}
            onClick={() => void acts.act(closing, "Dismissed", () => Api.closeLinkQuestion(q.seq), refresh)}>
            Dismiss question
          </button>
          <span id={described} className="text-[11px] text-muted">Dismissing tells {q.from} you closed the question without an answer.</span>
        </div>
      )}
      <CameBack why={acts.failed(closing)} />
      {sendingHere !== null && <SendingLine words={sendingHere} what="Your answer" />}
      {answering && !dismissed && linkId && words.ok && (
        // the digest is of the question's text as the server holds it: for a sealed question, its sealed text; but for
        // one the server sealed as encryption was turned on, of the words its session asked in the clear, as opened
        // here, since that session's own record of them is what it checks an answer against
        <SignBox purpose="answer" to={linkId} toName={q.from} placeholder="Your answer" submit="Review the answer" focus
          draft={{ key: answerDraft(linkId, q.seq), name: q.from }}
          extra={async () => ({ ref: q.seq, digest: await textDigest(q.sealed_in_place && words.ok ? words.value : q.text) })}
          onDone={async () => { setAnswering(false); await onSent?.(); sendingTo(null); await refresh(); }}
          onCancel={() => setAnswering(false)} onSending={sendingTo} />
      )}
    </>
  );
}

/** The fixed words of a Check: an instruction like any other, signed the same way, that a session answers by
 *  taking a turn. */
export const CHECK_TEXT = "Reply with your status: what you are doing, and what you are waiting on.";

/** Compose, review and sign one command: the words are composed here, the server's nonce is fetched and the payload's
 *  challenge computed on Review; the click on Sign reaches the passkey prompt with nothing awaited first. The words
 *  signed are exactly those shown under "You are signing"; the session and the life signed with them were chosen on
 *  the card before Review. Signing what a connected app proposed: `proposal` is its id, which the payload carries, and
 *  the app's name; `start` its words, which the owner may change before Review. */
export function SignBox({ purpose, to, toName, placeholder, submit, extra, fixed, start, proposal, onDone, onCancel, after, draft, focus, head, check, onFailed, answers, onSending }: {
  purpose: SignedPurpose; to: string; toName?: string; placeholder?: string; submit: string; fixed?: string; start?: string;
  proposal?: { id: string; by: string };
  extra?: () => Promise<{ ref?: number; digest?: string }>; onDone: () => Promise<void>; onCancel?: () => void; after?: number;
  /** where what is typed is kept until it is signed or discarded (linkDrafts.ts), and the link's name, to name it by */
  draft?: { key: string; name: string };
  /** opened by a button: the keyboard goes into it as it opens, to its words (or, with none to type, to Review) */
  focus?: boolean;
  /** a first line the owner does not change, signed before their words: a linked session's proposal names its proposer
   *  so ("Proposed by NAME:" and a line break) */
  head?: string;
  /** the place's own refusal of the words, said at Review before anything is fetched or signed */
  check?: (text: string) => string | null;
  /** what the place says of a refusal from the server, where it knows better than the server's words (null: those) */
  onFailed?: (e: unknown) => Promise<string | null>;
  /** what an answer's ref is: a session's question (the default) or its prompt */
  answers?: "question" | "prompt";
  /** the words signed, from their signature: where they are sent from shows them as sending; null
   *  once the server refused them, or once it took them and `onDone` has run, or before, where `onDone` says null
   *  itself once its place has read what went */
  onSending?: (words: string | null) => void;
}) {
  const [text, setText] = useState(fixed ?? start ?? (draft ? draftText(draft.key) : ""));
  const [hours, setHours] = useState(1);
  const [prepared, setPrepared] = useState<Prepared | null>(null);
  const reviewedAt = useRef(0);           // this browser's clock when the review was made: the server's may differ
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // said at Review, before the touch: which passkeys the session will refuse (pinCheck)
  const [warning, setWarning] = useState<PinWarning | null>(null);
  // Cancel with words typed asks first: a second click discards them
  const [discarding, setDiscarding] = useState(false);
  // Review and Sign wait while the page cannot reach the server, saying why
  const signing = useNeedsServer(SIGN_OFFLINE);
  // Sign on my Mac, beside Sign, where the account has a signer paired: the step parked for it (SignOnMac)
  const [onMac, setOnMac] = useState(false);
  // from the signature to the server's answer the words are on their way, said so, and nothing is signed or sent again:
  // a press is kept in a ref, so two presses before the page draws again send once
  const [sending, setSending] = useState(false);
  const pressed = useRef(false);
  const macPaired = useSignerPaired(prepared !== null);
  const draftKey = draft?.key;
  useEffect(() => (draftKey ? composerShown(draftKey) : undefined), [draftKey]);
  // two steps: the keyboard goes to Sign once it is reviewed, and back to the words once they are sent, each said
  const [said, setSaid] = useState("");
  const signButton = useRef<HTMLButtonElement>(null);
  const words = useRef<HTMLTextAreaElement>(null);
  const reviewButton = useRef<HTMLButtonElement>(null);
  const backToWords = useRef(false);
  useEffect(() => {
    if (prepared) signButton.current?.focus();
    else if (backToWords.current) { backToWords.current = false; words.current?.focus(); }
  }, [prepared]);
  useEffect(() => {
    if (focus) (words.current ?? reviewButton.current)?.focus();
  }, []);       // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!discarding) return;
    const t = setTimeout(() => setDiscarding(false), 4000);
    return () => clearTimeout(t);
  }, [discarding]);
  const type = (value: string) => {
    setText(value);
    setDiscarding(false);
    if (draft) keepDraft(draft.key, value, draft.name);
  };
  // the slash menu where the owner writes to a linked session: instructions' boxes alone; a hook,
  // so called before the return below
  const slash = useSlashMenu(text, type);
  if (!signsHere()) return <div className="mt-2 text-[12px] text-muted">{SIGN_ELSEWHERE()}</div>;
  const cancel = () => {
    if (fixed === undefined && text.trim() && !discarding) { setDiscarding(true); return; }
    if (draft) dropDraft(draft.key);
    onCancel?.();
  };
  const review = async () => {
    if (signing.off) return;
    const whole = (head ?? "") + text;
    const problem = purpose === "resume" ? null
      : commandProblem(text) ?? (head ? commandProblem(whole) : null) ?? check?.(text.normalize("NFC")) ?? null;
    if (problem) { setError(problem); return; }
    setBusy(true); setError("");
    try {
      const prepare = async () => prepareSigned(purpose, to, { text: whole.normalize("NFC"), life: hours * 3600, after, id: proposal?.id, ...(extra ? await extra() : {}) });
      const [p, w] = await Promise.all([prepare(), pinCheck(to)]);
      setWarning(w);
      setPrepared(p);
      reviewedAt.current = Date.now();
      setSaid(`${signingWhat(p, toName, after, proposal?.by, answers).replace(/:$/, ".")} ${w ? `${pinWords(w, toName ?? "The session")} ` : ""}`
              + `Sign with your ${p.allow.length ? "hardware key" : "passkey"} to send it.`);
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };
  // the server's refusal in the place's own words where it has them, else in the server's
  const failedWords = async (e: unknown) => (onFailed ? await onFailed(e).catch(() => null) : null) ?? message(e);
  // the server takes a nonce for five minutes: an older review would cost a passkey prompt and then a refusal
  const stale = () => {
    if (Date.now() - reviewedAt.current <= REVIEW_LIFE * 1000) return false;
    setPrepared(null);
    setError("This review is more than five minutes old: review it again.");
    return true;
  };
  // the signed step to its route, whichever way it was signed, and the box back to its words; from its signature it is
  // said to be sending, here and where it is sent from, until the server refuses it or `onDone` has run
  const send = async (signed: Signed, words: string) => {
    setSending(true);
    if (purpose !== "resume") onSending?.(words);
    try {
      if (purpose === "resume") await Api.resumeLink(to, signed); else await Api.sendLinkCommand(signed);
    } catch (e) {
      setSending(false); onSending?.(null);
      throw e;
    }
    backToWords.current = true;
    setPrepared(null); setText(fixed ?? ""); setOnMac(false);
    setSaid(`Signed: ${purpose === "resume" ? "the resume" : `your ${purpose}`} went to ${toName ?? "the session"}.`);
    if (draft) dropDraft(draft.key);                       // sent: nothing is left to keep
    try { await onDone(); } finally { setSending(false); onSending?.(null); }
  };
  const sign = async () => {
    if (!prepared || signing.off || pressed.current || stale()) return;
    pressed.current = true;
    setBusy(true); setError("");
    try {
      const signed = await signPrepared(prepared);          // the prompt first, nothing awaited before it
      await send(signed, prepared.words);
    } catch (e) { setPrepared(null); setError(await failedWords(e)); } finally { setBusy(false); pressed.current = false; }
  };
  const signedOnMac = async (signed: Signed) => {
    if (!prepared || pressed.current) return;
    pressed.current = true;
    try { await send(signed, prepared.words); } catch (e) { setPrepared(null); setOnMac(false); setError(await failedWords(e)); } finally { pressed.current = false; }
  };
  return (
    <div className="mt-2 rounded-md border border-line p-2">
      {!prepared && purpose !== "resume" && (
        <>
          {head && (
            // the line the owner does not change, shown as it is signed, above their words
            <div aria-label="Its first line, which stays" className="mb-1 whitespace-pre-wrap break-words rounded bg-bg px-2 py-1 font-mono text-[12px] text-fg-dim">{visible(head.replace(/\n$/, ""))}</div>
          )}
          {purpose === "instruction" && slash.menu}
          {purpose === "instruction" && slash.modal}
          {fixed === undefined && (
            <textarea ref={words} aria-label={placeholder} className="input h-20 w-full resize-y text-[12.5px]" placeholder={placeholder}
              value={text} onChange={(e) => type(e.target.value)}
              {...(purpose === "instruction" ? slash.boxProps : {})}
              onKeyDown={(e) => { if (purpose === "instruction") slash.onKeyDown(e); }} />
          )}
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {purpose === "instruction" && fixed === undefined && slash.button}
            {purpose === "instruction" && (
              // cut to a short width at a phone's, its full words on its menu, so the row holds the slash button too
              <select aria-label="Expires after"
                className="input h-7 w-auto min-w-0 max-w-[104px] text-[12px] sm:max-w-none"
                value={hours} onChange={(e) => setHours(Number(e.target.value))}>
                {[1, 4, 8, 24].map((h) => <option key={h} value={h}>expires in {h} {h === 1 ? "hour" : "hours"}</option>)}
              </select>
            )}
            <button ref={reviewButton} className={`btn btn-sm btn-primary ${UNAVAILABLE}`} disabled={busy} {...signing.attrs} onClick={() => void review()}>
              {busy ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />} {submit}
            </button>
            {onCancel && (
              <button className={`btn btn-sm ${discarding ? "btn-danger" : ""}`} disabled={busy} onClick={cancel}>
                {discarding ? "Discard draft?" : "Cancel"}
              </button>
            )}
          </div>
          {signing.note}
        </>
      )}
      {!prepared && purpose === "resume" && (
        <>
          <div className="flex flex-wrap items-center gap-1.5">
            <button ref={reviewButton} className={`btn btn-sm btn-primary ${UNAVAILABLE}`} disabled={busy} {...signing.attrs} onClick={() => void review()}>{busy ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />} {submit}</button>
            {onCancel && <button className="btn btn-sm" disabled={busy} onClick={cancel}>Cancel</button>}
          </div>
          {signing.note}
        </>
      )}
      {prepared && (
        <div>
          {/* clipped to its own lines, so nothing in an app's name is drawn over the words below it */}
          <div className="overflow-hidden text-[11.5px] text-fg-dim">{signingWhat(prepared, toName, after, proposal?.by, answers)}</div>
          {purpose !== "resume" && <pre className="mt-1 whitespace-pre-wrap break-words rounded bg-bg px-2 py-1.5 font-mono text-[12px] text-fg">{prepared.words}</pre>}
          {proposal && purpose !== "resume" && (
            <div className="mt-1 overflow-hidden text-[11px] text-wait">
              {prepared.words === ((head ?? "") + (start ?? "")).normalize("NFC")
                ? `Words proposed by \u201c${visible(proposal.by)}\u201d: once you sign them, they are your ${purpose} to ${toName ?? "the session"}.`
                : `You changed the words \u201c${visible(proposal.by)}\u201d proposed: once you sign them, they are your ${purpose} to ${toName ?? "the session"}.`}
            </div>
          )}
          <div className="mt-1 text-[11px] text-faint">Your passkey's prompt does not show these words: this is the only place you see them before you sign.</div>
          {warning && <PinNotice w={warning} name={toName ?? "The session"} />}
          {!onMac && (
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              <button ref={signButton} className={`btn btn-sm btn-primary ${UNAVAILABLE}`} disabled={busy || sending} {...signing.attrs} onClick={() => void sign()}>
                {sending ? <><Loader2 size={12} className="animate-spin" /> Sending</>
                  : <><KeyRound size={12} /> {prepared.allow.length ? "Sign with your hardware key" : "Sign with your passkey"}</>}
              </button>
              {macPaired && <button className={`btn btn-sm ${UNAVAILABLE}`} disabled={busy || sending} {...signing.attrs} onClick={() => { if (!signing.off && !stale()) { setError(""); setOnMac(true); } }}><Laptop size={12} /> Sign on my Mac</button>}
              <button className="btn btn-sm" disabled={busy || sending} onClick={() => { backToWords.current = true; setPrepared(null); }}>Edit</button>
            </div>
          )}
          {onMac && !sending && <SignOnMac prepared={prepared} onSigned={signedOnMac} onCancel={() => setOnMac(false)} />}
          {onMac && sending && (
            // the Mac signed it: its request is put away, and what goes is said, as Sign says it
            <div role="status" className="mt-1.5 flex items-center gap-1.5 text-[12px] text-muted"><Loader2 size={12} className="animate-spin" /> Sending</div>
          )}
          {signing.note}
        </div>
      )}
      {error && <div role="alert" className="mt-1.5 text-[12px] text-fail">{error}</div>}
      <Said text={said} />
    </div>
  );
}

const REVIEW_LIFE = 290;    // seconds a review is signed within: the nonce's 300, less a margin for the prompt

/** One of the account's passkeys as Account lists it (GET /api/account/passkeys): a session pins it by its fingerprint. */
export interface AccountKey { id: string; name: string; created_at: number; fingerprint?: string; tier?: string }

/** Which of the passkeys that can sign a command a session refuses: it acts only on what a passkey it pinned signed.
 *  `known`: from the pins it reports (with its History list); where it reports none, the passkeys added after it was
 *  linked, which it pinned only if it was given their pin line. `all`: it refuses every one of them. */
export interface PinWarning { refused: { name: string; fingerprint: string }[]; all: boolean; known: boolean; pinned: string[] }

/** The warning a review gives before the touch (PinWarning), from the account's passkeys, whether its high security is
 *  on (only its hardware keys sign a command then), the pins the session reports (none: not reported) and when it was
 *  linked. Null when it refuses none of them, or when that cannot be told. */
export function pinsWarning(keys: AccountKey[], high: boolean, pins: string[] | null | undefined, linked: number | null | undefined): PinWarning | null {
  const can = keys.filter((k) => !!k.fingerprint && (!high || k.tier === "hardware"));
  if (!can.length) return null;
  const known = !!pins && pins.length > 0;
  const refused = can.filter((k) => (known ? !pins!.includes(k.fingerprint!) : linked != null && k.created_at > linked));
  if (!refused.length) return null;
  return { refused: refused.map((k) => ({ name: k.name, fingerprint: k.fingerprint! })), all: refused.length === can.length, known,
           pinned: known ? can.filter((k) => pins!.includes(k.fingerprint!)).map((k) => k.name) : [] };
}

/** pinsWarning for a review of a command to link `to`, read as it is made: the account's passkeys, and the session's
 *  pins as encryption's view of the account serves them (the one the page holds where it cannot be read now). Null
 *  where they cannot be read: nothing is said then. */
export async function pinCheck(to: string): Promise<PinWarning | null> {
  try {
    const [mine, view] = await Promise.all([
      api<{ passkeys: AccountKey[]; high_security?: boolean }>("/api/account/passkeys"),
      E2EApi.view().catch(() => useE2EStore.getState().view),
    ]);
    const linked = useStore.getState().links?.links.find((l) => l.id === to)?.created_at;
    return pinsWarning(mine.passkeys ?? [], !!mine.high_security, view?.links.find((l) => l.link_id === to)?.pins, linked);
  } catch {
    return null;
  }
}

/** "A", "A and B", "A, B and C" (or, with "or"). */
const nameList = (names: string[], last: string) =>
  (names.length < 2 ? names.join("") : `${names.slice(0, -1).join(", ")} ${last} ${names[names.length - 1]}`);

/** A PinWarning in a sentence, the session named. */
export function pinWords(w: PinWarning, name: string): string {
  const them = nameList(w.refused.map((k) => k.name), "and");
  const one = w.refused.length === 1;
  return w.known
    ? w.all
      ? `${name} will refuse this: it has not pinned ${them}, ${one ? "the only passkey" : "the passkeys"} that can sign it.`
      : `${name} takes this only if ${nameList(w.pinned, "or")} signs it: it has not pinned ${them}, and refuses what ${one ? "that key signs" : "they sign"}.`
    : `${them} ${one ? "was" : "were"} added after ${name} was linked: unless you gave ${name} ${one ? "its pin line" : "their pin lines"}, `
      + (w.all ? "it refuses this." : `it refuses what ${one ? "that key signs" : "they sign"}.`);
}

/** What a review says before the touch when the session will refuse what some passkey signs, plainly, with the pin
 *  line that makes it take them, to run in that session's own window. */
export function PinNotice({ w, name }: { w: PinWarning; name: string }) {
  const [copied, setCopied] = useState(false);
  const them = nameList(w.refused.map((k) => k.name), "and");
  const line = pinLine(name, w.refused.map((k) => k.fingerprint));
  const said = pinWords(w, name);
  return (
    <div role="note" aria-label="Before you sign" className="mt-1.5 rounded-md border border-wait/50 bg-wait/[0.06] p-2 text-[12px]">
      <div className="text-wait">{said}</div>
      {line === null ? <div className="mt-1 text-fail">{NO_LINE}</div> : <>
        <div className="mt-1 text-fg-dim">To have it take {them}, run this in {name}&apos;s own window first:</div>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all rounded bg-bg px-2 py-1.5 font-mono text-[11px] text-fg">{line}</pre>
        <button className="btn btn-sm mt-1" onClick={() => { void navigator.clipboard?.writeText(line); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
          {copied ? <Check size={12} /> : <Copy size={12} />} {copied ? "Copied" : "Copy the pin line"}
        </button>
      </>}
    </div>
  );
}

/** What a review is about to sign, beside its words: to which session (its name and its link's id), what it is, and
 *  until when it can be taken; an answer says which question it answers, a resume which stop it lifts, a move which
 *  of the machine's sessions it moves; what a connected app proposed says which app proposed it, the words being the
 *  owner's once signed. */
export function signingWhat(p: Prepared, toName?: string, after?: number, proposedBy?: string, answers: "question" | "prompt" = "question"): string {
  const to = String(p.payload.to ?? "");
  const target = `${toName ?? "the session"} (link ${to.slice(0, 8)})`;
  const until = dayTime(Number(p.payload.expires_at));
  // the app's name as AppName draws it: what is not shown as itself written as [U+XXXX]
  const by = proposedBy ? `, proposed by \u201c${visible(proposedBy)}\u201d (its own word)` : "";
  switch (p.payload.purpose) {
    case "answer": return `You are signing an answer to ${answers} [${String(p.payload.ref)}] from ${target}${by}, good until ${until}:`;
    case "resume": return `You are signing a resume of ${target}${by}, lifting its stop of ${after ? dayTime(after) : "now"}, good until ${until}.`;
    case "move": return `You are signing a move of session ${String(p.payload.session)} into the workspace, for ${target} to take until ${until}.`;
    default: return `You are signing an instruction to ${target}${by}, good until ${until}:`;
  }
}

/** A command's state as the page says it: "answered" is the session's own word that it replied, never checked, so it
 *  reads "replied", in no colour of success. */
export const stateWord = (s?: LinkEvent["state"]) => (s === "answered" ? "replied" : s ?? "");
/** What a queued command waits for, said beside it: the session's
 *  watcher reads every 3 seconds (ade_link.py, WATCH_EVERY) and wakes an idle session; a busy one sees it at its next
 *  step. A heartbeat does not say a watcher runs (the `heartbeat` command sends one too), so the words say what the
 *  seconds wait on. */
export const QUEUED_SAID = "An idle session with its watcher running picks it up within seconds; a busy one after its current step.";
/** What the page says of a step, once after the newest of a view's steps and as each step's title: a call's line
 *  carries its tool's name and its target alone, so a command's names its program and nothing it runs after it. */
export const STEP_SAID = 'A Bash step names the program its command begins with, never what else it runs: "grep -l x *.yml | xargs rm -f" shows as "Bash: grep". Where it cannot tell the program, as for a command that begins by setting a variable, it shows Bash alone.';
/** The place in `said` of its newest step, -1 where it holds none. */
export const newestStep = (said: Said[]) => said.map((s) => s.who).lastIndexOf("step");
/** The same, for a link whose heartbeat has not come in QUEUED_HEARD seconds: no watcher of its is reading
 *  (a watcher's heartbeat comes every 5 minutes). It names the watcher, since the session may be heard
 *  by its live rounds meanwhile. */
export const QUEUED_UNHEARD = "Its session's watcher has not been heard from in the last 10 minutes: "
  + "the session picks this up at its next step, or when its watcher is back.";
const QUEUED_HEARD = 600;
/** What a link's queued command waits for: QUEUED_SAID, or QUEUED_UNHEARD where its heartbeat, its watcher's signal,
 *  has not come in QUEUED_HEARD seconds (whatever else it signed); said beside the command and on its pill
 *  alike. */
const queuedFor = (l: Pick<LinkView, "last_heartbeat_at">) =>
  (l.last_heartbeat_at != null && Date.now() / 1000 - l.last_heartbeat_at < QUEUED_HEARD ? QUEUED_SAID : QUEUED_UNHEARD);
const stateSaid = (s: LinkEvent["state"] | undefined, queued: string) =>
  (s === "answered" ? "The session says it replied: its word, not checked" : s === "queued" ? queued : undefined);

/** The owner's newest commands to a session, each with where it stands; one no session has taken can be withdrawn. */
export function Commands({ l }: { l: LinkView }) {
  const refresh = useStore((s) => s.refreshLinks);
  // a command withdrawn is said so at once, its Withdraw gone, and comes back with the reason if the server does not
  // take it; the list keeps every state, so it says the server's word once read again
  const acts = useActs(useStore((s) => s.setError));
  const cmds = l.commands ?? [];
  // what signed each, as the server records it (SignedBy): the account's passkeys name the one that did
  const keys = useKnownPasskeys(cmds.length > 0);
  if (!cmds.length) return null;
  const queued = queuedFor(l);
  return (
    <div className="mt-2 space-y-1">
      {cmds.map((c) => {
        const key = withdrawKey(c.seq);
        const withdrawn = c.state === "queued" ? acts.taken(key) : null;
        return (
        <div key={c.seq}>
          <div className="flex items-start gap-2 text-[11.5px]">
            <span className={`pill ${c.state === "queued" ? "text-wait" : "text-muted"}`} title={stateSaid(c.state, queued)}>{stateWord(c.state)}</span>
            <span className="min-w-0 flex-1 truncate" title={c.text}><span className="text-muted">{c.kind === "answer" ? "Your answer" : "Your instruction"}:</span> {plainLine(c.text)}</span>
            {c.state === "queued" && !withdrawn && (
              <button className="btn btn-sm !h-6" onClick={() => void acts.act(key, "Withdrawn", () => Api.withdrawLinkCommand(c.seq), refresh)}>Withdraw</button>
            )}
          </div>
          {withdrawn && <TakenLine said={withdrawn} sure={acts.sure(key)} />}
          <SignedBy facts={c as SignedFacts} keys={keys} />
          <CameBack why={acts.failed(key)} />
        </div>
        );
      })}
    </div>
  );
}

/** Stop and Resume: a stop needs only the sign-in (a forged one can only halt), a resume one of the owner's passkeys. */
export function StopResume({ l }: { l: LinkView }) {
  const refresh = useStore((s) => s.refreshLinks);
  const setError = useStore((s) => s.setError);
  const [resuming, setResuming] = useState(false);
  if (!l.stopped_at) {
    return <Confirm label="Stop" again="Click again to stop it" icon={<Pause size={12} />}
      onConfirm={async () => { try { await Api.stopLink(l.id); await refresh(); } catch (e) { setError(e); } }} />;
  }
  const acked = l.kind === "cloud" ? "a stop is a request in the cloud" : l.stop_acked_at ? "acknowledged" : "stop sent, not yet acknowledged";
  return (
    <div className="w-full">
      <div className="text-[11.5px] text-fail">Stopped {ago(isoOf(l.stopped_at))} · {acked}</div>
      {resuming
        ? <SignBox purpose="resume" to={l.id} toName={l.name} submit="Review the resume" after={l.stopped_at} focus onDone={async () => { setResuming(false); await refresh(); }} onCancel={() => setResuming(false)} />
        : signsHere()
          ? <button className="btn btn-sm mt-1" onClick={() => setResuming(true)}><Play size={12} /> Resume</button>
          : <div className="mt-1 text-[11px] text-muted">{SIGN_ELSEWHERE()}</div>}
    </div>
  );
}

export function LinkCard({ l, questions, waitings, onTicket, onTalk, sessionsBelow }: {
  l: LinkView; questions: LinkEvent[]; waitings?: LinkEvent[]; onTicket: () => void; onTalk?: () => void;
  sessionsBelow?: boolean;
}) {
  const refresh = useStore((s) => s.refreshLinks);
  const setError = useStore((s) => s.setError);
  const [open, setOpen] = useState(false);
  // an instruction begun and not sent opens again with its words
  const [commanding, setCommanding] = useState<null | "free" | "check">(() => (draftText(instructionDraft(l.id)) ? "free" : null));
  const [revoking, setRevoking] = useState<Change | null>(null);
  const [stopping, setStopping] = useState(false);
  // a command sent from the card: its composer goes, the keyboard goes back to Instruct, and what went is said
  const [said, setSaid] = useState("");
  // a command signed on the card, from its signature to the server's answer, named as its list names it
  const [sending, setSending] = useState<{ words: string; what: string } | null>(null);
  const instruct = useRef<HTMLButtonElement>(null);
  const backToInstruct = useRef(false);
  useEffect(() => {
    if (!commanding && backToInstruct.current) { backToInstruct.current = false; instruct.current?.focus(); }
  }, [commanding]);
  const cp = l.checkpoint && typeof l.checkpoint === "object" ? l.checkpoint : {};
  // a checkpoint's field as it is (checkpointFields.ts, which the slash menu's Lanes reads too): a list's
  // items, text as one, anything else as its JSON, and blank text as nothing; where the card shows fewer of a list's
  // items, how many more there are
  const [text, blank, items] = [fieldText, fieldBlank, fieldItems];
  const upTo = (all: string[], most: number) =>
    (all.length > most ? [...all.slice(0, most), `and ${all.length - most} more`] : all);
  const doing = blank(cp.doing) ? null : text(cp.doing);
  const [waiting, next, opens] = [items(cp.waiting_on), items(cp.next), items(cp.open)];
  // with no checkpoint, the newer of the newest step its session posted and its live view's newest round, marked as
  // that, but never a round a Stop posted again whole, whose words that Stop's notes say;
  // the line of its newest event is then not said twice
  const live = l.last_live && !l.last_live.posted_again ? l.last_live : null;
  const step = l.last_step && (!live || l.last_step.seq > live.seq) ? l.last_step : null;
  const instead = l.checkpoint ? null
    : step ? { ev: step, label: "No checkpoint yet; its newest step" }
    : live ? { ev: live, label: "No checkpoint yet; its live view's newest line" } : null;
  const heard = isoOf(heardAt(l));
  const ref = l.session_ref && l.session_ref.startsWith("https://claude.ai/") ? l.session_ref : null;
  const revoke = async () => {
    try { setRevoking({ what: "revoke", label: `Revoking ${l.name}`, action: "Revoke it", subject: `link-revoke:${l.id}`, ways: await confirmWays() }); } catch (e) { setError(e); }
  };
  // Stop it shows itself taken at once, and comes back with the reason if the server does not take it;
  // once taken, its box says so until the links are read again, which then show it stopped
  const stopped = useAct(`stop:${l.id}`, setError);
  const stop = () => void stopped.act("Stopping", () => Api.stopLink(l.id), async () => {
    try { await refresh(); } finally { setStopping(false); }
  }, { done: "Stopped" });
  return (
    <div className={`rounded-lg border p-3 ${l.silent ? "border-fail/50 bg-fail/[0.04]" : "border-line"}`}>
      <div className="flex flex-wrap items-baseline gap-x-2 text-[12.5px]">
        {/* a session's own id says nothing to read: it is under the name, for whoever looks for it */}
        <span title={l.session_ref && !ref ? `Its session: ${visible(l.session_ref)}` : undefined}><Name name={l.name} className="font-semibold" /></span>
        <span className="text-muted">{l.kind}</span>
        <span className="text-muted" title={full(heard)}>{heard ? `heard ${ago(heard)}` : "not heard from yet"}</span>
        {heard && heardThrough(l) && <span className="text-muted">{heardThrough(l)}</span>}
        {l.silent && <span className="text-fail">silent for more than two of its {l.interval_min}-minute intervals{l.heard_through === "keeper" ? KEEPER_SILENCE : ""}</span>}
      </div>
      {ref && <a className="text-[11.5px] text-accent hover:underline" href={ref} target="_blank" rel="noopener noreferrer">its session on claude.ai</a>}
      {instead ? <Latest ev={instead.ev} l={l} label={instead.label} /> : (
        <div className="mt-1.5 whitespace-pre-wrap break-words text-[12.5px] text-fg-dim">
          {doing ? visible(doing)
            : l.checkpoint ? "(its checkpoint says nothing of what it is doing)" : "(no checkpoint yet)"}
        </div>
      )}
      {l.last_event && l.last_event.seq !== instead?.ev.seq && <Latest ev={l.last_event} l={l} />}
      {waiting.length > 0 && (
        <div className="mt-1 text-[11.5px] text-muted">waiting on: {waiting.map(visible).join(" \u00b7 ")}</div>
      )}
      {next.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-[11.5px] text-fg-dim">
          {next.slice(0, 5).map((n, i) => <li key={i} className="whitespace-pre-wrap break-words">{visible(n)}</li>)}
          {next.length > 5 && <li className="list-none text-muted">and {next.length - 5} more</li>}
        </ul>
      )}
      {opens.length > 0 && (
        <div className="mt-1 text-[11.5px] text-muted">open: {upTo(opens.map(visible), 20).join(" \u00b7 ")}</div>
      )}
      {l.checkpoint_at && (
        <div className="mt-1 text-[11px] text-faint">checkpoint version {l.checkpoint_version}, {ago(isoOf(l.checkpoint_at))}</div>
      )}
      {sessionsBelow && (
        <div className="mt-1 text-[11px] text-muted">The mirrored turns of the sessions it registered, its own too once it is registered, are under each session below.</div>
      )}
      {questions.map((q) => <Question key={q.seq} q={q} linkId={l.id} />)}
      {(waitings ?? []).map((w) => <Waiting key={w.seq} w={w} linkId={l.id} />)}
      {sending && <SendingLine words={sending.words} what={sending.what} />}
      <Commands l={l} />
      {commanding && (
        <SignBox key={commanding} purpose="instruction" to={l.id} toName={l.name} placeholder={`What ${l.name} should do`} focus
          draft={commanding === "free" ? { key: instructionDraft(l.id), name: l.name } : undefined}
          fixed={commanding === "check" ? CHECK_TEXT : undefined} submit={commanding === "check" ? "Review the check" : "Review"}
          onDone={async () => {
            backToInstruct.current = true;
            setSaid(`Signed: your ${commanding === "check" ? "check" : "instruction"} went to ${l.name}.`);
            setCommanding(null);
            await refresh();
          }}
          onCancel={() => { backToInstruct.current = true; setCommanding(null); }}
          onSending={(words) => setSending(words === null ? null : { words, what: commanding === "check" ? "Your check" : "Your instruction" })} />
      )}
      <Said text={said} />
      <label className="mt-2 flex cursor-pointer items-center gap-2 text-[11.5px] text-fg-dim"
        title="Every session sees who else is linked, when each was heard and what each is doing; with this, also the rest of their checkpoints (next steps, what they wait on, their notes). The session is told when this changes.">
        <input type="checkbox" className="accent-[var(--color-accent)]" checked={!!l.reads_others}
          onChange={async (e) => { try { await Api.setLinkReadsOthers(l.id, e.target.checked); await refresh(); } catch (err) { setError(err); } }} />
        It may read the other sessions' checkpoints
      </label>
      <label className="mt-1 flex cursor-pointer items-center gap-2 text-[11.5px] text-fg-dim"
        title="What it proposes waits in Approvals, for you to read, change and sign as your own instruction: nothing it proposes reaches a session unless you sign it. Off unless you turn it on.">
        <input type="checkbox" className="accent-[var(--color-accent)]" checked={!!l.proposes}
          onChange={async (e) => { try { await Api.setLinkProposes(l.id, e.target.checked); await refresh(); } catch (err) { setError(err); } }} />
        It may propose messages for your other sessions
      </label>
      {stopping && !l.stopped_at && (
        <div role="group" aria-label={`Stopping ${l.name}`} className="mt-2 rounded-md border border-fail/50 bg-fail/[0.04] p-2 text-[12px]">
          <div>
            {l.kind === "cloud"
              ? <>Stop <Name name={l.name} />? In the cloud a stop is a request: it is asked to take nothing more until you resume it, which takes your passkey.</>
              : <>Stop <Name name={l.name} />? Its hooks hold every tool, and it takes nothing more, until you resume it, which takes your passkey.</>}
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            <button className="btn btn-sm btn-danger" disabled={!!stopped.taken} onClick={stop}><Pause size={12} /> Stop it</button>
            <button className="btn btn-sm" disabled={!!stopped.taken} onClick={() => setStopping(false)}>Keep it running</button>
          </div>
          {stopped.taken && <TakenLine said={stopped.taken} sure={stopped.sure} />}
          <CameBack why={stopped.failed} />
        </div>
      )}
      {l.stopped_at && <div className="mt-2"><StopResume l={l} /></div>}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {onTalk && <button className="btn btn-sm btn-primary" onClick={onTalk}><MessagesSquare size={12} /> Open conversation</button>}
        {!commanding && signsHere() && <button ref={instruct} className="btn btn-sm" onClick={() => setCommanding("free")}><Send size={12} /> Instruct</button>}
        {!commanding && signsHere() && (
          <button className="btn btn-sm" title="Ask it to reply with its status: shows it takes a turn, not only that its watcher runs" onClick={() => setCommanding("check")}>Check</button>
        )}
        <button className="btn btn-sm" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"} what it did · {l.events} {l.events === 1 ? "event" : "events"}</button>
        <LinkMenu name={l.name} onTicket={onTicket} onRevoke={() => void revoke()} onStop={l.stopped_at ? undefined : () => setStopping(true)} />
      </div>
      {revoking && (
        <ConfirmIt change={revoking} onCancel={() => setRevoking(null)}
          onConfirmed={async (confirmation?: string) => { await Api.revokeLink(l.id, confirmation); setRevoking(null); await refresh(); }} />
      )}
      {open && <Timeline id={l.id} />}
    </div>
  );
}

/** A session's words on one line, as plain text: its Markdown's marks taken off (emphasis, code, a link and an image
 *  by their words, a list's and a heading's marks), its lines run together, a line that ends a sentence followed by a
 *  space and any other by " · ". What is not shown as itself is still written as its code point (visible). */
export function plainLine(text: string): string {
  return visible(String(text)).split(/\r?\n/)
    .map((line) => line
      .replace(/^\s*(```|~~~).*$/, "")
      .replace(/^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+)/, "")
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/(\*\*|__)(?=\S)(.+?)(?<=\S)\1/g, "$2")
      .replace(/(^|[\s(])([*_])(?=\S)(.+?)(?<=\S)\2(?=$|[\s.,;:!?)])/g, "$1$3")
      .replace(/(`+)(.+?)\1/g, "$2")
      .replace(/\s+/g, " ")
      .trim())
    .filter(Boolean)
    .reduce((out, line) => (!out ? line : /[.!?:;]$/.test(out) ? `${out} ${line}` : `${out} · ${line}`), "");
}

const shorten = (text: string, most: number) => ([...text].length > most ? `${[...text].slice(0, most - 1).join("").trimEnd()}…` : text);

/** One of the owner's commands as a card names it: "your instruction “Run the tests…”", or "one of your commands"
 *  where the card does not hold it. */
function yourCommand(seq: number | null | undefined, l: Pick<LinkView, "commands">): string {
  const c = seq == null ? undefined : (l.commands ?? []).find((x) => x.seq === seq);
  return c ? `your ${c.kind === "answer" ? "answer" : "instruction"} “${shorten(plainLine(c.text), 48)}”` : "one of your commands";
}

/** How the newest thing a link posted begins, as a sentence, and whether its words follow: a take names the command it
 *  took (never the digest it carries), a stop it saw says so, and the rest say what they are. */
export function latestLead(ev: Pick<LinkEvent, "kind" | "to" | "ref" | "mirror" | "client_id">, l: Pick<LinkView, "commands">): { lead: string; words: boolean } {
  switch (ev.kind) {
    case "take": return { lead: `Took ${yourCommand(ev.ref, l)}`, words: false };
    case "question": return { lead: ev.to === "owner" ? "Asked you" : ev.to === "all" ? "Asked every session" : `Asked ${ev.to}`, words: true };
    case "done": return { lead: ev.ref != null ? `Replied to ${yourCommand(ev.ref, l)}` : "Replied", words: true };
    case "waiting": return ev.ref != null ? { lead: `Working on ${yourCommand(ev.ref, l)}`, words: false } : { lead: "Waiting", words: true };
    case "step": return { lead: "Step", words: true };
    // a prompt's and a proposal's words are read on their cards (LinkInbox.tsx), never as a line
    case "prompt": return { lead: "Reported a prompt in its window", words: false };
    case "proposal": return { lead: "Proposed a message for one of your sessions", words: false };
    case "note":
      if (ev.client_id?.startsWith("stopped:")) return { lead: "Saw your stop", words: false };
      if (ev.client_id?.startsWith("report:")) return { lead: `Reported on its prompt [${ev.ref}]`, words: true };
      if (ev.mirror === "owner") return { lead: "You wrote there", words: true };
      if (ev.mirror === "session") return { lead: "Said", words: true };
      if (ev.mirror === "info") return { lead: "", words: true };
      if (ev.mirror === "step") return { lead: "Called", words: true };
      if (ev.mirror === "live") return { lead: "", words: true };      // its last item's, from its words (latestSaid)
      return { lead: ev.ref != null ? `Note on ${yourCommand(ev.ref, l)}` : "Note", words: true };
    default: return { lead: ev.kind, words: true };
  }
}

/** The newest thing a link posted as one sentence, its words shown: a reply's status (done, blocked or failed, as the
 *  session's client writes it first) said in words beside what it replied to. */
export function latestSaid(ev: Pick<LinkEvent, "kind" | "to" | "ref" | "mirror" | "client_id">, l: Pick<LinkView, "commands">, text: string): string {
  if (ev.mirror === "live") {
    // a round of the live view says its newest, its last item, as that item's own note would
    const last = liveItems(text)?.at(-1);
    if (!last) return LIVE_UNREAD;
    return latestSaid({ ...ev, mirror: last[0] === "calls" ? "step" : last[0] }, l, liveText(last));
  }
  const { lead, words } = latestLead(ev, l);
  if (!words) return lead;
  const status = ev.kind === "done" ? /^(done|blocked|failed):\s*/.exec(text) : null;
  // a note of steps says its newest, the last line
  const body = ev.mirror === "step" ? (text.split("\n").filter((x) => x.trim()).pop() ?? "") : text;
  const said = plainLine(status ? body.slice(status[0].length) : body);
  const head = status ? `${lead}, ${status[1]}` : lead;
  return head ? `${head}: ${said}` : said;
}

/** The newest thing a link posted, on its card: a sentence (latestSaid), opened where it came sealed by the link,
 *  and how long ago. */
/** A card's newest line, titled with what a step names where it is a call the session made, as the conversation's step
 *  rows are: a title, which a desktop shows and a phone does not. */
const called = (said: string): ReactNode => (said.startsWith("Called: ") ? <span title={STEP_SAID}>{said}</span> : said);

function Latest({ ev, l, label }: { ev: LinkEvent; l: LinkView; label?: string }) {
  const account = useAccount();
  const opener = useOpened([{ envelope: ev.text, slot: eventSlotOf(ev, l.id, account) }]);
  const shown = eventShown(ev, l.id, account, opener);
  const { lead, words } = latestLead(ev, l);
  // a step standing in for a checkpoint says its words alone: its mark says it is a step
  const bare = !!label && ev.kind === "step";
  return (
    <div className="mt-1 flex items-baseline gap-2 text-[12px]" aria-label={label ?? "Latest"}>
      <span className="line-clamp-2 min-w-0 flex-1 break-words text-fg-dim">
        {label && <span className="text-muted">{label}: </span>}
        {!words ? lead : !shown.ok && lead && !bare ? <>{lead}: <ShownText shown={shown}>{() => null}</ShownText></>
          : <ShownText shown={shown}>{(t) => (bare ? plainLine(t) : called(latestSaid(ev, l, t)))}</ShownText>}
      </span>
      <span className="flex-none text-[11px] text-muted" title={full(ev.time)}>{ago(ev.time)}</span>
    </div>
  );
}

/** The left edge of what clips `el` across: its nearest ancestor whose overflow is not visible, or the window's. */
function clipLeft(el: HTMLElement): number {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const o = getComputedStyle(p).overflowX;
    if (o && o !== "visible") return Math.max(0, p.getBoundingClientRect().left);
  }
  return 0;
}

/** A card's less common actions, in a menu: link it again (a new key, its cursor and checkpoint kept), stop it, and
 *  revoke it. The menu opens above More, leftward from its right edge; where that would start left of the edge of what
 *  clips it, as where More wraps to the start of its row in a narrow Linked sessions, rightward from its left edge,
 *  measured each time it opens and before it is painted. */
function LinkMenu({ name, onTicket, onRevoke, onStop }: { name: string; onTicket: () => void; onRevoke: () => void; onStop?: () => void }) {
  const [open, setOpen] = useState(false);
  const [rightward, setRightward] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  useMenu(open, () => setOpen(false), box);
  useLayoutEffect(() => {
    if (!open) { setRightward(false); return; }
    const m = menu.current;
    if (m && m.getBoundingClientRect().left < clipLeft(m)) setRightward(true);
  }, [open]);
  const pick = (f: () => void) => () => { setOpen(false); f(); };
  const item = "flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-left text-[12.5px] text-fg-dim hover:bg-hover hover:text-fg";
  return (
    // Escape closes the menu alone, not Linked sessions around it
    <div className="relative" ref={box} onKeyDown={(e) => { if (e.key === "Escape" && open) e.preventDefault(); }}>
      <button className="btn btn-sm" aria-haspopup="menu" aria-expanded={open} aria-label={`More for ${name}`} title="Link again, stop or revoke"
        onClick={() => setOpen((o) => !o)}>
        <MoreHorizontal size={13} /> More
      </button>
      {open && (
        <div role="menu" ref={menu} aria-label={`More for ${name}`} className={`absolute bottom-full ${rightward ? "left-0" : "right-0"} z-50 mb-1 w-60 rounded-lg border border-line-strong bg-raised p-1 shadow-2xl shadow-black/60`}>
          <button role="menuitem" className={item} onClick={pick(onTicket)} title="A new ticket for this session, keeping its cursor and checkpoint">
            <Link2 size={13} /> Link again
          </button>
          {onStop && <button role="menuitem" className={item} onClick={pick(onStop)}><Pause size={13} /> Stop it…</button>}
          <button role="menuitem" className={`${item} !text-fail`} onClick={pick(onRevoke)}><X size={13} /> Revoke…</button>
        </div>
      )}
    </div>
  );
}

/** Whether any of these is sealed while the keys are locked: a list then offers Unlock once, above it. */
const anyLocked = (shown: { seal?: Seal | null }[]) => shown.some((s) => s.seal?.at === "locked");
const sealOfShown = (s: Shown<string>) => (s.ok ? {} : { seal: s.seal });

function Timeline({ id }: { id: string }) {
  const setError = useStore((s) => s.setError);
  const account = useAccount();
  const [events, setEvents] = useState<LinkEvent[] | null>(null);
  const [more, setMore] = useState(false);
  const load = async (before?: number) => {
    try {
      const got = (await Api.linkTimeline(id, before)).events;
      setEvents((had) => (before && had ? had.concat(got) : got));
      setMore(got.length === 100);
    } catch (e) { setError(e); }
  };
  useEffect(() => { void load(); }, [id]);    // eslint-disable-line react-hooks/exhaustive-deps
  // what this link posted is sealed by this link; the owner's commands to it never are
  const opener = useOpened((events ?? []).map((ev) => ({ envelope: ev.text, slot: eventSlotOf(ev, id, account) })));
  if (events === null) return <div className="mt-2 flex justify-center text-muted"><Loader2 size={14} className="animate-spin" /></div>;
  const lines = events.map((ev) => ({ ev, words: eventShown(ev, id, account, opener) }));
  return (
    <div className="mt-2 max-h-72 space-y-0.5 overflow-y-auto rounded bg-bg p-2">
      {anyLocked(lines.map((l) => sealOfShown(l.words))) && <EncryptedBar />}
      {events.length === 0 && <div className="text-[11.5px] text-muted">Nothing posted yet.</div>}
      {lines.map(({ ev, words }) => (
        <div key={ev.seq} className="whitespace-pre-wrap break-words font-mono text-[11px] text-fg-dim" title={full(ev.time)}>
          {linkHead(ev)}<ShownText shown={words} unlock={false}>{lineText}</ShownText>
        </div>
      ))}
      {more && <button className="btn btn-sm mt-1" onClick={() => void load(events[events.length - 1].seq)}>Older</button>}
    </div>
  );
}

/** One thing said in a linked session's conversation: by the owner ("you": what they typed there, as its mirror
 *  reports it, or a command they signed), by the session ("it"), the mirror's own word ("info"), or the calls the
 *  session made while its turn ran, as its live view posts them ("step"). */
export interface Said {
  seq: number;
  sub?: number;        // its place among the items of the one event it came from, a live view's round
  who: "you" | "it" | "info" | "step";
  head: string;
  text: string;
  time: string;
  state?: LinkEvent["state"];
  markdown?: boolean;
  mirror?: LinkEvent["mirror"];
  part?: number;       // the number of the last part joined in, for a message posted in numbered parts
  /** came sealed and is not shown: where it stands (its text is then empty) */
  seal?: Seal;
  /** while encryption is on: it came in the clear on a path encryption seals ("sent"), or it is one of the owner's
   *  commands, which are kept in the clear ("kept") */
  clear?: "sent" | "kept";
  /** a question the session asked the owner (or all), answered where it is read while it is open */
  question?: boolean;
  /** a prompt the session reported or a proposal it made: shown as its card (LinkInbox.tsx), answered there while open */
  item?: "prompt" | "proposal";
}

const PART = /^\((\d+) of (\d+)\) /;
export const LIVE_UNREAD = "A live update that could not be read.";
type LiveItem = ["owner" | "session" | "info", string] | ["calls", string[]];

/** A round of the live view (mirror "live"): its items, the owner's words, the session's, the mirror's own
 *  and the lines of the calls between them, in the turn's order; null for a text that is not a round's items. */
export function liveItems(text: string): LiveItem[] | null {
  let items: unknown;
  try { items = (JSON.parse(text) as { items?: unknown }).items; } catch { return null; }
  const good = (i: unknown): i is LiveItem => Array.isArray(i) && i.length === 2 && (
    (["owner", "session", "info"].includes(i[0]) && typeof i[1] === "string")
    || (i[0] === "calls" && Array.isArray(i[1]) && i[1].every((x: unknown) => typeof x === "string")));
  return Array.isArray(items) && items.length > 0 && items.every(good) ? items : null;
}

/** A live round's item as its own note's text would be: a run of calls a line each. */
function liveText(item: LiveItem): string {
  return item[0] === "calls" ? item[1].join("\n") : item[1];
}

/** How conversation() shows each event's words: `open` gives them as the page shows them (Sealed.tsx: opened, or why
 *  not); `encrypted` says the account's encryption is on. */
export interface SaidOptions { open?: (ev: LinkEvent) => Shown<string>; encrypted?: boolean }

/** A linked session's events as a conversation, oldest first: its mirror's notes (the owner's words as the session
 *  reports them, its own words, the mirror's word on what it left out, the lines of the calls it made; a round of its
 *  live view, one note, is a row for each of its items, in their order), the owner's signed instructions and answers
 *  with where each stands, the session's replies to them and its questions. Steps, its other notes and the rest are in
 *  its log. A message the mirror posted in numbered parts is one again, joined from its opened words: a part the page
 *  does not show stays a message of its own. */
export function conversation(events: LinkEvent[], name: string, you = `You, in ${name}`, { open, encrypted = false }: SaidOptions = {}): Said[] {
  const out: Said[] = [];
  // a message the mirror posted in numbered parts is one again: a part joined to the one before it
  const add = (said: Said) => {
    const part = said.mirror && !said.seal ? PART.exec(said.text) : null;
    if (part) {
      said.text = said.text.slice(part[0].length);
      said.part = Number(part[1]);
      const prev = out[out.length - 1];
      if (said.part > 1 && prev && prev.mirror === said.mirror && prev.part === said.part - 1) {
        prev.text += said.text;
        prev.part = said.part;
        if (said.clear) prev.clear = said.clear;
        return;
      }
    }
    out.push(said);
  };
  for (const ev of [...events].sort((a, b) => a.seq - b.seq)) {
    const words: Shown<string> = open ? open(ev) : { ok: true, value: String(ev.text), inClear: false };
    const base = { seq: ev.seq, text: words.ok ? words.value : "", time: ev.time,
                   ...(words.ok ? (words.inClear ? { clear: "sent" as const } : {}) : { seal: words.seal }) };
    const kept = encrypted ? { clear: "kept" as const } : {};
    if (ev.mirror === "live") {
      // a round a Stop of its session posted again whole, as the ADE says (posted_again), is left out, so each turn
      // shows once, before its Stop and after
      if (ev.posted_again) continue;
      // a round of the live view: a row for each of its items; sealed and not opened, one row that says so
      const items = words.ok ? liveItems(words.value) : [];
      if (!words.ok) add({ ...base, who: "it", head: name, mirror: "session" });
      else if (items === null) add({ ...base, who: "info", head: "", text: LIVE_UNREAD, mirror: "info" });
      else items.forEach((item, sub) => {
        const text = liveText(item);
        add(item[0] === "owner" ? { ...base, sub, text, who: "you", head: you, mirror: "owner" }
          : item[0] === "session" ? { ...base, sub, text, who: "it", head: name, markdown: true, mirror: "session" }
          : item[0] === "info" ? { ...base, sub, text, who: "info", head: "", mirror: "info" }
          : { ...base, sub, text, who: "step", head: "", mirror: "step" });
      });
      continue;
    }
    let said: Said | null = null;
    if (ev.mirror === "owner") said = { ...base, who: "you", head: you, mirror: "owner" };
    else if (ev.mirror === "session") said = { ...base, who: "it", head: name, markdown: true, mirror: "session" };
    else if (ev.mirror === "info") said = { ...base, who: "info", head: "", mirror: "info" };
    else if (ev.mirror === "step") said = { ...base, who: "step", head: "", mirror: "step" };
    else if (ev.from === "owner" && ev.kind === "instruction") said = { ...base, who: "you", head: "You signed an instruction", state: ev.state, ...kept };
    else if (ev.from === "owner" && ev.kind === "answer") said = { ...base, who: "you", head: `You answered [${ev.ref}]`, state: ev.state, ...kept };
    else if (ev.from !== "owner" && ev.kind === "done" && ev.ref != null) said = { ...base, who: "it", head: `${name} replied to [${ev.ref}]`, markdown: true };
    else if (ev.from !== "owner" && ev.kind === "question" && (ev.to === "owner" || ev.to === "all")) said = { ...base, who: "it", head: `${name} asks you`, question: true };
    else if (ev.from !== "owner" && (ev.kind === "prompt" || ev.kind === "proposal")) said = { ...base, who: "it", head: name, item: ev.kind };
    if (said) add(said);
  }
  return out;
}

/** A conversation's rows' key: an event's seq, and its item's place in a live view's round. */
const saidKey = (s: Said) => `${s.seq}.${s.sub ?? 0}`;

/** How often an open conversation is read, in milliseconds. */
export const READ_EVERY = 2000;
/** The session view reads its machine's copy of the conversation at every TALK_EVERY-th read: its machine sends one
 *  with its History list, which a session's Stop starts at most every 300 seconds (ade_link.py, HISTORY_EVERY). */
const TALK_EVERY = 5;

/** A conversation's scroller, which follows its newest line while the reader is at its bottom, also when a line
 *  already shown grows, as a sealed one does when it opens after it is shown, and stays where the reader scrolled up;
 *  `away` says the reader is not at the bottom, and `newest` takes them there. It moves
 *  when the lines change, read by what each shows, not at every read; and once the keys
 *  unlock (`keys` from locked) while some of its lines were sealed (`locked`), it takes the reader to the newest line,
 *  wherever they were, as the lines open (the reader
 *  should never have to scroll on coming in); a sealed line that only leaves the read moves nothing, nor do keys that
 *  go from locked to off or to a browser that cannot. */
function useFollow(said: Said[], more = "", locked = false, keys: E2EState | "" = "") {
  const scroller = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const [away, setAway] = useState(false);
  const was = useRef({ locked, keys });
  // and what shows after the lines or beside them (the share's line, a question's or a prompt's buttons): `more`
  const shown = said.map((s) => `${saidKey(s)}:${s.text.length}:${s.seal ? "s" : ""}:${s.state ?? ""}`).join("|") + `|${more}`;
  useLayoutEffect(() => {
    if (was.current.locked && was.current.keys === "locked" && keys === "unlocked") {
      atBottom.current = true;
      setAway(false);
    }
    was.current = { locked, keys };
    const el = scroller.current;
    if (el && atBottom.current) el.scrollTop = el.scrollHeight;
  }, [shown, locked, keys]);
  const onScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setAway(!atBottom.current);
  };
  const newest = () => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
    atBottom.current = true;
    setAway(false);
  };
  return { scroller, onScroll, away, newest };
}

/** Back to a conversation's newest line, where the reader scrolled up; and that the live view's share is used. */
function FollowBar({ away, newest, full }: { away: boolean; newest: () => void; full: boolean }) {
  return (
    <>
      {full && (
        <div role="status" aria-label="Live updates" className="my-2 rounded-md border border-wait/50 bg-wait/[0.06] px-2.5 py-1.5 text-[11.5px] text-fg-dim">
          Live updates are paused: this link&apos;s live view has used its share for this hour. Until it frees, each turn
          comes when it ends, as before the live view, and its questions and prompts still reach you.
        </div>
      )}
      {away && (
        <div className="pointer-events-none sticky bottom-1 flex justify-center">
          <button className="btn btn-sm pointer-events-auto shadow-sm" onClick={newest} aria-label="Newest">
            <ArrowDown size={13} /> Newest
          </button>
        </div>
      )}
    </>
  );
}

/** The highest seq among `events` of a Stop's notes: a mirrored note other than a round's. */
const stopOf = (events: LinkEvent[]) => Math.max(0, ...events.filter((e) => e.mirror && e.mirror !== "live").map((e) => e.seq));

/** Pages read from `from` back to `until` (seqs) as Older reads them, 100 at a time. */
async function readBack(read: (before: number) => Promise<LinkEvent[]>, from: number, until: number): Promise<LinkEvent[]> {
  const out: LinkEvent[] = [];
  for (let before = from; before > until;) {
    const page = await read(before);
    out.push(...page.filter((e) => e.seq >= until));
    if (page.length < 100 || !page.length) break;
    before = Math.min(...page.map((e) => e.seq));
  }
  return out;
}

/** What a conversation keeps between its reads, in one place, so that a read never finds a copy an effect has yet to
 *  make: its newest page as last shown, Older's pages, whether Older was asked for, the newest Stop's note read back
 *  for, whether a read back runs, and how many have failed in a row and how many reads the next waits. */
type Pages = {
  newest: LinkEvent[] | null; older: LinkEvent[]; asked: boolean; stopSeen: number | null; rereading: boolean;
  /** how many times Older's pages gave way: a read out when they did changes none of what this holds on its return,
   *  but that a read back ran */
  gen: number;
  /** the newest Stop's note a read has taken in (on its newest page, or in what it put into Older's), read back for or
   *  not */
  stopHigh: number;
  fails: number; wait: number;
};

/** `more` added to `base`, one event a seq, `more`'s copy kept. */
const withSeqs = (base: LinkEvent[], more: LinkEvent[]) => {
  const at = new Map(base.map((e) => [e.seq, e]));
  for (const e of more) at.set(e.seq, e);
  return [...at.values()];
};

/** A conversation's pages: `show` puts what `pages` holds on the page. */
function usePages() {
  const [newest, setNewest] = useState<LinkEvent[] | null>(null);
  const [older, setOlder] = useState<LinkEvent[]>([]);
  const pages = useRef<Pages>({ newest: null, older: [], asked: false, stopSeen: null, rereading: false, gen: 0,
                                stopHigh: 0, fails: 0, wait: 0 }).current;
  const show = useCallback(() => { setNewest(pages.newest); setOlder(pages.older); }, [pages]);
  return { newest, older, pages, show };
}

/** One read's newest page, `next`, taken in. Once Older is asked for, what left the newest page since the last read goes
 *  into Older's pages, and where the two do not meet (a full page whose oldest is past the last one's newest, as after
 *  a page was hidden) what lay between is read, one page, before either is shown: without that, what left
 *  the newest page was in neither and never shown again. Where that page is full and wholly past the last one, Older's
 *  pages give way and Older reads on from the newest page, so a page hidden for hours reads one page more, not all it
 *  missed; as the ADE's seqs run across its links (and in a session's view across the
 *  link's other sessions too), a full page wholly past the last one gives way even where it holds all that lay
 *  between, and one of fewer than 100 never does. A read, an Older's or a read back out when Older's pages gave way
 *  changes nothing of what a conversation keeps on its return, answer or failure, but that a read back has ended, a
 *  failure still said as an error (without that, a read back lowered what was read back
 *  for, which the read that let them go had set). Then, while a Stop's note a read has taken in (a mirrored note other
 *  than a round's, in its newest page or in what it put into Older's) is past the last read back for,
 *  Older's pages are read again, so a round there that the Stop posted again goes too (posted_again):
 *  their whole span, one read back at a time, merged so a page Older
 *  loaded meanwhile, or what left the newest page meanwhile, stays. A note taken in while a read
 *  back runs, even one gone from the newest page by its end, or one whose view went away during it,
 *  is read back at the next read; one whose read back failed, after a wait of a read
 *  that doubles with each failure in a row, to 30 (without that, retried at every read, each from the top of
 *  Older's span and each said as an error, for as long as Older was loaded), the count and the wait starting again
 *  where Older's pages give way (without that, five failures made a later note in pages read again wait 16 reads).
 *  A page left open reads Older's span again at each Stop's note. */
export async function takeNewest(p: Pages, next: LinkEvent[], read: (before: number) => Promise<LinkEvent[]>,
                                 show: () => void, live: () => boolean = () => true): Promise<void> {
  const prev = p.newest, gen = p.gen;
  let left: LinkEvent[] = [], gone = false;
  if (p.asked && prev?.length) {
    const top = next.length ? Math.min(...next.map((e) => e.seq)) : Infinity;
    const last = Math.max(...prev.map((e) => e.seq));
    left = prev.filter((e) => e.seq < top);
    if (next.length === 100 && top > last + 1) {
      const between = await read(top);
      if (between.length === 100 && Math.min(...between.map((e) => e.seq)) > last + 1) gone = true;
      else left = [...left, ...between.filter((e) => e.seq > last)];
    }
  }
  if (!live() || p.gen !== gen) return;
  p.newest = next;
  if (gone) {
    p.older = [];
    p.asked = false;
    p.gen += 1;
    p.fails = 0;
    p.wait = 0;
    left = [];
  } else if (left.length) p.older = withSeqs(p.older, left);
  show();
  p.stopHigh = Math.max(p.stopHigh, stopOf([...next, ...left]));
  const stop = p.stopHigh, seen = p.stopSeen, mine = p.gen;
  if (seen === null || stop <= seen || !p.older.length) {
    p.stopSeen = Math.max(seen ?? 0, stop);
    return;
  }
  if (p.rereading) return;              // the note stays unread back, so the read after this one's end reads it back
  if (p.wait > 0) {                     // after a read back that failed
    p.wait -= 1;
    return;
  }
  p.stopSeen = stop;
  p.rereading = true;
  try {
    const until = Math.min(...p.older.map((e) => e.seq)), from = Math.max(...p.older.map((e) => e.seq)) + 1;
    const again = await readBack(read, from, until);
    if (p.gen !== mine) return;         // Older's pages gave way meanwhile
    if (!live()) {
      p.stopSeen = seen;                // read back at the next read, where there is one
      return;
    }
    p.older = [...p.older.filter((e) => e.seq < until || e.seq >= from), ...again];
    p.fails = 0;
    show();
  } catch (e) {
    if (p.gen === mine) {               // read back at a later read
      p.stopSeen = seen;
      p.fails += 1;
      p.wait = Math.min(2 ** (p.fails - 1), 30);
    }
    throw e;
  } finally {
    p.rereading = false;
  }
}

/** The Stop hook that posts a session's turns to its link's conversation, and the PreToolUse hook that shows them
 *  while they run, for .claude/settings.local.json in its folder: the manual's
 *  ade_link.mirror.settings.json with the link's name filled in, but the client run by python3 on
 *  the PATH and the home folder named by $HOME, where the template writes both out. The page cannot know the owner's
 *  home folder or the product venv's path, and gives the block to be copied whole, nothing left to fill in. So the
 *  line finds its interpreter, the client and the link's directory through the HOME and the PATH of the session's
 *  environment, each of which a settings file the session loads can set; the manual points an owner who wants it not
 *  to, to the template, whose line names all three written out (what the client then reads, such as Claude Code's own
 *  folder through $CLAUDE_CONFIG_DIR, else HOME, it reads from either). Its interpreter runs in UTF-8 mode, -X utf8,
 *  as the template's and the link block's lines start it, so that a locale a settings file
 *  names cannot end it before the client runs; the lines the page gives to type keep CLIENT's -I alone. Null when the
 *  name is not in the form link.py holds a name to, which has no quote or $. */
export function mirrorSettings(name: string): string | null {
  if (!inForm(LINK_NAME, name)) return null;
  const line = `${CLIENT.replace(/^python3 -I /, "python3 -I -X utf8 ")} --dir "$HOME/.proofpane-ade/${name}" `;
  return JSON.stringify({ hooks: {
    Stop: [{ hooks: [{ type: "command", command: line + "mirror reply || true", timeout: 30 }] }],
    PreToolUse: [{ hooks: [{ type: "command", command: line + "mirror live || true", timeout: 5 }] }],
  } }, null, 2);
}

/** A link's conversation with nothing in it yet: what to do to see one, in place. A session on a machine posts its
 *  turns once its settings run the mirror; one in the cloud loads no hook, and shows here what it replies and asks. */
function NothingSaid({ l }: { l: LinkView }) {
  const [copied, setCopied] = useState(false);
  if (l.kind === "cloud") {
    return (
      <div className="px-2 py-8 text-center text-[12.5px] text-muted">
        Nothing said yet. A session in the cloud shows here what it replies to you and what it asks you; what you sign
        below reaches it.
      </div>
    );
  }
  const settings = mirrorSettings(l.name);
  return (
    <div className="px-2 py-6 text-[12.5px] text-muted">
      <div className="text-center">Nothing said yet. What you sign below reaches <Name name={l.name} /> either way.</div>
      {settings === null ? <div className="mt-3 text-fail">{NO_LINE}</div> : <>
        <div className="mt-3">Linked with setup? Its hooks are in its settings already; with --mirror, its next turn shows here.</div>
        <div className="mt-3">
          To see its conversation here too, add this to .claude/settings.local.json in its folder: each turn is posted
          here as it runs and as it ends. If the file already has settings, put this "hooks" inside its outer braces. If
          it has a "hooks" already, put this "Stop" and this "PreToolUse" inside that one instead; where it has either
          already, add the one entry from this one's list to that list. A settings file takes one "hooks": a second
          beside the first is still valid JSON,
          but a reader such as JavaScript's own keeps only the last, so the first one's hooks would be dropped with
          nothing said.
        </div>
        <pre aria-label="The settings to add" className="mt-1.5 overflow-x-auto whitespace-pre rounded bg-bg px-2.5 py-2 font-mono text-[11px] text-fg">{settings}</pre>
        <button className="btn btn-sm mt-1.5" onClick={() => { void navigator.clipboard?.writeText(settings); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
          {copied ? <Check size={12} /> : <Copy size={12} />} {copied ? "Copied" : "Copy the settings"}
        </button>
      </>}
    </div>
  );
}

const SAID_MARKDOWN = {
  code: MarkdownCode,
  // a session's words may name a link: one to another site opens beside the ADE, and any other is shown as text
  // (OutLink: one into this page could open its device or app-consent pane with a code the session chose); an image
  // is not fetched, its words are shown instead
  a: OutLink,
  img: ({ alt }: { alt?: string }) => <span className="text-muted">[image{alt ? `: ${alt}` : ""}]</span>,
};

/** A linked session's conversation: what was said, newest at the bottom, read again every five seconds while it is
 *  open, and under it an instruction composed and signed with a passkey. The session's words and what it reports the
 *  owner typed there are its own report; the owner's commands are theirs, signed. */
export function Conversation({ l, onBack, onClose, narrow = false }: { l: LinkView; onBack: () => void; onClose: () => void; narrow?: boolean }) {
  const setError = useStore((s) => s.setError);
  const refresh = useStore((s) => s.refreshLinks);
  const account = useAccount();
  // its open questions, answered on their own rows: what is written under the conversation is an instruction
  const questions = useStore((s) => s.links?.questions);
  const asked = useMemo(() => new Map((questions ?? []).filter((q) => q.link_id === l.id).map((q) => [q.seq, q])), [questions, l.id]);
  // its open prompts and proposals, answered on their own rows with Approvals' buttons
  const prompts = useStore((s) => s.links?.prompts);
  const proposals = useStore((s) => s.links?.proposals);
  const openPrompts = useMemo(() => new Map((prompts ?? []).filter((p) => p.link_id === l.id).map((p) => [p.seq, p])), [prompts, l.id]);
  const openProposals = useMemo(() => new Map((proposals ?? []).filter((p) => p.link_id === l.id).map((p) => [p.seq, p])), [proposals, l.id]);
  const { newest, older, pages, show } = usePages();
  const [more, setMore] = useState(false);
  const [liveFull, setLiveFull] = useState(false);
  const load = useCallback(async () => {
    try {
      const got = await Api.linkTimeline(l.id);
      setLiveFull(!!got.live_full);
      setMore((m) => m || got.events.length === 100);
      await takeNewest(pages, got.events, (before) => Api.linkTimeline(l.id, before).then((r) => r.events), show);
    } catch (e) { setError(e); }
  }, [l.id, setError, pages, show]);
  useEffect(() => {
    void load();
    const t = setInterval(() => document.visibilityState === "visible" && void load(), READ_EVERY);
    return () => clearInterval(t);
  }, [load]);
  const events = useMemo(() => {
    const seen = new Map<number, LinkEvent>();
    for (const ev of [...older, ...(newest ?? [])]) seen.set(ev.seq, ev);
    return [...seen.values()];
  }, [older, newest]);
  const opener = useOpened(events.map((ev) => ({ envelope: ev.text, slot: eventSlotOf(ev, l.id, account) })));
  const said = useMemo(() => conversation(events, l.name, undefined,
    { open: (ev) => eventShown(ev, l.id, account, opener), encrypted: opener.on }), [events, l.name, l.id, account, opener]);
  const stepAt = useMemo(() => newestStep(said), [said]);
  const locked = anyLocked(said);
  const { state: keys } = useE2E();
  // what is signed here, from its signature to the server's answer, by the place it was signed in (the box below, a
  // question's row or a prompt's card): at the conversation's end as sending, followed there
  const [sending, setSending] = useState<Readonly<Record<string, string>>>({});
  const sendingFrom = useCallback((from: string) => (words: string | null) => setSending((had) => {
    if (words !== null) return { ...had, [from]: words };
    if (!(from in had)) return had;
    const rest = { ...had };
    delete rest[from];
    return rest;
  }), []);
  const follow = useFollow(said, `${liveFull}|${[...asked.keys()]}|${[...openPrompts.keys()]}|${[...openProposals.keys()]}|${Object.keys(sending)}`, locked, keys);
  const queued = queuedFor(l);
  // Older's page below all shown, asked for before its read, so that what leaves the newest page meanwhile is kept
  const loadOlder = async () => {
    const first = Math.min(...[...pages.older, ...(pages.newest ?? [])].map((e) => e.seq));
    if (!Number.isFinite(first)) return;
    pages.asked = true;
    const gen = pages.gen;
    try {
      const got = (await Api.linkTimeline(l.id, first)).events;
      if (pages.gen !== gen) return;    // Older's pages gave way meanwhile: this page is below what is shown now
      pages.older = withSeqs(pages.older, got);
      show();
      setMore(got.length === 100);
    } catch (e) { setError(e); }
  };
  const heard = isoOf(heardAt(l));
  return (
    <>
      <div className={`sticky top-0 z-10 flex flex-none items-center justify-between gap-2 border-b border-line bg-panel px-3 ${narrow ? "h-12 !px-1" : "h-11"}`}>
        <span className="flex min-w-0 items-center gap-2 text-[13px]">
          <button className={headerIcon(narrow)} onClick={onBack} title="Back to the linked sessions" aria-label="Back to the linked sessions"><ArrowLeft size={narrow ? 18 : 15} /></button>
          <span className="truncate font-semibold">{l.name}</span>
          <span className="text-muted">{l.kind === "cloud" ? "cloud" : "local"}</span>
          <span className="truncate text-[11.5px] text-muted" title={full(heard)}>{heard ? `heard ${ago(heard)}` : "not heard from yet"}</span>
        </span>
        <span className="flex items-center gap-1">
          <button className={headerIcon(narrow)} onClick={() => void load()} title="Read again" aria-label="Read again"><RefreshCw size={14} /></button>
          <button className={headerIcon(narrow)} onClick={onClose} title="Close (Esc)" aria-label="Close Linked sessions" data-modal-close><X size={narrow ? 18 : 15} /></button>
        </span>
      </div>
      {/* Unlock above the lines, never scrolled away */}
      {locked && <div className="flex-none px-4 pt-2"><EncryptedBar /></div>}
      <div ref={follow.scroller} aria-label="The conversation" className="min-h-0 flex-1 overflow-y-auto px-4 py-3" onScroll={follow.onScroll}>
        {newest === null && <div className="flex justify-center py-8 text-muted"><Loader2 size={16} className="animate-spin" /></div>}
        {more && <div className="mb-3 flex justify-center"><button className="btn btn-sm" onClick={() => void loadOlder()}>Older</button></div>}
        {newest !== null && said.length === 0 && <NothingSaid l={l} />}
        {said.map((s, i) => {
          if (s.item) {
            const ev = events.find((e) => e.seq === s.seq);
            if (!ev) return null;
            // open: as the sessions view lists it; closed: as its log has it, saying how it closed and offering nothing
            const card = s.item === "prompt"
              ? <PromptCard p={openPrompts.get(s.seq) ?? { link_id: l.id, deadline_at: 0, look: false, ...ev }} name={l.name} live={openPrompts.has(s.seq)} unlock={false}
                  onSending={sendingFrom(`prompt:${s.seq}`)} onSent={load} />
              : <ProposalCard p={openProposals.get(s.seq) ?? { link_id: l.id, for_id: "", for: null, deadline_at: 0, ...ev }} live={openProposals.has(s.seq)} unlock={false} />;
            return <div key={saidKey(s)} className="mb-3">{card}</div>;
          }
          const q = s.question ? asked.get(s.seq) : undefined;
          return <SaidRow key={saidKey(s)} s={s} queued={queued} stepSaid={i === stepAt} after={q && <QuestionActions q={q} linkId={l.id} words={eventShown(q, l.id, account, opener)}
            onSending={sendingFrom(`answer:${q.seq}`)} onSent={load} />} />;
        })}
        {Object.entries(sending).map(([from, words]) => (
          <div key={from} data-sending className="mb-3 flex justify-end">
            <div className="max-w-[85%] rounded-lg bg-active px-3 py-2 text-[12.5px]">
              <div className="mb-0.5 flex items-center gap-1.5 text-[11px] text-muted"><Loader2 size={11} className="animate-spin" /> Sending</div>
              <div className="whitespace-pre-wrap break-words">{words}</div>
            </div>
          </div>
        ))}
        <FollowBar away={follow.away} newest={follow.newest} full={liveFull} />
      </div>
      <div className="flex-none border-t border-line px-3 pb-3">
        {said.some((s) => s.question && asked.has(s.seq)) && (
          <div className="pt-2 text-[11.5px] text-muted">To answer its question, use Answer on the question: what you write here goes as an instruction.</div>
        )}
        <SignBox purpose="instruction" to={l.id} toName={l.name} placeholder={`Message ${l.name}`} submit="Review"
          draft={{ key: instructionDraft(l.id), name: l.name }}
          onDone={async () => { await load(); sendingFrom("composer")(null); await refresh(); }} onSending={sendingFrom("composer")} />
      </div>
    </>
  );
}

/** A session's conversation as one: what its machine sent of it (History's copy, where its history.json lets
 *  conversations go), up to the first of its mirrored notes read here, then those notes; a message whose time is not
 *  known goes only where no note says when it was said. History's copy starts a message at every turn, whoever began
 *  it (a task's notification too), so a turn shows once; one mirrored into the link's own conversation is not here. */
export function sessionSaid(talk: HistoryTalk | null, notes: LinkEvent[], ran: string,
                            open: { talk?: (i: number) => Shown<string>; note?: (ev: LinkEvent) => Shown<string> } = {}): Said[] {
  const you = `You, in ${ran || "the session"}`;
  const cut = Math.min(...notes.map((n) => n.said_at ?? Infinity));
  const earlier: Said[] = [];
  (talk?.messages ?? []).forEach((m, i) => {
    const at = m.ms ?? (m.at != null ? m.at * 1000 : null);
    if (Number.isFinite(cut) && (at == null || at >= cut)) return;
    const words: Shown<string> = open.talk ? open.talk(i) : { ok: true, value: m.text, inClear: false };
    const base = { seq: -(i + 1), text: words.ok ? words.value : "", time: isoOf(m.at) ?? "",
                   ...(words.ok ? (words.inClear ? { clear: "sent" as const } : {}) : { seal: words.seal }) };
    earlier.push(m.who === "owner"
      ? { ...base, who: "you", head: you, mirror: "owner" }
      : { ...base, who: "it", head: "Claude", markdown: true, mirror: "session" });
  });
  return [...earlier, ...conversation(notes, "Claude", you, { open: open.note })];
}

/** What moving one of a machine's sessions into the workspace does, said before it is signed. */
export function moveSaid(link: string): string {
  return `Moving sends this session's conversation and its work not committed to your workspace, and the session `
    + `continues there. End it on ${link}'s machine first: a session still running there is not moved.`;
}

/** How long a move waits, said before it is signed: `hold` is the seconds, as the server says. */
export function moveWaits(hold: number, link: string): string {
  return hold > 0
    ? `It waits ${waitWords(hold)} before ${link}'s machine may take it: your devices with notifications are told at `
      + "once, and you can cancel it until then."
    : `${link}'s machine may take it as soon as it is signed (this deployment holds no move); your devices with `
      + "notifications are told.";
}

const MOVING: LinkMove["state"][] = ["held", "asked", "taken", "uploaded", "starting"];

/** Where the account's moves (newest first) leave each session, for its row: started, or on its way, by its newest. */
export function movedSessions(moves: LinkMove[]): Map<string, Moved> {
  const out = new Map<string, Moved>();
  const seen = new Set<string>();
  for (const m of moves) {
    if (seen.has(m.session)) continue;
    seen.add(m.session);
    if (m.state === "started") out.set(m.session, "started");
    else if (MOVING.includes(m.state)) out.set(m.session, "moving");
  }
  return out;
}

/** Why a session cannot move into the workspace as it stands, said before anything is signed: its repository (its
 *  first, as the list names it) is none of the workspace's, as the page last read them. Null where that cannot be told:
 *  the workspace not running, or its list not read yet, or the session naming no repository. The server holds a move
 *  to the same, before its hold (moves.check_repository). */
export function repositoryMissing(s: Pick<HistorySession, "repositories" | "project">, repos: { name: string; missing?: boolean }[] | null): string | null {
  const repo = sessionRepository(s);
  if (!repo || !repos?.length) return null;
  const has = repos.some((r) => !r.missing && r.name.toLowerCase() === repo.name.toLowerCase());
  return has ? null : `This session's repository, ${repo.name} (${repo.folder}), is not in your workspace: add it there first (Add a repository), then move the session.`;
}

/** Where a move stands, in words. */
export function moveWords(m: LinkMove, link: string): string {
  switch (m.state) {
    case "held": return `Signed: ${link}'s machine may take it at ${holdEnds(m.takes_effect_at)} unless you cancel it first.`;
    case "cancelled": return "Cancelled — it won't move into the workspace: nothing was sent.";
    case "asked": return `Asked: waiting for ${link} to take it, until ${dayTime(m.expires_at)}.`;
    case "taken": return `Taken: ${link} is sending its conversation and its work.`;
    case "uploaded": return `Sent: it waits for the workspace.${m.reason ? ` ${m.reason}` : ""}`;
    case "starting": return "Starting in the workspace.";
    case "started": return "Started in the workspace: it continues there.";
    case "failed": return `Failed: ${m.reason ?? "no reason was given."}`;
    case "expired": return `Expired: ${m.reason ?? `${link} did not take it in time.`}`;
  }
}

const MOVE_TONE: Record<LinkMove["state"], string> = {
  held: "text-wait", cancelled: "text-muted", asked: "text-wait", taken: "text-run", uploaded: "text-wait",
  starting: "text-run", started: "text-done", failed: "text-fail", expired: "text-muted",
};

/** What a move of a session whose History row came sealed says before it is signed. */
export const MOVE_SEALED = "Its title and conversation are encrypted here, and in the workspace they are in the clear.";

/** Move one of a machine's sessions into the workspace, where it continues: for a local session its machine lists
 *  and has not withheld, where the account has a workspace. Said first, with how long it waits, then signed with a
 *  passkey as an instruction is; then where it stands, read again every five seconds: a held one says when its machine
 *  may take it and who was told, with Cancel until then; one asked and not taken can be withdrawn; an uploaded one is
 *  started in the workspace from here (the workspace first, when it is not running), a started one opens there. The
 *  session's moves are read by any link of its machine, as the list may name another of them from one read to the
 *  next; the one shown is the one under way, whichever its place among them, else the newest. A new move is offered
 *  once they are read and none is under way (the server takes one move of a session at a time), and offered disabled,
 *  saying why, while the list shows the session running or its link is stopped. A session whose row came sealed is
 *  moved once its title is opened, which goes with the move, beside what is signed, for the workspace to name it by
 *  (none where it cannot be opened). Where the account keeps its work elsewhere (its choice is not `hosted`),
 *  the workspace is not started for it, so the hosted choice is offered
 *  first, with its sentence, before anything of the move is prepared. */
export function MoveToWorkspace({ s, onOpen }: { s: HistorySession; onOpen: (session: string) => void }) {
  const ws = useStore((st) => st.ws);
  const repos = useStore((st) => st.repos);
  const hold = useStore((st) => st.links?.move_hold);
  const stopped = useStore((st) => st.links?.links.find((l) => l.id === s.link_id)?.stopped_at ?? null);
  const setError = useStore((st) => st.setError);
  const offered = s.kind === "local" && !s.withheld && !!ws?.enabled && !!ws.allowed;
  const [moves, setMoves] = useState<LinkMove[] | null>(null);
  const [step, setStep] = useState<"idle" | "said" | "review">("idle");
  const [prepared, setPrepared] = useState<Prepared | null>(null);
  const reviewedAt = useRef(0);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [warning, setWarning] = useState<PinWarning | null>(null);
  // Review, Sign and a held move's Cancel wait while the page cannot reach the server, saying why
  const signing = useNeedsServer(SIGN_OFFLINE);
  const cancelling = useNeedsServer(CANCEL_OFFLINE);
  // Sign on my Mac, beside Sign once the move is reviewed, where the account has a signer paired (SignOnMac)
  const [onMac, setOnMac] = useState(false);
  const macPaired = useSignerPaired(step === "review" && prepared !== null);
  const [row] = useShownRows([s]);
  const sealedRow = rowSealed(s);
  const waiting = sealedRow && (row.seal?.at === "locked" || row.seal?.at === "opening");
  const title = sealedRow && !row.seal ? row.title : null;
  const shown = moves?.find((m) => MOVING.includes(m.state)) ?? moves?.[0] ?? null;
  // the link it went to, which may not be the one the list names now
  const via = useStore((st) => st.links?.links.find((l) => l.id === shown?.link_id)?.name) ?? s.link;
  const load = useCallback(async () => {
    try { setMoves((await Api.sessionMoves(s.id)).moves); } catch (e) { setError(e); }
  }, [s.id, setError]);
  useEffect(() => {
    if (!offered) return;
    void load();
    const t = setInterval(() => document.visibilityState === "visible" && void load(), 5000);
    return () => clearInterval(t);
  }, [offered, load]);
  // a move under way stops where the keys lock: its title is no longer open to go with it
  useEffect(() => { if (waiting) { setStep("idle"); setPrepared(null); } }, [waiting]);
  // its steps take the keyboard in turn, each said: Review the move, then Sign, then where the move stands
  const [said, setSaid] = useState("");
  // where the account keeps its work elsewhere, the hosted choice as the server says it, asked for before a move
  const elsewhere = !!ws?.choice && ws.choice !== "hosted";
  // and what stays where it lives now, said before the person keeps the hosted choice, as the account's settings say it
  const [hosting, setHosting] = useState<{ option: StorageOption | null; leaves: string[] } | null>(null);
  useEffect(() => {
    if (step !== "said" || !elsewhere) return;
    let live = true;
    setHosting(null);                                       // what the last ask read may have changed since
    Api.storage().then((v) => {
      if (live) setHosting({ option: v.choices.find((c) => c.choice === "hosted") ?? null, leaves: v.left_behind.hosted ?? [] });
    }).catch((e) => { if (live) setProblem(message(e)); });
    return () => { live = false; };
  }, [step, elsewhere]);
  const keepHosted = async () => {
    setBusy(true); setProblem("");
    try {
      const v = await Api.chooseStorage("hosted");
      const now = useStore.getState().ws;
      useStore.setState({ ws: { ...(now ?? { enabled: true }), choice: v.choice } });
      void useStore.getState().refresh();
    } catch (e) { setProblem(message(e)); } finally { setBusy(false); }
  };
  const signButton = useRef<HTMLButtonElement>(null);
  const status = useRef<HTMLDivElement>(null);
  const afterSign = useRef(false);
  useEffect(() => { if (step === "review") signButton.current?.focus(); }, [step]);
  useEffect(() => {
    if (afterSign.current && step === "idle" && shown) { afterSign.current = false; status.current?.focus(); }
  }, [step, shown]);
  if (!offered) return null;
  const moving = !!shown && MOVING.includes(shown.state);
  const running = !!ws?.running && !ws?.starting;
  const review = async () => {
    if (signing.off) return;
    setBusy(true); setProblem(""); setOnMac(false);
    try {
      const [p, w] = await Promise.all([prepareSigned("move", s.link_id, { session: s.id, life: MOVE_LIFE }), pinCheck(s.link_id)]);
      setWarning(w);
      setPrepared(p);
      reviewedAt.current = Date.now();
      setStep("review");
      setSaid(`${signingWhat(p, s.link)} ${w ? `${pinWords(w, s.link)} ` : ""}Sign with your passkey to send it.`);
    } catch (e) { setProblem(message(e)); } finally { setBusy(false); }
  };
  // the server takes a nonce for five minutes: an older review would cost a passkey prompt and then a refusal
  const stale = () => {
    if (Date.now() - reviewedAt.current <= REVIEW_LIFE * 1000) return false;
    setPrepared(null); setStep("said");
    setProblem("This review is more than five minutes old: review it again.");
    return true;
  };
  // the signed move to its route, whichever way it was signed, its title beside it; then where it stands
  const send = async (signed: Signed) => {
    await Api.sendLinkCommand(title ? { ...signed, title } : signed);
    await load();                     // read again before the step is idle: until then the list has no move of it
    afterSign.current = true;
    setSaid(hold ? `Signed: ${s.link}'s machine may take it in ${waitWords(hold)}, unless you cancel it first.`
      : `Signed: ${s.link}'s machine may take it now.`);
    setPrepared(null); setStep("idle");
  };
  const sign = async () => {
    if (!prepared || signing.off || stale()) return;
    setBusy(true); setProblem("");
    try {
      const signed = await signPrepared(prepared);          // the prompt first, nothing awaited before it
      await send(signed);
    } catch (e) { setProblem(message(e)); setPrepared(null); setStep("said"); } finally { setBusy(false); }
  };
  const signedOnMac = async (signed: Signed) => {
    setBusy(true);
    try { await send(signed); } catch (e) { setProblem(message(e)); setPrepared(null); setStep("said"); } finally { setBusy(false); }
  };
  const carryOn = async (seq: number) => {
    setBusy(true); setProblem("");
    try {
      const m = await Api.startMove(seq);
      setMoves((had) => (had ?? []).map((x) => (x.seq === m.seq ? m : x)));
      void useStore.getState().refresh();                   // the workspace may have started for it
    } catch (e) { setProblem(message(e)); } finally { setBusy(false); }
  };
  // Cancel and Withdraw show themselves taken at once, and come back with the reason if the server does not take them;
  // the move is read again either way, as before: cancelled or withdrawn, or past it
  const acts = useActs(setError);
  const cancel = (holdId: string) => {
    if (cancelling.off) return;
    setProblem("");
    void acts.act(holdKey(holdId), "Cancelled", () => Api.cancelHold(holdId), load, { refused: load });
  };
  const withdraw = (seq: number) => {
    setProblem("");
    void acts.act(withdrawKey(seq), "Withdrawn", () => Api.withdrawLinkCommand(seq), load, { refused: load });
  };
  const uploaded = shown?.state === "uploaded" ? shown.seq : null;
  const heldBy = shown?.state === "held" ? shown.hold_id ?? null : null;
  const asked = shown?.state === "asked" ? shown.seq : null;
  // moved, it continues in the workspace: no second move is offered, unless it ran on its machine since
  const moved = shown?.state === "started";
  const ranSince = moved && !!s.last_at && s.last_at > (shown?.updated_at ?? shown?.created_at ?? 0);
  // what keeps its machine from moving it now, as the page last read it: the workspace's list as the page has it
  // while the workspace runs (the opened row of a sealed one)
  const blocked = s.live ? `It is running on ${s.link}'s machine: end it there first.`
    : stopped ? `${s.link} is stopped: it takes nothing until you resume it.`
      : running && !row.seal ? repositoryMissing(row, repos) : null;
  return (
    <div className="flex-none border-t border-line px-3 py-2 text-[11.5px] text-fg-dim">
      {shown && (
        <div ref={status} tabIndex={-1} role="group" aria-label="This session's move" className="flex flex-wrap items-center gap-1.5 outline-none focus-visible:ring-1 focus-visible:ring-accent">
          <span className={`pill ${MOVE_TONE[shown.state]}`} title="This session's move into the workspace: the one under way, else its newest">move: {shown.state}</span>
          <span className="min-w-0 flex-1 break-words" title={full(shown.updated_at ?? shown.created_at)}>{visible(moveWords(shown, via))}</span>
          {uploaded !== null && (
            <button className="btn btn-sm" disabled={busy} onClick={() => void carryOn(uploaded)}>
              {busy ? <Loader2 size={12} className="animate-spin" /> : running ? <MoveRight size={12} /> : <Power size={12} />}
              {running ? " Carry on in the workspace" : " Start the workspace and carry on"}
            </button>
          )}
          {heldBy !== null && <button className={`btn btn-sm btn-danger ${UNAVAILABLE}`} disabled={busy || !!acts.taken(holdKey(heldBy))} {...cancelling.attrs} onClick={() => cancel(heldBy)}>Cancel</button>}
          {asked !== null && <button className="btn btn-sm" disabled={busy || !!acts.taken(withdrawKey(asked))} onClick={() => withdraw(asked)}>Withdraw</button>}
          {shown.state === "started" && shown.workspace_session && (
            <button className="btn btn-sm btn-primary" onClick={() => onOpen(shown.workspace_session!)}>
              <ExternalLink size={12} /> Open it in the workspace
            </button>
          )}
        </div>
      )}
      {shown?.state === "held" && <div className="mt-0.5 font-medium text-wait"><Countdown at={shown.takes_effect_at} /></div>}
      {shown?.state === "held" && shown.told != null && <div className="mt-0.5 text-[11px] text-muted">{toldWords(shown.told)}</div>}
      {heldBy !== null && cancelling.note}
      {[heldBy !== null ? holdKey(heldBy) : null, asked !== null ? withdrawKey(asked) : null].map((key) => key && (
        <div key={key}>
          {acts.taken(key) && <TakenLine said={acts.taken(key)!} sure={acts.sure(key)} />}
          <CameBack why={acts.failed(key)} />
        </div>
      ))}
      {moves !== null && !moving && step === "idle" && (!moved || ranSince) && (!signsHere()
        ? <div className="mt-1 text-[11px] text-muted">{SIGN_ELSEWHERE()}</div>
        : waiting
          ? (
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
              <Lock size={11} /> {row.seal?.at === "opening" ? "Opening its title…" : "Its title is encrypted: unlock to move it, since its title goes with the move."}
              <Unlock />
            </div>
          )
          : <>
              {ranSince && <div className="mt-1 text-[11px] text-muted">It ran on {s.link}&apos;s machine after it was moved.</div>}
              <button className="btn btn-sm mt-1" disabled={!!blocked} title={blocked ?? undefined} onClick={() => setStep("said")}><MoveRight size={12} /> {ranSince ? "Move it again" : "Move to the workspace"}</button>
              {blocked && <div className="mt-0.5 text-[11px] text-muted">{blocked}</div>}
            </>)}
      {step === "said" && elsewhere && !hosting && (
        <div className="mt-1 rounded-md border border-line p-2">
          <div>{MOVE_ELSEWHERE}</div>
          <div className="mt-1 text-muted"><Loader2 size={12} className="mr-1 inline animate-spin" /> Reading…</div>
          <button className="btn btn-sm mt-1.5" disabled={busy} onClick={() => { setStep("idle"); setProblem(""); }}>Cancel</button>
        </div>
      )}
      {step === "said" && elsewhere && hosting && (
        <NextStep say={hosting.option?.offered ? `${MOVE_ELSEWHERE} Keep your work in it to move this session.` : MOVE_ELSEWHERE}>
          <div className="mt-1 rounded-md border border-line p-2">
            <div>{MOVE_ELSEWHERE}</div>
            {hosting.option && <div className="mt-1 text-fg-dim">{hosting.option.sentence}</div>}
            {hosting.option && !hosting.option.offered && hosting.option.why && <div className="mt-1 text-muted">Not offered to you here: <span>{hosting.option.why}</span></div>}
            {hosting.leaves.length > 0 && (
              <div className="mt-1 text-fg-dim">
                <div>Where your work lives now, this stays:</div>
                <ul className="list-disc pl-4">{hosting.leaves.map((line) => <li key={line}>{line}</li>)}</ul>
              </div>
            )}
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {hosting.option?.offered && (
                <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void keepHosted()}>
                  {busy ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />} {KEEP.hosted}
                </button>
              )}
              <button className="btn btn-sm" disabled={busy} onClick={() => { setStep("idle"); setProblem(""); }}>Cancel</button>
            </div>
          </div>
        </NextStep>
      )}
      {step === "said" && !elsewhere && (
        <NextStep say={`${moveSaid(s.link)} Review the move, then sign it.`}>
          <div className="mt-1 rounded-md border border-line p-2">
            <div>{moveSaid(s.link)}</div>
            {hold !== undefined && <div className="mt-1">{moveWaits(hold, s.link)}</div>}
            {sealedRow && <div className="mt-1 text-wait">{MOVE_SEALED}</div>}
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              <button className={`btn btn-sm btn-primary ${UNAVAILABLE}`} disabled={busy} {...signing.attrs} onClick={() => void review()}>
                {busy ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />} Review the move
              </button>
              <button className="btn btn-sm" disabled={busy} onClick={() => { setStep("idle"); setProblem(""); }}>Cancel</button>
            </div>
            {signing.note}
          </div>
        </NextStep>
      )}
      {step === "review" && prepared && (
        <div className="mt-1 rounded-md border border-line p-2">
          <div>{signingWhat(prepared, s.link)}</div>
          {sealedRow
            ? <div className="mt-1 text-faint">{title ? `Its title, sent with the move in the clear to name it there: ${visible(title)}` : "Its title could not be opened here, so none goes with it: the workspace names it (untitled)."}</div>
            : s.title && <div className="mt-1 text-faint">Its title here: {visible(s.title)}</div>}
          <div className="mt-1 text-[11px] text-faint">Your passkey's prompt does not show what it signs: this is the only place you see it before you sign.</div>
          {warning && <PinNotice w={warning} name={s.link} />}
          {!onMac && (
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              <button ref={signButton} className={`btn btn-sm btn-primary ${UNAVAILABLE}`} disabled={busy} {...signing.attrs} onClick={() => void sign()}><KeyRound size={12} /> Sign with your passkey</button>
              {macPaired && <button className={`btn btn-sm ${UNAVAILABLE}`} disabled={busy} {...signing.attrs} onClick={() => { if (!signing.off && !stale()) { setProblem(""); setOnMac(true); } }}><Laptop size={12} /> Sign on my Mac</button>}
              <button className="btn btn-sm" disabled={busy} onClick={() => { setPrepared(null); setStep("idle"); }}>Cancel</button>
            </div>
          )}
          {onMac && <SignOnMac prepared={prepared} title={title ?? undefined} onSigned={signedOnMac} onCancel={() => setOnMac(false)} />}
          {signing.note}
        </div>
      )}
      {problem && <div role="alert" className="mt-1 text-[12px] text-fail">{problem}</div>}
      <Said text={said} />
    </div>
  );
}

/** One of a machine's sessions' conversation: what its machine sent of it, then its mirrored turns, newest at the
 *  bottom, read again every five seconds while it is open; read here only, the session goes on on its machine, where
 *  the command opens it, or moves into the workspace and goes on there (MoveToWorkspace). It is answered there, as
 *  its footer says; instructions go to its machine's link, whose conversation `onTalk` opens. */
export function SessionConversation({ s, onBack, onClose, onTalk, narrow = false }: { s: HistorySession; onBack: () => void; onClose: () => void; onTalk?: (link: string) => void; narrow?: boolean }) {
  const setError = useStore((st) => st.setError);
  const account = useAccount();
  const linkLive = useStore((st) => !!st.links?.links.some((l) => l.id === s.link_id));
  const [row] = useShownRows([s]);
  const [talk, setTalk] = useState<HistoryTalk | null | undefined>(undefined);
  const { newest, older, pages, show } = usePages();
  const [more, setMore] = useState(false);
  const [copied, setCopied] = useState(false);
  const [liveFull, setLiveFull] = useState(false);
  useEffect(() => {
    let live = true, reads = 0;
    // its mirrored notes at each read, and its machine's copy at the first and at every TALK_EVERY-th
    const load = async (withTalk: boolean) => {
      try {
        const [t, r] = await Promise.all([
          !withTalk ? Promise.resolve(undefined)
            : s.talk_at ? Api.linkTalk(s.link_id, s.id).catch((e) => { if (e instanceof ApiError && e.status === 404) return null; throw e; }) : Promise.resolve(null),
          Api.linkTimeline(s.link_id, undefined, s.id),
        ]);
        if (!live) return;
        if (t !== undefined) setTalk(t);
        setLiveFull(!!r.live_full);
        setMore((m) => m || r.events.length === 100);
        // its pages as in the link's conversation
        await takeNewest(pages, r.events, (before) => Api.linkTimeline(s.link_id, before, s.id).then((x) => x.events),
          show, () => live);
      } catch (e) { setError(e); }
    };
    void load(true);
    const t = setInterval(() => {
      reads += 1;
      if (document.visibilityState === "visible") void load(reads % TALK_EVERY === 0);
    }, READ_EVERY);
    return () => { live = false; clearInterval(t); };
  }, [s.link_id, s.id, s.talk_at, setError, pages, show]);
  const ran = s.client ? CLIENT_WORDS[s.client] ?? "" : "";
  const notes = useMemo(() => {
    const seen = new Map<number, LinkEvent>();
    for (const ev of [...older, ...(newest ?? [])]) seen.set(ev.seq, ev);
    return [...seen.values()];
  }, [older, newest]);
  // what its machine sent and its mirrored notes are both sealed by its machine's link
  const opener = useOpened([
    ...(talk?.messages ?? []).map((m, i) => ({ envelope: m.text, slot: talkSlotOf(talk!, i, s, account), talk: talkRefOf(talk!, s, account) })),
    ...notes.map((ev) => ({ envelope: ev.text, slot: eventSlotOf(ev, s.link_id, account) })),
  ]);
  const said = useMemo(() => sessionSaid(talk ?? null, notes, ran, {
    talk: (i) => talkShown(talk!, i, s, account, opener),
    note: (ev) => eventShown(ev, s.link_id, account, opener),
  }), [talk, notes, ran, s, account, opener]);
  const stepAt = useMemo(() => newestStep(said), [said]);
  const locked = anyLocked([row, ...said]);
  const { state: keys } = useE2E();
  const follow = useFollow(said, `${liveFull}`, locked, keys);
  const loadOlder = async () => {
    const first = Math.min(...[...pages.older, ...(pages.newest ?? [])].map((e) => e.seq));
    if (!Number.isFinite(first)) return;
    pages.asked = true;
    const gen = pages.gen;
    try {
      const got = (await Api.linkTimeline(s.link_id, first, s.id)).events;
      if (pages.gen !== gen) return;    // as in the link's conversation
      pages.older = withSeqs(pages.older, got);
      show();
      setMore(got.length === 100);
    } catch (e) { setError(e); }
  };
  const command = resumeCommand(s);
  return (
    <>
      <div className={`sticky top-0 z-10 flex flex-none items-center justify-between gap-2 border-b border-line bg-panel px-3 ${narrow ? "h-12 !px-1" : "h-11"}`}>
        <span className="flex min-w-0 items-center gap-2 text-[13px]">
          <button className={headerIcon(narrow)} onClick={onBack} title="Back to the linked sessions" aria-label="Back to the linked sessions"><ArrowLeft size={narrow ? 18 : 15} /></button>
          <span className="truncate font-semibold">{row.seal ? <SealWords seal={row.seal} unlock={false} /> : row.title ?? s.id}</span>
          <span className="flex-none text-muted">{[ran, s.link].filter(Boolean).join(" · ")}</span>
          {talk && <span className="truncate text-[11.5px] text-muted" title={full(isoOf(talk.updated_at))}>sent {dayTime(isoOf(talk.updated_at))}</span>}
        </span>
        <button className={headerIcon(narrow)} onClick={onClose} title="Close (Esc)" aria-label="Close Linked sessions" data-modal-close><X size={narrow ? 18 : 15} /></button>
      </div>
      {locked && <div className="flex-none px-4 pt-2"><EncryptedBar /></div>}
      <div ref={follow.scroller} aria-label="The conversation" className="min-h-0 flex-1 overflow-y-auto px-4 py-3" onScroll={follow.onScroll}>
        {(talk === undefined || newest === null) && <div className="flex justify-center py-8 text-muted"><Loader2 size={16} className="animate-spin" /></div>}
        {more && <div className="mb-3 flex justify-center"><button className="btn btn-sm" onClick={() => void loadOlder()}>Older</button></div>}
        {!more && talk && talk.earlier > 0 && (
          <div className="mb-3 text-center text-[11px] text-muted">
            {talk.earlier} earlier message{talk.earlier > 1 ? "s" : ""} stay on {s.link}'s machine
          </div>
        )}
        {talk !== undefined && newest !== null && said.length === 0 && (
          <div className="px-2 py-8 text-center text-[12.5px] text-muted">
            Nothing of it is here yet. To send its conversation here, add &quot;conversations&quot;: true to
            ~/.proofpane-ade/{s.link}/history.json on {s.link}&apos;s machine: it comes with the machine&apos;s next list.
          </div>
        )}
        {said.map((m, i) => <SaidRow key={saidKey(m)} s={m} stepSaid={i === stepAt} />)}
        <FollowBar away={follow.away} newest={follow.newest} full={liveFull} />
      </div>
      <MoveToWorkspace s={s} onOpen={(id) => {
        location.hash = `#session=${id}`;               // the app opens it once its list of sessions has it
        void useStore.getState().refresh();
        onClose();
      }} />
      <div className="flex-none border-t border-line px-3 py-2 text-[11.5px] text-fg-dim">
        <div>To reply to it, open it again on {s.link}&apos;s machine, in its folder, and answer it there:</div>
        <div className="mt-1 flex items-start gap-1.5">
          <pre className="min-w-0 flex-1 overflow-x-auto whitespace-pre-wrap break-all rounded bg-bg px-2 py-1 font-mono text-[11px] text-fg">{command}</pre>
          <button className="icon-btn !h-6 !w-6 flex-none" title="Copy" aria-label="Copy the command"
            onClick={() => { void navigator.clipboard?.writeText(command); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
            {copied ? <Check size={12} /> : <Copy size={12} />}
          </button>
        </div>
        {onTalk && linkLive && (
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <span>Or write from here to <Name name={s.link} />, the session linked on that machine:</span>
            <button className="btn btn-sm" onClick={() => onTalk(s.link_id)}><MessagesSquare size={12} /> Message {s.link}</button>
          </div>
        )}
      </div>
    </>
  );
}

function SaidRow({ s, after, queued = QUEUED_SAID, stepSaid = false }: { s: Said; after?: ReactNode; queued?: string; stepSaid?: boolean }) {
  const when = <span title={full(s.time)}>{dayTime(s.time)}</span>;
  // a message that came sealed and is not shown says where it stands in its place; Unlock is offered above the list
  const words = (shown: ReactNode) => (s.seal ? <SealWords seal={s.seal} unlock={false} /> : shown);
  const mark = s.clear === "sent" ? <InClear /> : s.clear === "kept" ? <KeptClear /> : null;
  if (s.who === "info") return <div className="my-2 text-center text-[11px] text-muted">{words(visible(s.text))}{mark && <> {mark}</>}</div>;
  if (s.who === "step") {
    // the calls the session made while its turn ran: a line each, a call made, which a denial or the
    // guard may still have stopped; after the newest of a view's steps, what a Bash step names (STEP_SAID)
    return (
      <>
        <div aria-label="The calls it made" title={STEP_SAID} className="my-1 whitespace-pre-wrap break-words pl-2 font-mono text-[11px] text-muted">{words(visible(s.text))}{mark && <> {mark}</>}</div>
        {stepSaid && <div className="mb-2 pl-2 text-[11px] text-muted">{STEP_SAID}</div>}
      </>
    );
  }
  if (s.who === "you") {
    return (
      <div className="mb-3 flex flex-col items-end">
        <div className="mb-0.5 flex items-center gap-1.5 text-[11px] text-muted">
          {mark}
          {s.state && <span className={`pill ${s.state === "queued" ? "text-wait" : "text-muted"}`} title={stateSaid(s.state, queued)}>{stateWord(s.state)}</span>}
          {s.state === "queued" && <span>{queued}</span>}
          <span title={s.mirror === "owner" ? "As the session's mirror reports what you typed there" : "Signed with one of your passkeys"}>{s.head}</span> · {when}
        </div>
        <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-xl rounded-br-sm bg-active px-3 py-2 text-[12.5px] text-fg">{words(visible(s.text))}</div>
      </div>
    );
  }
  return (
    <div className={`mb-3 ${after ? "rounded-md border border-wait/50 bg-wait/[0.05] px-2.5 py-2" : ""}`}>
      <div className="mb-0.5 text-[11px] text-muted">{s.head} · {when}{mark && <> {mark}</>}</div>
      {s.seal
        ? <div className="text-[12.5px]">{words(null)}</div>
        : s.markdown
          ? <div className="markdown max-w-full break-words text-[13px] leading-[1.6] text-fg"><ReactMarkdown remarkPlugins={[remarkGfm]} components={SAID_MARKDOWN}>{visible(s.text)}</ReactMarkdown></div>
          : <div className="whitespace-pre-wrap break-words text-[12.5px] text-fg">{visible(s.text)}</div>}
      {after}
    </div>
  );
}
