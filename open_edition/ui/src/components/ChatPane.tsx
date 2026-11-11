import { useCallback, useDeferredValue, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { MarkdownCode } from "./MarkdownCode";
import { OutLink } from "./OutLink";

// a fenced block coloured as the file viewer colours it; a link to another site opened beside the page, and any
// other shown as text (OutLink: one into this page could open its device or app-consent pane with a code filled in)
const CHAT_MARKDOWN = { code: MarkdownCode, a: OutLink };
import { ArrowUp, Check, CircleAlert, Loader2, Mic, Paperclip, ShieldAlert, Square, Wrench, X } from "lucide-react";
import { LANGS, NOTE, useVoice } from "../voice";
import { ALLOW_TOOL_CALL, Api, type ChatEvent, type SessionRecord } from "../api";
import { useStore } from "../store";
import { droppedPath, hasPath } from "../drag";
import { shown, type Live } from "../live";
import { words, type Chosen } from "../chatSettings";
import { usd } from "./bits";
import { ChatSettingsBar } from "./ChatSettings";
import { McpDialog, heldCount } from "./McpDialog";
import { useSlashMenu } from "./SlashMenu";
import { CameBack, TakenLine, useActs, type Acts } from "./Acting";
import { confirmPresence } from "../presence";

type Item =
  | { type: "user"; seq: number; text: string }
  | { type: "text"; seq: number; text: string }
  | { type: "tool"; seq: number; id: string; name: string; summary: string; end?: { error: boolean; summary: string }; ask?: Extract<Item, { type: "ask" }> }
  | { type: "ask"; seq: number; id: string; name: string; summary: string; forSession: boolean; decision?: string }
  | { type: "result"; seq: number; cost: number | null; ms?: number; turns?: number; error: boolean }
  | { type: "error"; seq: number; text: string }
  | { type: "note"; seq: number; text: string }
  | { type: "question"; seq: number; text: string }
  | { type: "queued"; seq: number; text: string };

const s = (v: unknown) => (typeof v === "string" ? v : "");

/** The chat log, folded: a tool and its result are one row, an ask and its answer one card. */
export function fold(events: ChatEvent[]): Item[] {
  const out: Item[] = [];
  const tools = new Map<string, Extract<Item, { type: "tool" }>>();
  const asks = new Map<string, Extract<Item, { type: "ask" }>>();
  for (const e of events) {
    switch (e.kind) {
      case "user": out.push({ type: "user", seq: e.seq, text: s(e.text) }); break;
      case "text": {
        const last = out[out.length - 1];
        if (last && last.type === "text") last.text += "\n\n" + s(e.text);
        else out.push({ type: "text", seq: e.seq, text: s(e.text) });
        break;
      }
      case "tool": {
        const t = { type: "tool" as const, seq: e.seq, id: s(e.id), name: s(e.name), summary: s(e.summary) };
        tools.set(t.id, t);
        out.push(t);
        break;
      }
      case "tool_end": {
        const t = tools.get(s(e.id));
        if (t) t.end = { error: !!e.error, summary: s(e.summary) };
        break;
      }
      case "ask": {
        const a = { type: "ask" as const, seq: e.seq, id: s(e.id), name: s(e.name), summary: s(e.summary), forSession: !!e.for_session };
        asks.set(a.id, a);
        // a question about a tool call goes with that call's row, as one thing; one about nothing logged stands alone
        const tool = tools.get(a.id);
        if (tool) tool.ask = a;
        else out.push(a);
        break;
      }
      case "answered": {
        const a = asks.get(s(e.id));
        if (a) a.decision = s(e.decision);
        break;
      }
      case "result":
        out.push({ type: "result", seq: e.seq, cost: typeof e.cost === "number" ? e.cost : null, ms: e.duration_ms as number | undefined, turns: e.turns as number | undefined, error: !!e.error });
        break;
      case "error": out.push({ type: "error", seq: e.seq, text: s(e.text) }); break;
      case "mcp_change": out.push({ type: "note", seq: e.seq, text: `MCP: ${s(e.change)} ${s(e.name)}` }); break;
      case "settings": out.push({ type: "note", seq: e.seq, text: `Settings: ${words({ model: s(e.model) || undefined, effort: s(e.effort) || undefined, permission_mode: s(e.permission_mode) || undefined } as Chosen)}` }); break;
      case "mcp": {
        const held = Array.isArray(e.held) ? (e.held as unknown[]).filter((x): x is string => typeof x === "string") : [];
        const servers = Array.isArray(e.servers) ? (e.servers as { name?: unknown; status?: unknown }[]).map((x) => `${s(x.name)} (${s(x.status)})`) : [];
        const bits = [e.strict ? `No MCP server runs in this chat: ${s(e.strict)}` : "", servers.length ? `MCP servers: ${servers.join(", ")}` : "",
          held.length ? `held back until you approve them under Connectors: ${held.join(", ")}` : ""].filter(Boolean);
        if (bits.length) out.push({ type: "note", seq: e.seq, text: bits.join("; ") });
        break;
      }
      case "upload": out.push({ type: "note", seq: e.seq, text: `Attached ${s(e.name) || "a file"}` }); break;
      // an agent's run: its question for the owner (its ask_owner tool), and a message kept until its turn ends
      case "question": out.push({ type: "question", seq: e.seq, text: s(e.text) }); break;
      case "queued": out.push({ type: "queued", seq: e.seq, text: s(e.text) }); break;
      default: break;
    }
  }
  return out;
}

/** A chat's pane; an agent's run's (`run`) has its agent's settings and its own MCP servers, so neither bar is shown,
 *  and once it has ended it takes no message. */
export function ChatPane({ record, run }: { record: SessionRecord; run?: { live: boolean } }) {
  const events = useStore((st) => st.chat[record.id]) || [];
  const loadChat = useStore((st) => st.loadChat);
  const setError = useStore((st) => st.setError);
  const items = useMemo(() => fold(events), [events]);
  const live = useStore((st) => st.live[record.id]);
  const [text, setText] = useState("");
  // the slash menu: a `/` at the start of the box, or its button, opens it
  const slash = useSlashMenu(text, setText);
  const [mcpOpen, setMcpOpen] = useState(false);
  const [attaching, setAttaching] = useState(0);
  const [dragging, setDragging] = useState<"" | "files" | "path">("");
  const fileInput = useRef<HTMLInputElement>(null);
  const textRef = useRef(text);
  textRef.current = text;
  const voice = useVoice(useCallback(() => textRef.current, []), setText);
  // a file from this device into the chat's worktree; its path goes into the message, for Claude to read
  const attach = async (files: FileList | File[]) => {
    for (const file of Array.from(files)) {
      setAttaching((n) => n + 1);
      try {
        const out = await Api.upload(record.id, file, true);
        setText((t) => `${t && !t.endsWith("\n") ? `${t}\n` : t}Attached: ${out.path}\n`);
      } catch (e) {
        setError(e);
      } finally {
        setAttaching((n) => n - 1);
      }
    }
  };
  // how many of the repository's servers wait for approval: what claude said at its last start, until the dialog says
  const logged = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const held = events[i].kind === "mcp" ? events[i].held : undefined;
      if (Array.isArray(held)) return held.length;
    }
    return 0;
  }, [events]);
  const [held, setHeld] = useState<number | null>(null);
  const [sending, setSending] = useState(false);
  // a message on its way, from the press to the server's answer: at the chat's end as sending, the box keeping its
  // words until then and the press kept in a ref, so two presses send once; a refusal keeps the words, with the reason
  // under the box
  const [sendingText, setSendingText] = useState<string | null>(null);
  const [sendFailed, setSendFailed] = useState("");
  const pressed = useRef(false);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const busy = record.chat?.state === "running" || record.chat?.state === "needs_you";

  useEffect(() => { void loadChat(record.id); }, [record.id, loadChat]);
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [items.length, events, live, sendingText]);

  const send = async () => {
    const t = text.trim();
    if (!t || pressed.current) return;
    pressed.current = true;
    setSending(true); setSendingText(t); setSendFailed("");
    stick.current = true;
    try {
      await Api.chatSend(record.id, t);
      setText("");
      await loadChat(record.id).catch(() => {});            // its own line, before the sending one goes
    } catch (e) {
      setSendFailed(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false); setSendingText(null); pressed.current = false;
    }
  };

  // a tool ask's answer shows itself taken at once, its buttons disabled, and comes back with the reason if the server
  // does not take it; once taken, the chat's log is read again, and its card then says it as the log
  // has it. A refusal that comes once its card has gone is said as the page's error. Allowing asks the phone for the
  // person first (presence.ts): a press the person does not confirm changes nothing on the card, and is said as the
  // page's error, as before; the server's time starts once they are confirmed.
  const acts = useActs(setError);
  const answer = async (id: string, decision: string) => {
    const allow = decision !== "deny";
    if (allow) {
      try { await confirmPresence(ALLOW_TOOL_CALL); } catch (e) { setError(e); return; }
    }
    void acts.act(chatAskKey(record.id, id), ANSWERED[decision] ?? decision,
                  () => (allow ? Api.chatAnswer(record.id, id, decision, { confirmed: true }) : Api.chatAnswer(record.id, id, decision)),
                  () => loadChat(record.id));
  };

  return (
    <div
      className={`relative flex h-full min-h-0 flex-col ${dragging ? "ring-2 ring-inset ring-accent/60" : ""}`}
      onDragOver={(e) => {
        const what = e.dataTransfer.types.includes("Files") ? "files" : hasPath(e) ? "path" : "";
        if (what) { e.preventDefault(); setDragging(what); }
      }}
      onDragLeave={(e) => { if (e.currentTarget === e.target) setDragging(""); }}
      onDrop={(e) => {
        if (e.dataTransfer.files.length) { e.preventDefault(); setDragging(""); void attach(e.dataTransfer.files); return; }
        if (!hasPath(e)) return;
        e.preventDefault();
        setDragging("");
        // a file or folder of this session's worktree, from its files or changes: its path into the message
        const got = droppedPath(e, record.id);
        if (got && "path" in got) setText((t) => `${t}${t && !/\s$/.test(t) ? " " : ""}${got.path} `);
        else if (got) setError(new Error(got.refused));
      }}
    >
      {dragging && <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-bg/70 text-[13px] text-accent">{dragging === "path" ? "Drop to put its path in the message" : "Drop to attach it to the message"}</div>}
      <div
        ref={scroller}
        className="min-h-0 flex-1 overflow-y-auto"
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
      >
        <div className="mx-auto w-full max-w-[860px] px-5 py-5">
          {items.length === 0 && <div className="py-16 text-center text-[12.5px] text-muted">The conversation starts with the task.</div>}
          {items.map((it) => <Row key={`${it.type}-${it.seq}`} it={it} onAnswer={(id, d) => void answer(id, d)} acts={acts} chat={record.id} />)}
          {sendingText !== null && (
            <div data-sending className="mb-4 mt-2 flex justify-end">
              <div className="max-w-[80%] rounded-xl rounded-br-sm bg-active px-3.5 py-2 text-[13px] text-fg">
                <div className="mb-0.5 flex items-center gap-1.5 text-[11px] text-muted"><Loader2 size={11} className="animate-spin" /> Sending</div>
                <div className="whitespace-pre-wrap">{sendingText}</div>
              </div>
            </div>
          )}
          {live && <Writing live={live} />}
          {record.chat?.state === "running" && !shown(live).any && (
            <div className="mt-3 flex items-center gap-2 text-[12px] text-muted"><Loader2 size={13} className="animate-spin" /> Working…</div>
          )}
        </div>
      </div>
      {run && !run.live ? (
        <div className="flex-none border-t border-line bg-panel px-4 py-3 text-center text-[12px] text-muted">This run has ended: run its agent again for another.</div>
      ) : (
      <div className="flex-none border-t border-line bg-panel px-4 py-3">
        {!run && <ChatSettingsBar record={record} held={held ?? logged} onConnectors={() => setMcpOpen(true)} />}
        {mcpOpen && <McpDialog session={record.id} onClose={() => setMcpOpen(false)} onChange={(v) => setHeld(heldCount(v))} />}
        {slash.modal}
        {slash.menu && <div className="mx-auto w-full max-w-[860px]">{slash.menu}</div>}
        <div className="mx-auto flex w-full max-w-[860px] items-end gap-2">
          <input ref={fileInput} type="file" multiple className="hidden" onChange={(e) => { if (e.target.files?.length) void attach(e.target.files); e.target.value = ""; }} />
          <button className="icon-btn h-[38px] w-[30px] flex-none" title="Attach a file (or paste or drop one): it goes into the worktree, and its path into the message" onClick={() => fileInput.current?.click()} disabled={attaching > 0}>
            {attaching > 0 ? <Loader2 size={15} className="animate-spin" /> : <Paperclip size={15} />}
          </button>
          <span className="flex h-[38px] items-center">{slash.button}</span>
          <textarea aria-label="Message to Claude" {...slash.boxProps}
            rows={Math.min(8, Math.max(1, text.split("\n").length))}
            className="input min-h-[38px] flex-1 !py-2"
            placeholder={voice.listening ? "Listening…" : busy ? "Claude is working; your message goes next" : "Message Claude…"}
            value={text}
            onChange={(e) => { if (voice.listening) voice.cancel(); setText(e.target.value); }}
            onPaste={(e) => { if (e.clipboardData.files.length) { e.preventDefault(); void attach(e.clipboardData.files); } }}
            onKeyDown={(e) => {
              if (slash.onKeyDown(e)) return;
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void send();
              }
            }}
          />
          {voice.available && (
            <button
              className={`icon-btn h-[38px] w-[34px] flex-none ${voice.listening ? "!text-fail" : ""}`}
              aria-pressed={voice.listening}
              aria-label={voice.listening ? "Stop listening" : "Speak instead of typing"}
              title={voice.listening ? "Listening: tap to stop" : `Speak instead of typing. ${NOTE}`}
              onClick={voice.toggle}
            >
              {voice.listening ? <Square size={14} fill="currentColor" className="animate-pulse-dot" /> : <Mic size={16} />}
            </button>
          )}
          {busy ? (
            <button className="btn h-[38px]" onClick={() => Api.chatInterrupt(record.id).catch(setError)} title="Stop this turn">
              <Square size={13} /> Stop
            </button>
          ) : null}
          <button className={`btn h-[38px] w-[38px] justify-center !px-0 ${text.trim() ? "btn-primary" : ""}`} disabled={!text.trim() || sending} onClick={() => void send()} title="Send (Enter)">
            {sending ? <Loader2 size={16} className="animate-spin" /> : <ArrowUp size={16} strokeWidth={2.4} />}
          </button>
        </div>
        {sendFailed && <div role="alert" className="mt-1 text-[12px] text-fail">{sendFailed}</div>}
        {(voice.listening || voice.line) && (
          <div className="mx-auto mt-1.5 flex w-full max-w-[860px] flex-wrap items-center gap-2 text-[11.5px] text-muted" aria-live="polite">
            <span className="flex gap-0.5" role="group" aria-label="The language you speak">
              {LANGS.map(([code, name]) => (
                <button key={code} className={`rounded px-1.5 py-0.5 ${voice.lang === code ? "bg-active text-fg" : "hover:text-fg"}`} aria-pressed={voice.lang === code} onClick={() => voice.choose(code)}>{name}</button>
              ))}
            </span>
            <span className={voice.line ? "text-wait" : ""}>{voice.line || (voice.started ? `Listening: tap again, or pause, to stop. ${NOTE}` : "Starting…")}</span>
          </div>
        )}
      </div>
      )}
    </div>
  );
}

