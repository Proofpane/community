import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, CaseSensitive, ChevronDown, ChevronRight, CircleAlert, File, FileSymlink, Folder, FolderOpen, FolderTree, Loader2, Pencil, Regex, RefreshCw, Save, Search } from "lucide-react";
import { Api, ApiError, type FileEntry, type FileView } from "../api";
import type { EditorHandle } from "./CodeEditor";
import { ErrorBoundary } from "./ErrorBoundary";
import { ImageView, MarkdownView, PdfView, ShowAs } from "./Preview";
import { isImage, isMarkdown, isPdf } from "../preview";
import { highlightLines, languageOf } from "../highlight";
import { byFile, matcher, segments, type Match } from "../search";
import { useStore } from "../store";
import { draftKey, drafts } from "../drafts";
import { dragPath } from "../drag";

type Picked = { path: string; line?: number; at: number };

const CodeEditor = lazy(() => import("./CodeEditor").then((m) => ({ default: m.CodeEditor })));


type Found = { query: string; matchCase: boolean; regex: boolean; matches: Match[]; truncated: boolean };

/** The session's worktree, read only: a tree that opens as you go, a search of its text, and the file you pick, at
 *  the line a match is on. On a narrow pane (a phone) it is one column: the list, then the file with a way back. */
export function FilesPanel({ session }: { session: string }) {
  const [folders, setFolders] = useState<Record<string, FileEntry[] | "loading" | string>>({});
  const [open, setOpen] = useState<Record<string, boolean>>({ "": true });
  const [picked, setPicked] = useState<Picked | null>(null);
  const [file, setFile] = useState<FileView | null>(null);
  const [fileError, setFileError] = useState("");
  const [mode, setMode] = useState<"tree" | "search">("tree");
  const [query, setQuery] = useState("");
  const [matchCase, setMatchCase] = useState(false);
  const [regex, setRegex] = useState(false);
  const [found, setFound] = useState<Found | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const searchInput = useRef<HTMLInputElement>(null);
  const asked = useRef(0);
  const reading = useRef("");      // the file last asked for: an answer for an earlier one is not shown

  const load = useCallback(async (path: string) => {
    setFolders((f) => ({ ...f, [path]: "loading" }));
    try {
      const r = await Api.files(session, path);
      setFolders((f) => ({ ...f, [path]: r.entries }));
    } catch (e) {
      setFolders((f) => ({ ...f, [path]: e instanceof Error ? e.message : String(e) }));
    }
  }, [session]);

  useEffect(() => {
    setFolders({});
    setOpen({ "": true });
    setPicked(null);
    setFile(null);
    setFound(null);
    reading.current = "";
    void load("");
  }, [session, load]);

  // a file picked is read afresh each time (the agent may have changed it); the same file stays shown meanwhile, and
  // an answer for a file picked before another is let go
  const pick = useCallback(async (path: string, line?: number) => {
    setPicked({ path, line, at: Date.now() });
    reading.current = path;
    setFile((f) => (f && f.path === path ? f : null));
    setFileError("");
    try {
      const got = await Api.file(session, path);
      if (reading.current === path) setFile(got);
    } catch (e) {
      if (reading.current === path) setFileError(e instanceof Error ? e.message : String(e));
    }
  }, [session]);

  const run = useCallback(async (q: string, c: boolean, r: boolean) => {
    if (!q.trim()) { ++asked.current; setFound(null); setSearchError(""); setSearching(false); return; }
    const mine = ++asked.current;
    setSearching(true);
    setSearchError("");
    try {
      const got = await Api.search(session, q, c, r);
      if (mine === asked.current) setFound({ query: q, matchCase: c, regex: r, matches: got.matches, truncated: got.truncated });
    } catch (e) {
      if (mine === asked.current) { setFound(null); setSearchError(e instanceof Error ? e.message : String(e)); }
    } finally {
      if (mine === asked.current) setSearching(false);
    }
  }, [session]);

  // typing searches once it pauses; Enter searches at once
  useEffect(() => {
    if (mode !== "search") return;
    const t = setTimeout(() => void run(query, matchCase, regex), 350);
    return () => clearTimeout(t);
  }, [query, matchCase, regex, mode, run]);

  // what the palette asked for: a file, its folders opened down to it; or the search, with a query
  const wanted = useStore((st) => st.fileFor[session]);
  const tookFile = useStore((st) => st.tookFile);
  useEffect(() => {
    if (!wanted) return;
    tookFile(session);
    if ("search" in wanted) {
      setMode("search");
      if (wanted.search) setQuery(wanted.search);
      setTimeout(() => searchInput.current?.focus(), 0);
      return;
    }
    const parts = wanted.path.split("/");
    const parents = parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join("/"));
    setMode("tree");
    setOpen((o) => ({ ...o, ...Object.fromEntries(parents.map((p) => [p, true])) }));
    for (const p of parents) void load(p);
    void pick(wanted.path, wanted.line);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted, session]);

  // a path opened from elsewhere (a link in a Markdown file): its folders opened down to it, and it picked
  const openTo = (path: string, line?: number) => {
    const parts = path.split("/");
    const parents = parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join("/"));
    setOpen((o) => ({ ...o, ...Object.fromEntries(parents.map((p) => [p, true])) }));
    for (const p of parents) if (folders[p] === undefined) void load(p);
    void pick(path, line);
  };

  const toggle = (path: string) => {
    const now = !open[path];
    setOpen({ ...open, [path]: now });
    if (now && folders[path] === undefined) void load(path);
  };

  const renderFolder = (path: string, depth: number) => {
    const entries = folders[path];
    if (entries === undefined || entries === "loading") {
      return <div className="flex items-center gap-2 py-1 text-[12px] text-muted" style={{ paddingLeft: 12 + depth * 14 }}><Loader2 size={12} className="animate-spin" /> Reading…</div>;
    }
    if (typeof entries === "string") return <div className="py-1 text-[12px] text-fail" style={{ paddingLeft: 12 + depth * 14 }}>{entries}</div>;
    if (!entries.length) return <div className="py-1 text-[12px] text-faint" style={{ paddingLeft: 12 + depth * 14 }}>Empty</div>;
    return entries.map((e) => {
      const full = path ? `${path}/${e.name}` : e.name;
      if (e.type === "dir") {
        return (
          <div key={full}>
            <button className="flex w-full items-center gap-1.5 py-[3px] pr-2 text-left text-[12.5px] text-fg-dim hover:bg-hover" style={{ paddingLeft: 8 + depth * 14 }} onClick={() => toggle(full)}
              draggable onDragStart={(ev) => dragPath(ev, session, full)}>
              {open[full] ? <ChevronDown size={12} className="text-faint" /> : <ChevronRight size={12} className="text-faint" />}
              {open[full] ? <FolderOpen size={13} className="text-accent/80" /> : <Folder size={13} className="text-accent/80" />}
              <span className="truncate">{e.name}</span>
            </button>
            {open[full] && renderFolder(full, depth + 1)}
          </div>
        );
      }
      return (
        <button
          key={full}
          className={`flex w-full items-center gap-1.5 py-[3px] pr-2 text-left text-[12.5px] ${picked?.path === full ? "bg-active text-fg" : "text-fg-dim hover:bg-hover"}`}
          style={{ paddingLeft: 26 + depth * 14 }}
          onClick={() => pick(full)}
          title={`${full}: drag it onto the agent to type its path`}
          draggable
          onDragStart={(ev) => dragPath(ev, session, full)}
        >
          {e.type === "link" ? <FileSymlink size={13} className="flex-none text-muted" /> : <File size={13} className="flex-none text-muted" />}
          <span className="truncate">{e.name}</span>
        </button>
      );
    });
  };

  const modeButton = (m: "tree" | "search", label: string, icon: ReactNode) => (
    <button
      className={`flex h-6 items-center gap-1 rounded px-1.5 text-[11.5px] ${mode === m ? "bg-active text-fg" : "text-muted hover:text-fg-dim"}`}
      onClick={() => { setMode(m); if (m === "search") setTimeout(() => searchInput.current?.focus(), 0); }}
      aria-pressed={mode === m}
    >
      {icon} {label}
    </button>
  );

  return (
    <div className="flex h-full min-h-0">
      <aside className={`flex w-72 flex-none flex-col border-r border-line bg-panel @max-2xl:w-full @max-2xl:border-r-0 ${picked ? "@max-2xl:hidden" : ""}`}>
        <div className="flex h-9 flex-none items-center gap-1 border-b border-line px-2">
          {modeButton("tree", "Files", <FolderTree size={12} />)}
          {modeButton("search", "Search", <Search size={12} />)}
          <span className="ml-auto" />
          {mode === "tree" && (
            <button className="icon-btn !h-6 !w-6" title="Read again" onClick={() => { setFolders({}); void load(""); for (const p of Object.keys(open)) if (p && open[p]) void load(p); }}>
              <RefreshCw size={12} />
            </button>
          )}
        </div>
        {mode === "tree" ? (
          <div className="min-h-0 flex-1 overflow-auto py-1">{renderFolder("", 0)}</div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col">
            <form className="flex flex-none items-center gap-1 border-b border-line p-2" onSubmit={(e) => { e.preventDefault(); void run(query, matchCase, regex); }}>
              <input
                ref={searchInput}
                className="input h-7 min-w-0 flex-1 !py-0 font-mono text-[12px]"
                placeholder="Text in the files"
                aria-label="Search the files' text"
                value={query}
                maxLength={200}
                onChange={(e) => setQuery(e.target.value)}
                autoCapitalize="off" autoCorrect="off" spellCheck={false}
              />
              <button type="button" className={`icon-btn !h-7 !w-7 ${matchCase ? "!bg-active !text-accent" : ""}`} title="Match case" aria-pressed={matchCase} onClick={() => setMatchCase(!matchCase)}><CaseSensitive size={14} /></button>
              <button type="button" className={`icon-btn !h-7 !w-7 ${regex ? "!bg-active !text-accent" : ""}`} title="A regular expression (POSIX extended)" aria-pressed={regex} onClick={() => setRegex(!regex)}><Regex size={14} /></button>
            </form>
            <SearchResults found={found} searching={searching} error={searchError} picked={picked} onPick={(p, l) => void pick(p, l)} />
          </div>
        )}
      </aside>
      <div className={`min-w-0 flex-1 ${picked ? "" : "@max-2xl:hidden"}`}>
        {!picked ? (
          <div className="flex h-full items-center justify-center text-[12.5px] text-muted">{mode === "search" ? "Pick a match to read its file there." : "Pick a file to read it."}</div>
        ) : (
          <OpenFile
            key={picked.path}
            session={session}
            picked={picked}
            file={file}
            error={fileError}
            onFile={(f) => { if (f.path === reading.current) setFile(f); }}
            onOpen={(p) => openTo(p)}
            onBack={() => { setPicked(null); reading.current = ""; setFile(null); }}
          />
        )}
      </div>
    </div>
  );
}

