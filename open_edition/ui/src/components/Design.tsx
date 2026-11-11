import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Copy, MonitorPlay, MousePointerClick } from "lucide-react";
import { Api, DESKTOP, sendToTerminal, type SessionRecord } from "../api";
import { useStore } from "../store";
import { Modal } from "./Modal";
import { head } from "../text";

// Design mode, in the desktop app (desktop/src-tauri: open_preview, design_pick, picker.js): a session's preview
// window shows the app it is building, served on this Mac, and the element the person picks there comes back here,
// where they say what to change and send both to the session's agent.

/** What the preview window's picker says of an element (picker.js, then lib.rs's clean_pick). */
export interface DesignPick {
  url: string;
  title: string;
  selector: string;
  html: string;
  text: string;
  rect: { x: number; y: number; width: number; height: number };
  viewport: { width: number; height: number };
  styles: Record<string, string>;
}

/** A pick as the shell sends it: `shot` is a PNG of the element as the preview showed it, base64, where the system
 *  gave one. */
export interface PickEvent { session: string; page: string; pick: DesignPick; shot?: string | null }

/** The PNG the shell sent, as bytes the page can show and upload. */
export function shotBlob(base64: string): Blob {
  const raw = atob(base64);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
  return new Blob([bytes], { type: "image/png" });
}

/** Where a message to a session's agent goes, as the review of its changes does: its chat, or the terminal of a
 *  running Claude Code or Codex. Null when there is none to send to. */
export function agentInbox(rec: Pick<SessionRecord, "program" | "running">): "chat" | "terminal" | null {
  if (rec.program === "chat") return "chat";
  return rec.running && (rec.program === "claude" || rec.program === "codex") ? "terminal" : null;
}

/** The message: what the person asks first, then the element as the picker found it, and where its picture was put
 *  in the worktree, if it has one. */
export function designMessage(comment: string, p: DesignPick, picture?: string): string {
  const styles = Object.entries(p.styles).map(([k, v]) => `${k}: ${v}`).join("; ");
  const lines = [
    comment.trim(),
    "",
    `The element, picked in the running app at ${p.url}${p.title ? ` ("${p.title}")` : ""}:`,
    `- CSS path: ${p.selector}`,
    `- box: ${p.rect.width}x${p.rect.height} at (${p.rect.x}, ${p.rect.y}) in a ${p.viewport.width}x${p.viewport.height} window`,
  ];
  if (picture) lines.push(`- a picture of it, as the preview showed it: ${picture}`);
  if (p.text) lines.push(`- its text: "${p.text}"`);
  if (styles) lines.push(`- computed styles: ${styles}`);
  lines.push("- its HTML:", p.html);
  return lines.join("\n");
}

const lastUrlKey = (session: string) => `pp-preview@${session}`;

function lastUrl(session: string): string {
  try { return localStorage.getItem(lastUrlKey(session)) || "http://localhost:3000"; } catch { return "http://localhost:3000"; }
}

async function invoke<T>(cmd: string, args: Record<string, unknown>): Promise<T> {
  return (await import("@tauri-apps/api/core")).invoke<T>(cmd, args);
}

/** An address for a port the app listens on: localhost, as a browser would be pointed at it. */
export function portUrl(port: number): string {
  return `http://localhost:${port}`;
}

/** A session's Preview: the address of the app it serves on this Mac, opened in the preview window. What its own
 *  processes listen on is offered first. */