/** What claude is writing, as it writes it, until the log has it: its text so far with a caret, or that it is
 *  thinking, or about to call a tool. */
function Writing({ live }: { live: Live }) {
  const { blocks } = shown(live);
  return (
    <>
      {blocks.map((b, i) => <Streaming key={b.block} text={b.text} caret={i === blocks.length - 1 && !live.thinking && !live.tool} />)}
      {live.thinking && <div className="mb-3 mt-1 text-[12.5px]"><span className="shimmer">Thinking…</span></div>}
      {live.tool && (
        <div className="mb-1 flex items-center gap-2 rounded-md px-2 py-1 font-mono text-[12px] text-fg-dim">
          <Loader2 size={13} className="flex-none animate-spin text-run" />
          <Wrench size={12} className="flex-none text-faint" />
          <span className="text-fg">{live.tool}</span>
          <span className="shimmer font-sans">preparing…</span>
        </div>
      )}
    </>
  );
}

function Streaming({ text, caret }: { text: string; caret: boolean }) {
  const later = useDeferredValue(text);     // markdown is parsed as often as the page can keep up, never more
  return (
    <div className="markdown mb-3 text-[13.5px] leading-[1.6] text-fg" aria-busy="true">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={caret ? [caretAtTheEnd] : []} components={CHAT_MARKDOWN}>{later}</ReactMarkdown>
    </div>
  );
}

