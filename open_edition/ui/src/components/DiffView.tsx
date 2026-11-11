import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Diff, Hunk, getChangeKey, markEdits, parseDiff, tokenize, type FileData } from "react-diff-view";
import { forDiff, languageOf } from "../highlight";
import "react-diff-view/style/index.css";
import { Check, Columns2, Copy, FileMinus2, FilePen, FilePlus2, Loader2, MessageSquarePlus, Rows2, Send, Undo2, X } from "lucide-react";
import { dragPath } from "../drag";
import { diffSections, sectionPaths } from "../diffPaths";

export interface ReviewComment {
  file: string;
  line: number;       // the line number in the new file (old for a deleted line)
  side: "new" | "old";
  text: string;
}

/** A git diff, reviewed the way a pull request is: a file list, the hunks, and comments that go back to the agent. */
/** The files of a diff, as react-diff-view parses them, each named as git wrote it (diffPaths.ts: its parser misreads a
 *  name with a space or in quotes); none for an empty diff, which its parser would give as one file with no name, and
 *  nothing it cannot read. */
export function diffFiles(diff: string): FileData[] {
  if (!diff.trim()) return [];
  try {
    const files = parseDiff(diff, { nearbySequences: "zip" });
    // the parser starts a file at each "diff --git" line, which no line of a hunk can start with
    const sections = diffSections(diff);
    if (sections.length === files.length) {
      files.forEach((f, i) => {
        const names = sectionPaths(sections[i]);
        if (names) [f.oldPath, f.newPath] = names;
        // a file renamed and changed too: the parser calls it changed, and its discard would put back only one name
        if (f.type === "modify" && f.oldPath !== f.newPath && f.oldPath !== "/dev/null" && f.newPath !== "/dev/null") f.type = "rename";
      });
    }
    return files.filter((f) => f.oldPath || f.newPath);
  } catch {
    return [];
  }
}

/** A review as one message for an agent: each comment on its file and line. */
export function reviewText(comments: ReviewComment[]): string {
  return "Review of your changes. Please address each comment:\n\n" +
    comments.map((c) => `- ${c.file}:${c.line}${c.side === "old" ? " (a removed line)" : ""}: ${c.text}`).join("\n");
}