/** The file picked: read, or edited and saved over the version it was opened from. A save the file changed under
 *  (the agent wrote it meanwhile) is refused by the workspace and said here, with the choice of reloading it or saving
 *  over it; edits left unsaved are kept as a draft when the person moves away. */
function OpenFile({ session, picked, file, error, onFile, onOpen, onBack }: {
  session: string; picked: Picked; file: FileView | null; error: string; onFile: (f: FileView) => void;
  onOpen: (path: string) => void; onBack: () => void;
}) {
  const key = draftKey(session, picked.path);
  const image = isImage(picked.path);
  const markdown = isMarkdown(picked.path);
  // a Markdown file or an SVG is shown rendered, with its source a click away; a search's match shows the source
  const [showAs, setShowAs] = useState<"preview" | "source">(picked.line ? "source" : "preview");
  const [editing, setEditing] = useState<{ text: string; clean?: string; base: string } | null>(null);
  const [gen, setGen] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [discard, setDiscard] = useState(false);
  const editor = useRef<EditorHandle>(null);
  const letGo = useRef(false);      // a reload or a discard: what the editor holds is not kept as a draft
  const refresh = useStore((s) => s.refresh);

  // a draft left from before opens in the editor, unsaved
  useEffect(() => {
    const d = drafts.get(key);
    if (d && file?.editable && !editing) setEditing({ text: d.text, clean: file.text ?? "", base: d.base });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file, key]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const leave = useCallback((text: string, unsaved: boolean) => {
    if (letGo.current) { letGo.current = false; return; }
    if (unsaved && editing) drafts.set(key, { text, base: editing.base });
    else drafts.delete(key);                // back where the file is: no draft to come back to
  }, [key, editing]);

  const edit = () => {
    if (!file?.editable || !file.sha) return;
    setSaveError(""); setConflict(false);
    setEditing({ text: file.text ?? "", base: file.sha });
  };

  const save = async (over = false) => {
    if (!editing || !editor.current || saving) return;
    const text = editor.current.text();
    setSaving(true); setSaveError(""); setDiscard(false);
    try {
      let base = editing.base;
      if (over) {
        const now = await Api.file(session, picked.path);
        if (!now.editable || !now.sha) throw new Error("The file is no longer one the page can edit.");
        base = now.sha;
      }
      const got = await Api.saveFile(session, picked.path, text, base);
      setEditing((e) => e && { ...e, base: got.sha || e.base });
      editor.current?.saved();
      editor.current?.focus();          // back to the text, after a save from a button
      drafts.delete(key);
      setConflict(false);
      onFile(got);
      void refresh();
    } catch (e) {
      if (e instanceof ApiError && e.status === 412) setConflict(true);   // the file changed since it was opened
      else setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  // what the file holds now, the edits let go
  const reload = async () => {
    setSaveError(""); setDiscard(false);
    try {
      const now = await Api.file(session, picked.path);
      letGo.current = true;
      drafts.delete(key);
      setConflict(false);
      onFile(now);
      setEditing(now.editable && now.sha ? { text: now.text ?? "", base: now.sha } : null);
      setGen((g) => g + 1);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    }
  };

  // Done, or with unsaved edits Discard, asked twice
  const close = () => {
    if (dirty && !discard) { setDiscard(true); return; }
    letGo.current = true;
    drafts.delete(key);
    setEditing(null); setDirty(false); setDiscard(false); setConflict(false); setSaveError("");
  };

  return (
    <div className="flex h-full min-w-0 flex-col">
      <div className="flex h-9 flex-none items-center gap-2 border-b border-line bg-bg px-3">
        <button className="icon-btn !h-6 !w-6 hidden @max-2xl:inline-flex" title="Back to the list" onClick={onBack}><ArrowLeft size={14} /></button>
        <span className="min-w-0 truncate font-mono text-[12px] text-fg-dim">{picked.path}{picked.line && !editing ? `:${picked.line}` : ""}</span>
        {editing && dirty && <span className="flex-none text-[11px] text-wait">unsaved</span>}
        <span className="ml-auto" />
        {saveError && <span className="min-w-0 truncate text-[11.5px] text-fail" title={saveError}>{saveError}</span>}
        {!editing && file && (markdown || (image && file.text != null)) && <ShowAs value={showAs} onChange={setShowAs} />}
        {!editing && file && <span className="flex-none text-[11px] text-faint">{formatSize(file.size)}</span>}
        {!editing && file?.editable && (
          <button className="btn btn-sm flex-none" onClick={edit} title="Edit this file in the worktree"><Pencil size={12} /> Edit</button>
        )}
        {editing && (
          <>
            <button className={`btn btn-sm flex-none ${discard ? "!border-fail/60 !text-fail" : ""}`} disabled={saving} onClick={close}>
              {dirty ? (discard ? "Discard the changes?" : "Discard") : "Done"}
            </button>
            <button className="btn btn-sm btn-outline flex-none" disabled={!dirty || saving} onClick={() => void save()} title="Save (Cmd-S, or Ctrl-S)">
              {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />} Save
            </button>
          </>
        )}
      </div>
      {conflict && (
        <div className="flex flex-none flex-wrap items-center gap-2 border-b border-wait/40 bg-wait/[0.08] px-3 py-1.5 text-[12px] text-fg-dim">
          <CircleAlert size={13} className="flex-none text-wait" />
          <span>The file changed since you opened it: the agent may have written it.</span>
          <span className="ml-auto" />
          <button className="btn btn-sm" onClick={() => void reload()}>Reload it, letting your edits go</button>
          <button className="btn btn-sm btn-outline" onClick={() => void save(true)}>Save over it</button>
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-auto">
        {editing ? (
          <ErrorBoundary what="The editor">
          <Suspense fallback={<div className="flex items-center gap-2 p-4 text-[12.5px] text-muted"><Loader2 size={13} className="animate-spin" /> Opening the editor…</div>}>
            <CodeEditor
              key={gen}
              text={editing.text}
              clean={editing.clean}
              path={picked.path}
              line={picked.line}
              onDirty={setDirty}
              onSave={() => void save()}
              onLeave={leave}
              handle={editor}
            />
          </Suspense>
          </ErrorBoundary>
        ) : error ? <div className="p-4 text-[12.5px] text-fail">{error}</div>
          : !file ? <div className="flex items-center gap-2 p-4 text-[12.5px] text-muted"><Loader2 size={13} className="animate-spin" /> Reading…</div>
          : file.link ? <div className="p-4 text-[12.5px] text-muted">A link. The page names it and does not follow it.</div>
          : isPdf(picked.path) ? <PdfView session={session} path={picked.path} size={formatSize(file.size)} />
          : image && (file.binary || file.too_large || showAs === "preview") ? <ImageView session={session} path={picked.path} size={formatSize(file.size)} />
          : markdown && showAs === "preview" && file.text != null ? <MarkdownView session={session} path={picked.path} text={file.text} onOpen={onOpen} />
          : file.binary ? <div className="p-4 text-[12.5px] text-muted">A binary file.</div>
          : file.too_large ? <div className="p-4 text-[12.5px] text-muted">Too large to show here ({formatSize(file.size)}).</div>
          : <Code text={file.text || ""} path={picked.path} line={picked.line} at={picked.at} />}
      </div>
    </div>
  );
}

function SearchResults({ found, searching, error, picked, onPick }: {
  found: Found | null; searching: boolean; error: string; picked: Picked | null; onPick: (path: string, line: number) => void;
}) {
  const groups = useMemo(() => (found ? byFile(found.matches) : []), [found]);
  const re = useMemo(() => (found ? matcher(found.query, found.matchCase, found.regex) : null), [found]);
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  if (error) return <div className="p-3 text-[12px] text-fail">{error}</div>;
  if (!found) {
    return <div className="p-3 text-[12px] text-faint">{searching ? <><Loader2 size={12} className="mr-1.5 inline animate-spin" /> Searching…</> : "The files git sees here, committed or new, and not what .gitignore leaves out."}</div>;
  }
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="flex items-center gap-1.5 px-3 py-1.5 text-[11.5px] text-muted">
        {searching && <Loader2 size={11} className="animate-spin" />}
        {found.matches.length ? `${found.matches.length}${found.truncated ? "+" : ""} ${found.matches.length === 1 ? "line" : "lines"} in ${groups.length} ${groups.length === 1 ? "file" : "files"}` : "Nothing found."}
        {found.truncated && <span className="text-faint">(the first {found.matches.length}: narrow it to see the rest)</span>}
      </div>
      {groups.map((g) => (
        <div key={g.path}>
          <button className="flex w-full items-center gap-1.5 px-2 py-[3px] text-left text-[12px] text-fg-dim hover:bg-hover" onClick={() => setClosed({ ...closed, [g.path]: !closed[g.path] })} title={g.path}>
            {closed[g.path] ? <ChevronRight size={12} className="flex-none text-faint" /> : <ChevronDown size={12} className="flex-none text-faint" />}
            <File size={12} className="flex-none text-muted" />
            <span className="truncate">{g.path}</span>
            <span className="ml-auto flex-none rounded bg-active px-1 font-mono text-[10px] text-fg-dim">{g.matches.length}</span>
          </button>
          {!closed[g.path] && g.matches.map((m) => {
            const on = picked?.path === m.path && picked.line === m.line;
            return (
              <button
                key={m.line}
                className={`flex w-full items-baseline gap-2 py-[2px] pl-7 pr-2 text-left font-mono text-[11.5px] ${on ? "bg-active text-fg" : "text-fg-dim hover:bg-hover"}`}
                onClick={() => onPick(m.path, m.line)}
              >
                <span className="w-8 flex-none text-right text-muted">{m.line}</span>
                <span className="min-w-0 truncate">
                  {segments(m.text.trimStart().slice(0, 240), re).map((s, i) => (s.hit ? <mark key={i} className="rounded-sm bg-accent/25 text-fg">{s.text}</mark> : <span key={i}>{s.text}</span>))}
                </span>
              </button>
            );
          })}
        </div>
      ))}
    </div>
  );
}

const HIGHLIGHT_BYTES = 300_000;   // a larger file is shown without colour, to stay quick

function Code({ text, path, line, at }: { text: string; path: string; line?: number; at?: number }) {
  const lines = text.split("\n");
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  const coloured = useMemo(() => {
    const language = languageOf(path);
    if (!language || text.length > HIGHLIGHT_BYTES) return null;
    try { return highlightLines(text, language); } catch { return null; }
  }, [text, path]);
  const target = useRef<HTMLTableRowElement>(null);
  // the line a match is on, brought into view each time it is picked
  useEffect(() => { if (line) target.current?.scrollIntoView({ block: "center" }); }, [line, at, text]);
  return (
    <table className="w-full border-collapse font-mono text-[12px] leading-[1.6]">
      <tbody>
        {lines.map((l, i) => (
          <tr key={i} ref={line === i + 1 ? target : undefined} className={line === i + 1 ? "bg-accent/15" : "hover:bg-panel"}>
            <td className={`w-12 select-none border-r border-line pr-3 text-right align-top ${line === i + 1 ? "text-accent" : "text-faint"}`}>{i + 1}</td>
            <td className="whitespace-pre-wrap break-words pl-3 pr-4 align-top text-fg">
              {coloured?.[i]?.length ? coloured[i].map((seg, j) => (seg.cls ? <span key={j} className={seg.cls}>{seg.text}</span> : seg.text)) : l || " "}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function formatSize(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