interface HastNode { type: string; value?: string; tagName?: string; properties?: Record<string, unknown>; children?: HastNode[] }

/** A rehype step that puts the caret after the last thing written, however deep: in a list's last item, a code block. */
export function caretAtTheEnd() {
  return (tree: HastNode) => {
    let node = tree;
    for (;;) {
      const kids = (node.children || []).filter((c) => c.type === "element" || (c.type === "text" && (c.value || "").trim()));
      const last = kids[kids.length - 1];
      if (!last || last.type !== "element" || last.tagName === "br" || last.tagName === "hr" || last.tagName === "img") break;
      node = last;
    }
    node.children = [...(node.children || []), { type: "element", tagName: "span", properties: { className: ["caret"] }, children: [] }];
  };
}

const DECIDED: Record<string, string> = { allow: "allowed once", allow_session: "allowed for this session", deny: "denied", stop: "stopped" };

/** The key a chat's tool ask is answered by, here and in Approvals (Acting.tsx): one press of it for the page. */
export const chatAskKey = (chat: string, ask: string) => `chat:${chat}:${ask}`;

/** What a tool ask was answered, as its card says it from the press until the chat's log says it. */
const ANSWERED: Record<string, string> = { allow: "Allowed once", allow_session: "Allowed for this session", deny: "Denied" };