export function DiffView(props: {
  diff: string;
  /** Send a review to the session's agent; true once it has reached it. Absent: the agent cannot take one here (it is
   *  not running, or is not one a review can be typed to), and the comments are kept, to copy. */
  onSendReview?: (comments: ReviewComment[]) => Promise<boolean>;
  header?: ReactNode;
  /** Put a file back as it was where the session started; a rename gives both its paths. */
  onDiscard?: (paths: string[]) => Promise<void>;
  /** The session whose changes these are: a file's name can then be dragged onto its agent (drag.ts). */
  session?: string;
}) {
  const files = useMemo(() => diffFiles(props.diff), [props.diff]);
  // split only where there is room for two columns of code, unless the person chose; narrow panes get unified
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1200);
  const [chosen, setChosen] = useState<"split" | "unified" | null>(null);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const narrow = width < 760;
  const view = chosen ?? (width >= 1100 ? "split" : "unified");
  const setView = setChosen;
  const [active, setActive] = useState<string | null>(null);
  const [comments, setComments] = useState<ReviewComment[]>([]);
  const [reviewSaid, setReviewSaid] = useState<"" | "sending" | "sent" | "copied">("");
  const sendReview = async () => {
    if (!props.onSendReview) return;
    setReviewSaid("sending");
    const ok = await props.onSendReview(comments);
    if (ok) setComments([]);                // kept when it did not reach the agent, to send again
    setReviewSaid(ok ? "sent" : "");
  };
  const copyReview = async () => {
    try { await navigator.clipboard.writeText(reviewText(comments)); setReviewSaid("copied"); } catch { setReviewSaid(""); }
  };
  const [draft, setDraft] = useState<{ file: string; key: string; line: number; side: "new" | "old"; text: string } | null>(null);
  // the line number a comment was opened from: the focus goes back to it when the comment is added or let go
  const from = useRef<string | null>(null);
  const backToLine = () => {
    const label = from.current;
    setTimeout(() => { if (label) document.querySelector<HTMLElement>(`[data-diff-line][aria-label="${CSS.escape(label)}"]`)?.focus(); }, 0);
  };

  const stats = files.map((f) => {
    let added = 0, removed = 0;
    for (const h of f.hunks) for (const c of h.changes) c.type === "insert" ? added++ : c.type === "delete" ? removed++ : 0;
    return { path: pathOf(f), type: f.type, added, removed };
  });
  const totals = stats.reduce((a, s) => ({ added: a.added + s.added, removed: a.removed + s.removed }), { added: 0, removed: 0 });

  if (!files.length) {
    return <div ref={box} className="flex h-full items-center justify-center text-[12.5px] text-muted">No changes yet.</div>;
  }

  return (
    <div ref={box} className="flex h-full min-h-0">
      <aside className={`${narrow && comments.length === 0 ? "hidden" : "flex"} w-60 flex-none flex-col border-r border-line bg-panel`}>
        <div className="flex h-9 items-center justify-between border-b border-line px-3">
          <span className="text-[12px] text-fg-dim">
            {files.length} {files.length === 1 ? "file" : "files"}{" "}
            <span className="font-mono"><span className="text-done">+{totals.added}</span> <span className="text-fail">−{totals.removed}</span></span>
          </span>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto py-1">
          {stats.map((s) => (
            <button
              key={s.path}
              onClick={() => { setActive(s.path); document.getElementById(anchor(s.path))?.scrollIntoView({ block: "start" }); }}
              className={`flex w-full items-center gap-2 px-3 py-1 text-left text-[12px] ${active === s.path ? "bg-active text-fg" : "text-fg-dim hover:bg-hover"}`}
              title={s.path}
            >
              <FileIcon type={s.type} />
              <span className="min-w-0 flex-1 truncate font-mono text-[11.5px]">{shortPath(s.path)}</span>
              <span className="flex-none font-mono text-[10.5px]"><span className="text-done">+{s.added}</span> <span className="text-fail">−{s.removed}</span></span>
            </button>
          ))}
        </div>
        {comments.length > 0 && (
          <div className="border-t border-line p-3">
            <div className="mb-2 text-[12px] text-fg-dim">{comments.length} {comments.length === 1 ? "comment" : "comments"} in this review</div>
            {props.onSendReview ? (
              <button className="btn btn-outline w-full justify-center" disabled={reviewSaid === "sending"} onClick={() => void sendReview()}>
                {reviewSaid === "sending" ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />} Send to the agent
              </button>
            ) : (
              <>
                <p className="mb-2 text-[11.5px] text-muted">This session&apos;s agent cannot take a review here: it is not running, or it is not Claude Code, Codex or a chat. Copy it, and give it to the agent yourself.</p>
                <button className="btn w-full justify-center" onClick={() => void copyReview()}>
                  {reviewSaid === "copied" ? <Check size={13} className="text-done" /> : <Copy size={13} />} {reviewSaid === "copied" ? "Copied" : "Copy as text"}
                </button>
              </>
            )}
          </div>
        )}
        {comments.length === 0 && reviewSaid === "sent" && (
          <div className="border-t border-line p-3 text-[12px] text-done" role="status"><Check size={13} className="mr-1 inline" />Sent to the agent as one review.</div>
        )}
      </aside>

      <div className="min-w-0 flex-1 overflow-y-auto" onScroll={() => {}} onKeyDown={moveAmongLines}>
        <div className="sticky top-0 z-10 flex h-9 items-center gap-2 overflow-hidden border-b border-line bg-bg/95 px-3 backdrop-blur">
          {narrow && <span className="flex-none text-[12px] text-fg-dim">{files.length} {files.length === 1 ? "file" : "files"} <span className="font-mono"><span className="text-done">+{totals.added}</span> <span className="text-fail">−{totals.removed}</span></span></span>}
          {!narrow && props.header}
          <div className="ml-auto flex rounded-md border border-line-strong p-0.5">
            <button className={`flex h-6 items-center gap-1 rounded px-2 text-[11.5px] ${view === "unified" ? "bg-active text-fg" : "text-muted"}`} onClick={() => setView("unified")}>
              <Rows2 size={12} /> Unified
            </button>
            <button className={`flex h-6 items-center gap-1 rounded px-2 text-[11.5px] ${view === "split" ? "bg-active text-fg" : "text-muted"}`} onClick={() => setView("split")}>
              <Columns2 size={12} /> Split
            </button>
          </div>
        </div>

        {files.map((f) => {
          const path = pathOf(f);
          const tokens = safeTokens(f);
          const widgets: Record<string, ReactNode> = {};
          for (const c of comments.filter((c) => c.file === path)) {
            const key = keyFor(f, c.line, c.side);
            if (key) widgets[key] = <CommentCard text={c.text} onRemove={() => setComments(comments.filter((x) => x !== c))} />;
          }
          if (draft && draft.file === path) {
            widgets[draft.key] = (
              <CommentBox
                value={draft.text}
                onChange={(text) => setDraft({ ...draft, text })}
                onCancel={() => { setDraft(null); backToLine(); }}
                onSave={() => { if (draft.text.trim()) setComments([...comments, { file: path, line: draft.line, side: draft.side, text: draft.text.trim() }]); setDraft(null); backToLine(); }}
              />
            );
          }
          return (
            <section key={path} id={anchor(path)} className="border-b border-line">
              <div className="sticky top-9 z-[5] flex h-8 items-center gap-2 border-b border-line bg-panel px-3 text-[12px]">
                <FileIcon type={f.type} />
                {props.session && f.type !== "delete" ? (
                  <span className="cursor-grab truncate font-mono text-[11.5px] text-fg" draggable title={`${path}: drag it onto the agent to type its path`}
                    onDragStart={(ev) => dragPath(ev, props.session!, path)}>{path}</span>
                ) : <span className="truncate font-mono text-[11.5px] text-fg">{path}</span>}
                {f.type === "rename" && <span className="truncate font-mono text-[11px] text-faint">from {f.oldPath}</span>}
                {props.onDiscard && (
                  <DiscardButton
                    what={f.type === "add" ? "Remove this new file" : f.type === "delete" ? "Bring this file back" : "Put this file back as it was"}
                    onDiscard={() => props.onDiscard!(f.type === "rename" ? [f.newPath, f.oldPath] : [path])}
                  />
                )}
              </div>
              {f.hunks.length === 0 ? (
                <div className="px-4 py-3 text-[12px] text-muted">{f.isBinary ? "Binary file" : "No text changes"}</div>
              ) : (
                <Diff
                  viewType={view}
                  diffType={f.type}
                  hunks={f.hunks}
                  tokens={tokens}
                  widgets={widgets}
                  className="ade-diff"
                  gutterType="default"
                  renderGutter={({ change, side, renderDefault }) => {
                    // a line is commented on from one of its numbers: a deleted line's old one, any other's new one;
                    // a button, so the keyboard reaches it too (one stop per file, the arrows from line to line)
                    const target = change.type === "delete" ? "old" : "new";
                    if (side !== target) return renderDefault();
                    const line = change.type === "delete" ? change.lineNumber : change.type === "insert" ? change.lineNumber : change.newLineNumber;
                    const first = getChangeKey(change) === firstKey(f);
                    return (
                      <button type="button" className="diff-comment" data-diff-line tabIndex={first ? 0 : -1}
                        aria-label={`Comment on ${side === "old" ? "the removed " : ""}line ${line} of ${path}`}
                        onClick={(e) => { from.current = e.currentTarget.getAttribute("aria-label"); setDraft({ file: path, key: getChangeKey(change), line, side: target, text: "" }); }}>
                        {renderDefault()}
                      </button>
                    );
                  }}
                >
                  {(hunks) => hunks.map((h) => <Hunk key={h.content} hunk={h} />)}
                </Diff>
              )}
            </section>
          );
        })}
        <div className="flex items-center gap-1.5 px-4 py-4 text-[11.5px] text-faint">
          <MessageSquarePlus size={13} /> {props.onSendReview ? "Click a line number (or Tab to one, then the arrows) to comment; comments go to the agent as one review."
            : "Click a line number (or Tab to one, then the arrows) to comment; the comments make one review, to copy for the agent."}
        </div>
      </div>
    </div>
  );
}

