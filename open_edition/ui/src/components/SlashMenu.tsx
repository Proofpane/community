import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { api, linkWaiting, type LinkReported, type LinkSessions, type LinkView } from "../api";
import { fieldBlank, fieldItems, fieldText } from "../checkpointFields";
import { reportedSlot, useE2EStore } from "../e2e";
import { useStore } from "../store";
import { refusalWords, reportedLists, visible } from "../textRules";
import { dayTime } from "../when";
import { Modal } from "./Modal";
import { sealOf, sealWords, useAccount, useOpened } from "./Sealed";

// Where the owner writes, to a linked session or in the
// workspace's chat, a `/` at the start of the box, or the slash button beside it, opens a menu of what the page already
// holds, each entry a click, a tap or Enter, each opening a modal over the page that reads and changes nothing.

export type SlashEntry = "account" | "lanes" | "map" | "usage" | "plans" | "background";
export const SLASH_ENTRIES: { id: SlashEntry; name: string; hint: string }[] = [
  { id: "account", name: "Account", hint: "who is signed in, the workspace, the passkeys, the linked sessions" },
  { id: "lanes", name: "Lanes", hint: "each linked session's checkpoint, newest first" },
  { id: "map", name: "Agent map", hint: "who holds the Manager's role, the commands open, what waits on you" },
  // what each session's client reports of its own Claude Code session with its heartbeat
  { id: "usage", name: "Usage", hint: "each session's context used of its window, and its model" },
  { id: "plans", name: "Plan list", hint: "each session's task list" },
  { id: "background", name: "Background tasks", hint: "each session's background tasks and agents" },
];
const DOT = " \u00b7 ";
const READING = "reading\u2026";
const TO = " \u2192 ";

/** The entries a box's words ask for: none unless they start with `/` and hold no space yet, filtered by what follows
 *  the slash, by an entry's id or its name. */
export function slashMatches(text: string): SlashEntry[] | null {
  if (!text.startsWith("/") || /\s/.test(text)) return null;
  const want = text.slice(1).toLowerCase();
  return SLASH_ENTRIES.filter((e) => e.id.startsWith(want) || e.name.toLowerCase().startsWith(want)).map((e) => e.id);
}

/** The slash menu of a box: open while its words ask for it (`slashMatches`), or from a press of its button until the
 *  words change or a tap lands elsewhere; its entries chosen by a click, a tap, or the arrows and a plain Enter; Escape
 *  closes it, from the box or from the button. `button` goes beside the box, `menu` above it, `modal` anywhere (it is
 *  drawn over the page's body, outside any dialog the box is in), and `boxProps` on the box (which entry is chosen, for
 *  a screen reader); `onKeyDown` takes the keys it uses and says so, so that the box's own Enter does not send too. */