/** A question about a tool call: what it would do, and the answers Claude Code offers; once answered, what was said. */
function AskCard({ it, onAnswer, acts, chat }: { it: Extract<Item, { type: "ask" }>; onAnswer: (id: string, d: string) => void; acts: Acts; chat: string }) {
  const key = chatAskKey(chat, it.id);
  const taken = acts.taken(key);
  return (
    <div className={`my-2 rounded-lg border px-3.5 py-3 ${it.decision ? "border-line bg-panel" : "border-wait/60 bg-wait/[0.06]"}`}>
      <div className="mb-1.5 flex items-center gap-2 text-[12.5px]">
        <ShieldAlert size={14} className={it.decision ? "text-muted" : "text-wait"} />
        <span className="font-medium">{it.decision ? "Asked to use" : "Claude wants to use"} <span className="font-mono">{it.name}</span></span>
        {it.decision && <span className="ml-auto text-[11.5px] text-muted">{it.decision === "deny" || it.decision === "stop" ? "Denied" : it.decision === "allow_session" ? "Allowed for this session" : "Allowed once"}</span>}
      </div>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md bg-bg px-3 py-2 font-mono text-[12px] text-fg-dim">{it.summary}</pre>
      {!it.decision && (
        <div className="mt-2.5 flex flex-wrap gap-2">
          <button className="btn btn-outline btn-sm" disabled={!!taken} onClick={() => onAnswer(it.id, "allow")}>Allow once</button>
          {it.forSession && <button className="btn btn-sm" disabled={!!taken} onClick={() => onAnswer(it.id, "allow_session")}>Allow for this session</button>}
          <button className="btn btn-sm btn-danger" disabled={!!taken} onClick={() => onAnswer(it.id, "deny")}>Deny</button>
        </div>
      )}
      {!it.decision && taken && <TakenLine said={taken} sure={acts.sure(key)} />}
      {!it.decision && <CameBack why={acts.failed(key)} />}
    </div>
  );
}