/** The first line of a file's diff, by its change key: the one stop the keyboard makes in the file's line numbers. */
function firstKey(f: FileData): string | null {
  const c = f.hunks[0]?.changes[0];
  return c ? getChangeKey(c) : null;
}

/** The arrows move between the line numbers one comments from, as between rows; Home and End to a file's first and last. */
export function moveAmongLines(e: React.KeyboardEvent<HTMLElement>) {
  const at = e.target as HTMLElement;
  if (!at.matches?.("[data-diff-line]")) return;
  const file = at.closest("section");
  const all = Array.from((file || e.currentTarget).querySelectorAll<HTMLElement>("[data-diff-line]"));
  const i = all.indexOf(at);
  const to = e.key === "ArrowDown" ? i + 1 : e.key === "ArrowUp" ? i - 1 : e.key === "Home" ? 0 : e.key === "End" ? all.length - 1 : null;
  if (to === null || to < 0 || to >= all.length) return;
  e.preventDefault();
  all[to].focus();
}

function pathOf(f: FileData) {
  return f.type === "delete" ? f.oldPath : f.newPath;
}

function anchor(path: string) {
  return "diff-" + path.replace(/[^a-zA-Z0-9]+/g, "-");
}

function shortPath(path: string) {
  const parts = path.split("/");
  return parts.length > 3 ? `…/${parts.slice(-2).join("/")}` : path;
}