export function useSlashMenu(text: string, setText: (t: string) => void) {
  const [pressed, setPressed] = useState(false);
  const [shut, setShut] = useState(false);          // closed by Escape or a tap elsewhere, until the words change
  const [at, setAt] = useState(0);
  const [open, setOpen] = useState<SlashEntry | null>(null);
  const id = useId();
  const menuRef = useRef<HTMLUListElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const typed = slashMatches(text);
  // the words changed: a menu its button opened closes, and one Escape closed may open again
  useEffect(() => { setPressed(false); setShut(false); setAt(0); }, [text]);
  const entries = pressed ? SLASH_ENTRIES.map((e) => e.id) : !shut && typed ? typed : null;
  const shown = entries !== null;
  // a tap or a click anywhere but the menu and its button closes it, as the page's other menus close
  useEffect(() => {
    if (!shown) return;
    const away = (e: MouseEvent) => {
      const t = e.target as Node | null;
      if (t && (menuRef.current?.contains(t) || buttonRef.current?.contains(t))) return;
      setPressed(false);
      setShut(true);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [shown]);
  const chosen = entries ? Math.min(at, Math.max(entries.length - 1, 0)) : 0;
  const optionId = (e: SlashEntry) => `${id}-${e}`;
  const close = () => { setPressed(false); setShut(true); };
  const pick = (entry: SlashEntry) => {
    // the words that called an entry are spent on it; a menu its button opened leaves the box's words as they are
    if (!pressed && typed?.length) setText("");
    setOpen(entry);
    setPressed(false);
  };
  const onKeyDown = (e: KeyboardEvent): boolean => {
    if (!entries) return false;
    // an input method's Enter and arrows choose its words, not an entry; nor does the box send them meanwhile
    if (e.nativeEvent.isComposing || e.keyCode === 229) return true;
    if (e.key === "Escape") {
      e.preventDefault();
      close();
      return true;
    }
    if (entries.length === 0) return false;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setAt((i) => (i + (e.key === "ArrowDown" ? 1 : entries.length - 1)) % entries.length);
      return true;
    }
    if (e.key === "Enter" && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      pick(entries[chosen]);
      return true;
    }
    return false;
  };
  const listed = !!entries?.length;
  const button = (
    <button ref={buttonRef} className="icon-btn h-[30px] w-[30px] flex-none font-mono text-[14px]"
      aria-label="Open the slash menu" aria-haspopup="listbox" aria-expanded={shown}
      aria-controls={listed ? id : undefined}
      title="The slash menu: account, lanes, the agent map, usage, the plan list, background tasks"
      onClick={() => { if (pressed) close(); else { setPressed(true); setShut(false); } }}
      onKeyDown={(e) => { if (e.key === "Escape" && shown) { e.preventDefault(); close(); } }}>/</button>
  );
  const menu = entries && (entries.length === 0
    ? <p role="status" className="mb-1.5 px-1 text-[12px] text-muted">Nothing is called that.</p>
    : (
      <ul ref={menuRef} id={id} role="listbox" aria-label="The slash menu"
        className="mb-1.5 max-w-[420px] rounded-lg border border-line-strong bg-panel py-1 shadow-lg">
        {entries.map((entry, i) => {
          const e = SLASH_ENTRIES.find((x) => x.id === entry)!;
          return (
            <li key={entry} id={optionId(entry)} role="option" aria-selected={i === chosen}
              onMouseDown={(ev) => ev.preventDefault()} onClick={() => pick(entry)}
              className={`cursor-pointer px-3 py-1.5 ${i === chosen ? "bg-active" : "hover:bg-active"}`}>
              <span className="font-mono text-[12.5px]">/{e.id}</span>
              <span className="ml-2 text-[12px] text-muted">{e.hint}</span>
            </li>
          );
        })}
      </ul>
    ));
  // on the box: the list it controls while one shows, and the entry chosen in it, for a screen reader to say
  const boxProps = {
    "aria-autocomplete": "list" as const,
    "aria-controls": listed ? id : undefined,
    "aria-activedescendant": listed && entries ? optionId(entries[chosen]) : undefined,
  };
  const modal = open && <SlashModal entry={open} onClose={() => setOpen(null)} />;
  return { button, menu, modal, boxProps, onKeyDown, open: shown };
}

/** The modal an entry opens: it reads what the page holds (the account also the passkeys' list, the agent map also
 *  the controller's view of who holds the Manager's role) and changes nothing; Escape or a tap outside closes it. It is
 *  drawn over the page's body, outside any dialog its box is in (the Linked sessions panel), so that Escape and a tap
 *  outside close it alone. */
export function SlashModal({ entry, onClose }: { entry: SlashEntry; onClose: () => void }) {
  const title = SLASH_ENTRIES.find((e) => e.id === entry)!.name;
  return createPortal(
    <Modal title={title} onClose={onClose} width={620}>
      {entry === "account" && <AccountView />}
      {entry === "lanes" && <LanesView />}
      {entry === "map" && <MapView />}
      {(entry === "usage" || entry === "plans" || entry === "background") && <ReportedView entry={entry} />}
    </Modal>,
    document.body,
  );
}

const heard = (l: LinkView) => l.last_heard_at ?? l.last_heartbeat_at ?? l.last_seen_at ?? null;
const named = (n: number, names: string[]) => `${n}${n ? `: ${names.join(", ")}` : ""}`;

type Keys = { names: string[]; high: boolean; can: string[] };
type KeyRow = { name?: string | null; fingerprint?: string | null; tier?: string | null };

function AccountView() {
  const user = useStore((s) => s.user);
  const ws = useStore((s) => s.ws);
  const links = useStore((s) => s.links);
  // encryption's view of the account as the page holds it (never read again from here): each session's pins
  const e2e = useE2EStore((s) => s.view);
  const [keys, setKeys] = useState<Keys | null | "failed">(null);
  useEffect(() => {
    let live = true;
    api<{ passkeys: KeyRow[]; high_security?: boolean }>("/api/account/passkeys")
      .then((r) => {
        const high = !!r.high_security;
        // the passkeys that can sign a command, by fingerprint: while high security is on, its hardware keys alone
        const can = r.passkeys.filter((k) => !!k.fingerprint && (!high || k.tier === "hardware"))
          .map((k) => k.fingerprint as string);
        if (live) setKeys({ names: r.passkeys.map((k) => k.name || "unnamed"), high, can });
      })
      .catch(() => { if (live) setKeys("failed"); });
    return () => { live = false; };
  }, []);
  // how many of those each linked session pinned, as it reports its pins (pinsWarning's reading)
  const pinned = (l: LinkView) => {
    if (keys === null || keys === "failed" || !e2e) return "not read here";
    const pins = e2e.links.find((x) => x.link_id === l.id)?.pins;
    return pins?.length ? `${keys.can.filter((f) => pins.includes(f)).length} of ${keys.can.length}` : "not reported";
  };
  // the workspace as it stands: whether this deployment and account have one, where the account's work lives, and
  // whether it runs, its last start's failure said with why
  const space = !ws ? "not read yet" : !ws.enabled ? "off on this deployment"
    : ws.allowed === false ? "not this account's"
    : ws.choice && ws.choice !== "hosted"
      ? `none: the account's work lives in ${ws.choice === "repository" ? "its repository" : "its own machine"}`
    : ws.start_error ? `its last start failed: ${visible(String(ws.start_error))}`
    : ws.starting ? "starting" : ws.running ? "running" : "not running";
  const passkeys = keys === null ? READING : keys === "failed" ? "could not be read just now"
    : named(keys.names.length, keys.names) + (keys.high ? " (high security on)" : "");
  return (
    <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1.5 break-words text-[12.5px]">
      <dt className="text-muted">Signed in</dt>
      <dd>{user ? `${user.email}${user.admin ? " (admin)" : ""}` : "no one"}</dd>
      <dt className="text-muted">Workspace</dt><dd>{space}</dd>
      <dt className="text-muted">Passkeys</dt><dd>{passkeys}</dd>
      <dt className="text-muted">Linked sessions</dt>
      <dd>{links ? named(links.links.length, links.links.map((l) => l.name)) : "not read yet"}</dd>
      {!!links?.links.length && <>
        <dt className="text-muted">Pinned by each</dt>
        <dd>{links.links.map((l) => `${l.name}: ${pinned(l)}`).join("; ")}</dd>
      </>}
    </dl>
  );
}

function LanesView() {
  const links = useStore((s) => s.links);
  const lanes = useMemo(() => [...(links?.links ?? [])].sort((a, b) => (heard(b) ?? 0) - (heard(a) ?? 0)), [links]);
  if (!links) return <p className="text-[12.5px] text-muted">The linked sessions are not read yet.</p>;
  if (lanes.length === 0) return <p className="text-[12.5px] text-muted">No session is linked.</p>;
  return (
    <ul className="space-y-2.5">
      {lanes.map((l) => {
        // its checkpoint's fields as its card shows them (checkpointFields.ts), each character showing itself
        const cp = l.checkpoint && typeof l.checkpoint === "object" ? l.checkpoint : {};
        const list = (v: unknown) => fieldItems(v).map(visible).join(DOT);
        const doing = fieldBlank(cp.doing) ? "" : visible(fieldText(cp.doing));
        const lines: [string, string][] = [["Doing", doing], ["Next", list(cp.next)],
          ["Blocked on", list(cp.waiting_on)], ["Open", list(cp.open)]];
        const at = heard(l);
        return (
          <li key={l.id} aria-label={l.name}
            className="break-words rounded-lg border border-line px-3 py-2 text-[12.5px]">
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="font-medium">{l.name}</span>
              <span className="text-[11.5px] text-muted">
                {l.kind}{at ? `${DOT}heard ${dayTime(at)}` : `${DOT}not heard yet`}{l.silent ? `${DOT}silent` : ""}
              </span>
            </div>
            {lines.filter(([, v]) => v).map(([k, v], i) => (
              <div key={k} className={i === 0 ? "mt-1" : ""}><span className="text-muted">{k}: </span>{v}</div>
            ))}
            {!l.checkpoint && <div className="mt-1 text-muted">No checkpoint yet.</div>}
          </li>
        );
      })}
    </ul>
  );
}

/** The owner's node on the map: no linked session's id is empty. */
const OWNER = "";

/** One edge of the map: from whom to whom (a session's id, or OWNER), named, and what goes along it, in the app's own
 *  words (never a message's). */
export interface Edge { from: string; to: string; fromName: string; toName: string; what: string }

/** The map's edges from what the page holds: the owner's open commands to each session, the proposals one session made
 *  for another, and what waits on the owner from each as Approvals counts it (linkWaiting: its open prompts, its
 *  proposals and its open questions to them or to all). */
export function mapEdges(s: LinkSessions | null | undefined): Edge[] {
  if (!s) return [];
  const name = new Map(s.links.map((l) => [l.id, l.name]));
  const nameOf = (id: string, given?: string | null) => (id === OWNER ? "you" : name.get(id) ?? given ?? "a session");
  const edge = (from: string, to: string, what: string, toGiven?: string | null): Edge =>
    ({ from, to, fromName: nameOf(from), toName: nameOf(to, toGiven), what });
  const out: Edge[] = [];
  for (const l of s.links) {
    const open = (l.commands ?? []).filter((c) => c.state === "queued" || c.state === "taken").length;
    if (open) out.push(edge(OWNER, l.id, `${open} command${open === 1 ? "" : "s"} open`));
  }
  for (const p of s.proposals ?? []) {
    out.push(edge(p.link_id, p.for_id, "a proposal waiting for your signature", p.for));
  }
  for (const l of s.links) {
    const n = linkWaiting(s, l.id);
    if (n) out.push(edge(l.id, OWNER, `${n} waiting on you`));
  }
  return out;
}

type ControllerRead = { controller?: { account?: string | null; state?: string } | null };

function MapView() {
  const links = useStore((s) => s.links);
  // who holds the Manager's role, as the controller's view says: undefined while it is read, null for no one
  const [holder, setHolder] = useState<string | null | undefined>(undefined);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    api<ControllerRead>("/api/ade/controller")
      .then((v) => {
        if (!live) return;
        const c = v.controller;
        setHolder(c?.account ? `the workspace's chat, on ${c.account} (${c.state ?? "off"})` : null);
      })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, []);
  const edges = mapEdges(links);
  // each node by its id, so that a session named "you" is a node of its own
  const nodes = [{ id: OWNER, name: "you", label: "you, the owner" },
    ...(links?.links ?? []).map((l) => ({ id: l.id, name: l.name, label: `the session ${l.name}` }))];
  return (
    <div className="space-y-3 break-words text-[12.5px]">
      <p>
        <span className="text-muted">The Manager: </span>
        {failed ? "could not be read just now" : holder === undefined ? READING : holder ?? "no one holds the role"}
      </p>
      <ul aria-label="The sessions" className="flex flex-wrap gap-2">
        {nodes.map((n) => (
          <li key={n.id} aria-label={n.label} className="rounded-md border border-line px-2.5 py-1">
            {n.name}
            <span className="ml-1.5 text-[11px] text-muted">
              {edges.filter((e) => e.from === n.id || e.to === n.id).length}
            </span>
          </li>
        ))}
      </ul>
      {edges.length === 0 ? <p className="text-muted">Nothing goes between them now.</p> : (
        <ul aria-label="What goes between them" className="space-y-1">
          {edges.map((e, i) => (
            <li key={i}>
              <span className="font-medium">{e.fromName}</span>{TO}
              <span className="font-medium">{e.toName}</span>: {e.what}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const NOT_REPORTED = "not reported";
const TASK_WORDS = new Map([["pending", "to do"], ["in_progress", "doing"], ["completed", "done"]]);
const grouped = (n: number) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
const isText = (x: unknown): x is string => typeof x === "string";
const isCount = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);

/** A report's lists in the clear, as the rules for them read them (reportedLists): those it holds alone. */
function clearLists(r: LinkReported): Record<string, unknown> {
  return { ...(r.tasks !== undefined ? { tasks: r.tasks } : {}),
    ...(r.background !== undefined ? { background: r.background } : {}) };
}

/** Usage, the plan list or the background tasks: each linked session newest first, with what its client last reported
 *  of its own session and when, its lists opened where they came sealed and this tab holds the keys, and "not
 *  reported" for what a client reports nothing of. */
function ReportedView({ entry }: { entry: "usage" | "plans" | "background" }) {
  const links = useStore((s) => s.links);
  const account = useAccount();
  const lanes = useMemo(() => [...(links?.links ?? [])].sort((a, b) => (heard(b) ?? 0) - (heard(a) ?? 0)), [links]);
  const sealed = entry === "usage" ? [] : lanes.flatMap((l) => (l.reported?.sealed
    ? [{ envelope: l.reported.sealed, slot: reportedSlot(account, l.id) }] : []));
  const opener = useOpened(sealed);
  if (!links) return <p className="text-[12.5px] text-muted">The linked sessions are not read yet.</p>;
  if (lanes.length === 0) return <p className="text-[12.5px] text-muted">No session is linked.</p>;
  const lines = (l: LinkView): string[] => {
    const r = l.reported;
    if (!r) return [NOT_REPORTED];
    if (entry === "usage") {
      const u = r.usage ?? {};
      const used = isCount(u.context_used) ? u.context_used : undefined;
      const window = isCount(u.context_window) ? u.context_window : undefined;
      // past the window it was reported with, a count alone: that window was a guess (ade_link.py, _report_from)
      const context = used === undefined ? NOT_REPORTED
        : window && used <= window
          ? `${grouped(used)} of ${grouped(window)} tokens (${Math.round((used / window) * 100)}%)`
        : `${grouped(used)} tokens`;
      const rate = !u.rate_limit ? NOT_REPORTED : u.rate_limit.state === "ok" ? "not limited"
        : `limited${isCount(u.rate_limit.resets_at) ? `, until ${dayTime(u.rate_limit.resets_at)}` : ""}`;
      return [`Context: ${context}`, `Model: ${isText(u.model) ? visible(u.model) : NOT_REPORTED}`,
        `Rate limit: ${rate}`];
    }
    // held whole to the server's rules for them, a sealed report's (which the server cannot read) as a clear one's
    let checked = reportedLists(clearLists(r));
    if (r.sealed) {
      const seal = sealOf(opener.state, opener.look(r.sealed, reportedSlot(account, l.id)));
      if (!("open" in seal)) return [sealWords(seal.seal)];
      checked = reportedLists(seal.open);
    }
    if (!checked.ok) return [refusalWords(checked.refusal)];
    const words = checked.value;
    if (entry === "plans") {
      return words.tasks.length
        ? words.tasks.map((t) => `${TASK_WORDS.get(t.state) ?? visible(t.state)}: ${visible(t.text)}`)
        : [NOT_REPORTED];
    }
    return words.background.length
      ? words.background.map((b) => `${visible(b.kind)} ${visible(b.id)}, ${visible(b.state)}`
        + (b.text ? `: ${visible(b.text)}` : ""))
      : [NOT_REPORTED];
  };
  return (
    <ul className="space-y-2.5">
      {lanes.map((l) => (
        <li key={l.id} aria-label={l.name}
          className="break-words rounded-lg border border-line px-3 py-2 text-[12.5px]">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-medium">{l.name}</span>
            <span className="text-[11.5px] text-muted">
              {l.reported_at ? `reported ${dayTime(l.reported_at)}` : "no report yet"}
            </span>
          </div>
          <ul className="mt-1 space-y-0.5">
            {lines(l).map((line, i) => <li key={i}>{line}</li>)}
          </ul>
        </li>
      ))}
    </ul>
  );
}