/** A tool call and its result: one line, opened with a click (a phone has no hover) to show what it was given and what
 *  it gave back, as the log keeps them. */
function ToolRow({ it, onAnswer, acts, chat }: { it: Extract<Item, { type: "tool" }>; onAnswer: (id: string, d: string) => void; acts: Acts; chat: string }) {
  const [open, setOpen] = useState(false);
  const waiting = it.ask && !it.ask.decision;
  return (
    <div className="mb-1">
      {waiting && <AskCard it={it.ask!} onAnswer={onAnswer} acts={acts} chat={chat} />}
      <button
        className="flex w-full items-start gap-2 rounded-md px-2 py-1 text-left font-mono text-[12px] text-fg-dim hover:bg-panel"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        title={open ? "Close" : "What it was given and what it gave back"}
      >
        {!it.end ? <Loader2 size={13} className="mt-0.5 flex-none animate-spin text-run" /> : it.end.error ? <X size={13} className="mt-0.5 flex-none text-fail" /> : <Check size={13} className="mt-0.5 flex-none text-done" />}
        <Wrench size={12} className="mt-0.5 flex-none text-faint" />
        <span className="text-fg">{it.name}</span>
        <span className={`min-w-0 flex-1 text-muted ${open ? "whitespace-pre-wrap break-all" : "truncate"}`}>{it.summary}</span>
        {it.ask?.decision && (
          <span className={`flex-none font-sans text-[11px] ${it.ask.decision === "deny" || it.ask.decision === "stop" ? "text-fail" : "text-faint"}`}>
            {DECIDED[it.ask.decision] || it.ask.decision}
          </span>
        )}
      </button>
      {open && (
        <div className={`ml-[42px] mt-0.5 whitespace-pre-wrap break-words rounded-md border px-2.5 py-1.5 font-mono text-[11.5px] ${it.end?.error ? "border-fail/30 text-fail" : "border-line text-fg-dim"}`}>
          {it.end ? it.end.summary || "(it gave back nothing)" : "Still running…"}
        </div>
      )}
    </div>
  );
}