const HIGHLIGHT_LINES = 4000;     // a file's diff longer than this is shown without colour, to stay quick

function safeTokens(f: FileData) {
  try {
    // Word-level marks only where a line was changed, not added: on a pure insertion they light the whole line.
    const paired = f.hunks.some((h) => h.changes.some((c) => c.type === "delete")) && f.hunks.some((h) => h.changes.some((c) => c.type === "insert"));
    const enhancers = paired ? [markEdits(f.hunks, { type: "line" })] : [];
    const language = languageOf(pathOf(f));
    const size = f.hunks.reduce((n, h) => n + h.changes.length, 0);
    if (language && size <= HIGHLIGHT_LINES) {
      return tokenize(f.hunks, { highlight: true, refractor: forDiff as never, language, enhancers });
    }
    return paired ? tokenize(f.hunks, { enhancers }) : undefined;
  } catch {
    return undefined;
  }
}

function keyFor(f: FileData, line: number, side: "new" | "old") {
  for (const h of f.hunks) {
    for (const c of h.changes) {
      if (side === "old" && c.type === "delete" && c.lineNumber === line) return getChangeKey(c);
      if (side === "new" && c.type === "insert" && c.lineNumber === line) return getChangeKey(c);
      if (side === "new" && c.type === "normal" && c.newLineNumber === line) return getChangeKey(c);
    }
  }
  return null;
}

function FileIcon({ type }: { type: FileData["type"] }) {
  if (type === "add") return <FilePlus2 size={13} className="flex-none text-done" />;
  if (type === "delete") return <FileMinus2 size={13} className="flex-none text-fail" />;
  return <FilePen size={13} className="flex-none text-wait" />;
}

function CommentCard({ text, onRemove }: { text: string; onRemove: () => void }) {
  return (
    <div className="m-2 flex items-start gap-2 rounded-md border border-line-strong bg-raised px-3 py-2 font-sans text-[12.5px]">
      <span className="flex-1 whitespace-pre-wrap text-fg">{text}</span>
      <button className="icon-btn !h-5 !w-5" onClick={onRemove} title="Remove"><X size={12} /></button>
    </div>
  );
}

function CommentBox(props: { value: string; onChange: (v: string) => void; onSave: () => void; onCancel: () => void }) {
  return (
    <div className="m-2 rounded-md border border-accent/50 bg-raised p-2 font-sans">
      <textarea aria-label="Your comment on this line"
        autoFocus
        rows={3}
        className="input"
        placeholder="What should change here?"
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) props.onSave();
          if (e.key === "Escape") props.onCancel();
        }}
      />
      <div className="mt-2 flex justify-end gap-2">
        <button className="btn btn-sm btn-ghost" onClick={props.onCancel}>Cancel</button>
        <button className="btn btn-sm btn-outline" onClick={props.onSave}>Add comment</button>
      </div>
    </div>
  );
}

/** A file's change thrown away, after a second click: the file goes back to how it was where the session started. */
function DiscardButton({ what, onDiscard }: { what: string; onDiscard: () => Promise<void> }) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 4000);
    return () => clearTimeout(t);
  }, [armed]);
  return (
    <button
      className={`ml-auto flex h-6 flex-none items-center gap-1 rounded px-2 text-[11.5px] ${armed ? "bg-fail/15 text-fail" : "text-muted hover:bg-hover hover:text-fg"}`}
      title={`${what}: where the session started. Its commits stay; the next Finish takes the file as it is.`}
      disabled={busy}
      onClick={async () => {
        if (!armed) { setArmed(true); return; }
        setBusy(true);
        try { await onDiscard(); } finally { setBusy(false); setArmed(false); }
      }}
    >
      {busy ? <Loader2 size={12} className="animate-spin" /> : <Undo2 size={12} />} {armed ? "Click again to discard" : "Discard"}
    </button>
  );
}