export function PreviewButton({ rec }: { rec: Pick<SessionRecord, "id"> }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState(() => lastUrl(rec.id));
  const [error, setError] = useState("");
  const [found, setFound] = useState<{ port: number; command: string }[] | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open || !DESKTOP) return;
    let stop = false;
    setFound(null);
    Api.ports(rec.id)
      .then((r) => {
        if (stop) return;
        setFound(r.ports);
        // nothing typed or remembered for this session: its first port is the likely one
        let remembered = false;
        try { remembered = !!localStorage.getItem(lastUrlKey(rec.id)); } catch { /* none */ }
        if (!remembered && r.ports.length) setUrl(portUrl(r.ports[0].port));
      })
      .catch(() => { if (!stop) setFound([]); });
    return () => { stop = true; };
  }, [open, rec.id]);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", away);
    return () => window.removeEventListener("mousedown", away);
  }, [open]);
  if (!DESKTOP) return null;
  const go = async () => {
    setError("");
    try {
      await invoke("open_preview", { session: rec.id, url: url.trim() });
      try { localStorage.setItem(lastUrlKey(rec.id), url.trim()); } catch { /* the default next time */ }
      setOpen(false);
    } catch (e) {
      setError(typeof e === "string" ? e : e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <div className="relative" ref={box}>
      <button className="icon-btn" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        title="Preview the app this session serves on this Mac, and pick an element of it for the agent">
        <MonitorPlay size={14} />
      </button>
      {open && (
        <form className="absolute right-0 top-8 z-40 w-80 rounded-lg border border-line-strong bg-raised p-3 shadow-2xl shadow-black/60"
          onSubmit={(e) => { e.preventDefault(); void go(); }}>
          <label className="label mb-1.5 block" htmlFor={`preview-${rec.id}`}>The app, on this Mac</label>
          <div className="flex gap-1.5">
            <input id={`preview-${rec.id}`} className="input flex-1 font-mono text-[12px]" value={url} autoFocus spellCheck={false}
              onChange={(e) => setUrl(e.target.value)} placeholder="http://localhost:3000" />
            <button className="btn btn-outline">Open</button>
          </div>
          {found && found.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1" aria-label="What the session's app listens on">
              {found.map((f) => (
                <button key={f.port} type="button" className={`rounded border px-1.5 py-0.5 font-mono text-[11px] ${url === portUrl(f.port) ? "border-accent text-accent" : "border-line text-fg-dim hover:border-line-strong"}`}
                  title={`${f.command || "a program"} in this session's worktree`} onClick={() => setUrl(portUrl(f.port))}>
                  :{f.port}{f.command ? ` ${f.command}` : ""}
                </button>
              ))}
            </div>
          )}
          {found && found.length === 0 && <div className="mt-1.5 text-[11.5px] text-faint">Nothing in this session's worktree listens on a port yet.</div>}
          <div className="mt-1.5 text-[11.5px] text-faint">In the preview, Pick an element sends it here, to say what to change.</div>
          {error && <div className="mt-1.5 text-[12px] text-fail">{error}</div>}
        </form>
      )}
    </div>
  );
}

// picks from the preview windows: designPicks.ts, which the shell loads without the rest of this
export { useDesignPicks } from "../designPicks";

/** A pick, shown with what the person wants changed, and sent to the session's agent. */
export function PickDialog({ event, rec, onClose }: { event: PickEvent; rec: SessionRecord | undefined; onClose: () => void }) {
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [showHtml, setShowHtml] = useState(false);
  const setError = useStore((s) => s.setError);
  const p = event.pick;
  const inbox = rec ? agentInbox(rec) : null;
  const name = rec ? (rec.title || rec.task.split("\n")[0]).trim() : "";
  const blob = useMemo(() => (event.shot ? shotBlob(event.shot) : null), [event.shot]);
  const shotUrl = useMemo(() => (blob ? URL.createObjectURL(blob) : null), [blob]);
  useEffect(() => () => { if (shotUrl) URL.revokeObjectURL(shotUrl); }, [shotUrl]);
  const message = designMessage(comment || "Change this element:", p);
  const send = async () => {
    if (!inbox) return;
    setBusy(true);
    try {
      // the picture into the worktree, where the agent can open it (and, for a chat, attached to it); the words go
      // without it if it cannot be put there
      let picture: string | undefined;
      if (blob) {
        try {
          const stamp = new Date().toISOString().replace(/[:.]/g, "-");
          picture = (await Api.upload(event.session, new File([blob], `picked-${stamp}.png`, { type: "image/png" }), inbox === "chat")).path;
        } catch (e) {
          setError(e);
        }
      }
      const text = designMessage(comment, p, picture);
      if (inbox === "terminal") await sendToTerminal(event.session, text);
      else await Api.chatSend(event.session, text);
      onClose();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const styles = Object.entries(p.styles);
  return (
    <Modal title="An element from the preview" onClose={onClose} width={640} footer={
      <>
        <button className="btn" onClick={() => { void navigator.clipboard?.writeText(message); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
          {copied ? <Check size={13} /> : <Copy size={13} />} Copy
        </button>
        <button className="btn" onClick={onClose}>Discard</button>
        <button className="btn btn-outline" disabled={!inbox || busy || !comment.trim()} onClick={() => void send()}
          title={inbox ? undefined : "This session's agent is not running: start it, or copy this"}>
          <MousePointerClick size={13} /> Send to {name ? `"${name.length > 30 ? head(name, 30) + "…" : name}"` : "the agent"}
        </button>
      </>
    }>
      <div className="space-y-3">
        <div className="text-[12px] text-muted">
          From <span className="font-mono text-fg-dim">{p.url}</span>{p.title ? <> ({p.title})</> : null}
        </div>
        <div className="rounded-lg border border-line bg-bg p-3">
          <div className="break-all font-mono text-[12px] text-accent">{p.selector}</div>
          <div className="mt-1 text-[11.5px] text-muted">
            {p.rect.width}x{p.rect.height} at ({p.rect.x}, {p.rect.y}) in a {p.viewport.width}x{p.viewport.height} window
            {p.text ? <> · <span className="text-fg-dim">&ldquo;{head(p.text, 80)}{p.text.length > 80 ? "…" : ""}&rdquo;</span></> : null}
          </div>
          {styles.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {styles.slice(0, 14).map(([k, v]) => (
                <span key={k} className="rounded border border-line px-1.5 py-0.5 font-mono text-[10.5px] text-fg-dim" title={`${k}: ${v}`}>
                  {k}: {v.length > 28 ? head(v, 28) + "…" : v}
                </span>
              ))}
            </div>
          )}
          {shotUrl && (
            <img src={shotUrl} alt="The element, as the preview showed it" className="mt-2 max-h-48 max-w-full rounded border border-line object-contain" />
          )}
          <button className="btn btn-sm mt-2 block" onClick={() => setShowHtml((s) => !s)}>{showHtml ? "Hide" : "Show"} its HTML</button>
          {showHtml && <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-panel p-2 font-mono text-[11px] text-fg-dim">{p.html}</pre>}
        </div>
        <div>
          <label className="label mb-1.5 block" htmlFor="pick-comment">What should change</label>
          <textarea id="pick-comment" className="input min-h-[84px] w-full resize-y py-2 text-[13px]" autoFocus value={comment}
            onChange={(e) => setComment(e.target.value)} placeholder="Make this button full width on a phone, and keep its colour"
            onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && inbox && comment.trim()) { e.preventDefault(); void send(); } }} />
          <div className="mt-1 text-[11.5px] text-faint">
            {inbox === "terminal" ? "Typed into the agent's terminal, with the element below your words."
              : inbox === "chat" ? "Sent as a message in the session's chat, with the element below your words."
              : "This session's agent is not running, so there is nothing to send to: start it, or copy this."}
            {inbox && " ⌘↩ sends."}
          </div>
        </div>
      </div>
    </Modal>
  );
}