function Row({ it, onAnswer, acts, chat }: { it: Item; onAnswer: (id: string, d: string) => void; acts: Acts; chat: string }) {
  switch (it.type) {
    case "user":
      return (
        <div className="mb-4 mt-2 flex justify-end">
          <div className="max-w-[80%] whitespace-pre-wrap rounded-xl rounded-br-sm bg-active px-3.5 py-2 text-[13px] text-fg">{it.text}</div>
        </div>
      );
    case "text":
      return (
        <div className="markdown mb-3 text-[13.5px] leading-[1.6] text-fg">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={CHAT_MARKDOWN}>{it.text}</ReactMarkdown>
        </div>
      );
    case "tool":
      return <ToolRow it={it} onAnswer={onAnswer} acts={acts} chat={chat} />;
    case "ask":
      return <AskCard it={it} onAnswer={onAnswer} acts={acts} chat={chat} />;
    case "result":
      return (
        <div className="mb-4 mt-1 flex items-center gap-2 text-[11px] text-faint">
          <span className="h-px flex-1 bg-line" />
          {it.error && <span className="text-fail">turn ended with an error</span>}
          {it.cost != null && <span className="font-mono">{usd(it.cost)}</span>}
          {it.ms != null && <span>{(it.ms / 1000).toFixed(1)}s</span>}
          {it.turns != null && <span>{it.turns} {it.turns === 1 ? "step" : "steps"}</span>}
          <span className="h-px flex-1 bg-line" />
        </div>
      );
    case "error":
      return (
        <div className="my-2 flex items-start gap-2 rounded-md border border-fail/40 bg-fail/[0.07] px-3 py-2 text-[12.5px] text-fail">
          <CircleAlert size={14} className="mt-0.5 flex-none" /> <span className="whitespace-pre-wrap">{it.text}</span>
        </div>
      );
    case "note":
      return <div className="my-1 text-center text-[11px] text-faint">{it.text}</div>;
    case "question":
      return (
        <div className="my-3 rounded-lg border border-wait/50 bg-wait/[0.06] px-3.5 py-2.5">
          <div className="mb-1 text-[11px] font-medium text-wait">It asks you</div>
          <div className="whitespace-pre-wrap text-[13px]">{it.text}</div>
          <div className="mt-1 text-[11px] text-muted">Your next message is its answer.</div>
        </div>
      );
    case "queued":
      return <div className="my-1 text-right text-[11px] text-faint">Sent when this turn ends: {it.text}</div>;
  }
}
