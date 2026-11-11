import { useEffect, useRef, useState, type DragEvent } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { SearchAddon, type ISearchOptions } from "@xterm/addon-search";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { ChevronDown, ChevronUp, Paperclip, Search, X } from "lucide-react";
import { CONTROL, droppedPath, hasPath } from "../drag";
import { useTheme } from "../theme";

/** The bytes a terminal pane is attached to: a stream out, a way in, and the size it should be. */
export interface TerminalLink {
  attach(io: { write: (data: string | Uint8Array) => void; status: (text: string) => void }): {
    send: (data: string) => void;
    resize: (cols: number, rows: number) => void;
    close: () => void;
  };
}

const LIGHT = {
  background: "#ffffff",
  foreground: "#1f2328",
  cursor: "#0891b2",
  cursorAccent: "#ffffff",
  selectionBackground: "#0891b233",
  black: "#24292f", red: "#cf222e", green: "#116329", yellow: "#7d4e00", blue: "#0969da", magenta: "#8250df", cyan: "#1b7c83", white: "#6e7781",
  brightBlack: "#57606a", brightRed: "#a40e26", brightGreen: "#1a7f37", brightYellow: "#633c01", brightBlue: "#218bff", brightMagenta: "#a475f9", brightCyan: "#3192aa", brightWhite: "#8c959f",
};

const THEME = {
  background: "#030712",
  foreground: "#f3f4f6",
  cursor: "#22d3ee",
  cursorAccent: "#030712",
  selectionBackground: "#22d3ee44",
  black: "#1f2937", red: "#fb7185", green: "#34d399", yellow: "#fbbf24", blue: "#60a5fa", magenta: "#c084fc", cyan: "#22d3ee", white: "#d1d5db",
  brightBlack: "#6b7280", brightRed: "#fda4af", brightGreen: "#6ee7b7", brightYellow: "#fcd34d", brightBlue: "#93c5fd", brightMagenta: "#d8b4fe", brightCyan: "#67e8f9", brightWhite: "#ffffff",
};

const FIND: ISearchOptions = {
  decorations: { matchBackground: "#22d3ee33", activeMatchBackground: "#22d3ee99", matchOverviewRuler: "#22d3ee66", activeMatchColorOverviewRuler: "#22d3ee" },
};
// ⌘F on a Mac; elsewhere Ctrl+Shift+F, since Ctrl+F belongs to the shell (forward a character)
const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const isFind = (e: KeyboardEvent) => e.type === "keydown" && e.key.toLowerCase() === "f" && (isMac ? e.metaKey && !e.ctrlKey : e.ctrlKey && e.shiftKey);

// keys a phone's keyboard does not have, as the bytes a terminal sends for them, and each key's name for a screen reader
const KEYS: [string, string, string][] = [["Esc", "\x1b", "Escape"], ["Tab", "\t", "Tab"], ["^C", "\x03", "Control C"],
  ["↑", "\x1b[A", "Up arrow"], ["↓", "\x1b[B", "Down arrow"], ["←", "\x1b[D", "Left arrow"], ["→", "\x1b[C", "Right arrow"],
  ["⏎", "\r", "Return"]];

/** A session's terminal; `readOnly` shows what an ended program printed, taking no input. */
/** Whether a terminal draws with WebGL: yes, but under automation (navigator.webdriver, where headless Chrome drew a
 *  blank terminal and no context loss was reported), when the page asks for "?gl=0", or without WebGL2. "?gl" turns it
 *  on even under automation, to test it. */
export function webglWanted(search: string, nav: { webdriver?: boolean }, canvas?: () => { getContext(kind: string): unknown }): boolean {
  const asked = new URLSearchParams(search).get("gl");
  if (asked === "0") return false;
  if (asked === null && nav.webdriver) return false;
  try {
    const make = canvas || (() => document.createElement("canvas"));
    return !!make().getContext("webgl2");
  } catch {
    return false;
  }
}

/** A path as a shell reads it: as it is when it holds nothing a shell treats specially, else in single quotes; after
 *  "./" when it starts with "-", which a program would read as an option; and without any control character, which no
 *  quoting keeps a terminal from acting on (drag.ts refuses a path holding one before it gets here). */
export function shellWord(path: string): string {
  const clean = path.replace(new RegExp(CONTROL.source, "g"), "");
  const word = clean.startsWith("-") ? `./${clean}` : clean;
  return /^[\w./@%+=:,-]+$/.test(word) ? word : `'${word.replace(/'/g, `'\\''`)}'`;
}

export function TerminalPane({ link, focus, readOnly, onFiles, session }: {
  link: TerminalLink; focus?: boolean; readOnly?: boolean;
  /** Files dropped, pasted or attached: put in the worktree, their paths given back to be typed for the program. */
  onFiles?: (files: File[]) => Promise<string[]>;
  /** The session whose worktree the program works in: a file or folder of it dragged here from the page has its path
   *  typed (drag.ts). */
  session?: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [dropping, setDropping] = useState<"" | "files" | "path">("");
  const [sending, setSending] = useState("");
  const picker = useRef<HTMLInputElement>(null);
  const take = useRef<(files: File[]) => void>(() => {});
  take.current = (files: File[]) => {
    if (!onFiles || readOnly || !files.length) return;
    setSending(files.length === 1 ? `Putting ${files[0].name || "the file"} in the worktree…` : `Putting ${files.length} files in the worktree…`);
    onFiles(files).then((paths) => {
      // typed as a paste, so a program that asked for bracketed pastes is told it is one
      termRef.current?.paste(paths.map(shellWord).join(" ") + " ");
      termRef.current?.focus();
      setSending("");
    }, (e) => {
      setSending(e instanceof Error ? e.message : String(e));
      setTimeout(() => setSending(""), 6000);
    });
  };
  const sendRef = useRef<((d: string) => void) | null>(null);
  const searchRef = useRef<SearchAddon | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const theme = useTheme((t) => t.theme);
  const [finding, setFinding] = useState(false);
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<{ index: number; count: number } | null>(null);
  const findInput = useRef<HTMLInputElement>(null);
  const touch = typeof matchMedia !== "undefined" && matchMedia("(pointer: coarse)").matches;

  useEffect(() => {
    const term = new Terminal({
      fontFamily: getComputedStyle(document.body).getPropertyValue("--font-mono") || "Menlo, monospace",
      fontSize: 13,
      lineHeight: 1.25,
      cursorBlink: !readOnly,
      disableStdin: !!readOnly,
      scrollback: 10000,
      allowProposedApi: true,
      theme: useTheme.getState().theme === "light" ? LIGHT : THEME,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon());
    const search = new SearchAddon();
    term.loadAddon(search);
    searchRef.current = search;
    termRef.current = term;
    const results = search.onDidChangeResults((r) => setFound(r.resultCount ? { index: r.resultIndex, count: r.resultCount } : { index: -1, count: 0 }));
    term.attachCustomKeyEventHandler((e) => {
      if (!isFind(e)) return true;
      e.preventDefault();
      setFinding(true);
      setTimeout(() => findInput.current?.select(), 0);
      return false;
    });
    term.open(host.current!);
    // a file pasted (a screenshot) goes to the worktree and its path is typed, before xterm.js sees the paste
    const onPaste = (e: ClipboardEvent) => {
      const files = Array.from(e.clipboardData?.files || []);
      if (!files.length || readOnly) return;
      e.preventDefault();
      e.stopPropagation();
      take.current(files);
    };
    const hostEl = host.current!;
    hostEl.addEventListener("paste", onPaste, true);
    // WebGL draws an agent's busy screen far faster than the DOM, and a lost context falls back to the DOM. It is left
    // off where it drew a blank terminal without saying so (headless Chrome, under automation: webglWanted), and "?gl=0"
    // turns it off by hand.
    if (webglWanted(location.search, navigator)) import("@xterm/addon-webgl").then(({ WebglAddon }) => {
      try {
        const gl = new WebglAddon();
        gl.onContextLoss(() => gl.dispose());
        term.loadAddon(gl);
      } catch { /* the DOM renderer stays */ }
    });
    const conn = link.attach({
      write: (d) => term.write(d),
      status: (t) => term.write(`\r\n\x1b[2m${t}\x1b[0m\r\n`),
    });
    const sub = term.onData((d) => { if (!readOnly) conn.send(d); });
    sendRef.current = (d) => conn.send(d);
    const doFit = () => {
      try {
        fit.fit();
        conn.resize(term.cols, term.rows);
      } catch { /* not laid out yet */ }
    };
    const ro = new ResizeObserver(() => requestAnimationFrame(doFit));
    ro.observe(host.current!);
    doFit();
    if (focus) term.focus();
    return () => {
      hostEl.removeEventListener("paste", onPaste, true);
      ro.disconnect();
      sub.dispose();
      results.dispose();
      conn.close();
      term.dispose();
      searchRef.current = null;
      termRef.current = null;
    };
  }, [link, focus, readOnly]);

  useEffect(() => {
    if (termRef.current) termRef.current.options.theme = theme === "light" ? LIGHT : THEME;
  }, [theme]);

  const find = (next: boolean, text = query) => {
    const search = searchRef.current;
    if (!search) return;
    if (!text) { search.clearDecorations(); setFound(null); return; }
    if (next) search.findNext(text, FIND);
    else search.findPrevious(text, FIND);
  };
  const closeFind = () => {
    setFinding(false);
    searchRef.current?.clearDecorations();
    setFound(null);
    termRef.current?.focus();
  };
  const findBar = finding && (
    <div className="absolute right-3 top-2 z-10 flex items-center gap-1 rounded-lg border border-line-strong bg-raised px-2 py-1 shadow-xl shadow-black/50">
      <Search size={13} className="text-muted" />
      <input aria-label="Find in the terminal"
        ref={findInput}
        autoFocus
        className="h-6 w-44 bg-transparent text-[12.5px] text-fg outline-none placeholder:text-faint"
        placeholder="Find in the terminal"
        value={query}
        onChange={(e) => { setQuery(e.target.value); find(true, e.target.value); }}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); find(!e.shiftKey); }
          else if (e.key === "Escape") { e.preventDefault(); closeFind(); }
        }}
      />
      <span className="min-w-12 text-right font-mono text-[11px] text-faint">
        {found ? (found.count ? `${found.index + 1}/${found.count}` : "none") : ""}
      </span>
      <button className="icon-btn !h-6 !w-6" title="Previous (Shift+Enter)" onClick={() => find(false)}><ChevronUp size={13} /></button>
      <button className="icon-btn !h-6 !w-6" title="Next (Enter)" onClick={() => find(true)}><ChevronDown size={13} /></button>
      <button className="icon-btn !h-6 !w-6" title="Close (Esc)" onClick={closeFind}><X size={13} /></button>
    </div>
  );

  const files = !!onFiles && !readOnly;
  const paths = !!session && !readOnly;
  const isFiles = (e: DragEvent) => Array.from(e.dataTransfer.types).includes("Files");
  const dropTarget = files || paths ? {
    onDragOver: (e: DragEvent) => {
      const what = files && isFiles(e) ? "files" : paths && hasPath(e) ? "path" : "";
      if (what) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; setDropping(what); }
    },
    onDragLeave: (e: DragEvent) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDropping(""); },
    onDrop: (e: DragEvent) => {
      if (files && isFiles(e)) { e.preventDefault(); setDropping(""); take.current(Array.from(e.dataTransfer.files)); }
      else if (paths && hasPath(e)) {
        e.preventDefault();
        setDropping("");
        const got = droppedPath(e, session!);
        if (got && "path" in got) {
          // typed as a paste, as a dropped file's path is
          termRef.current?.paste(shellWord(got.path) + " ");
          termRef.current?.focus();
        } else if (got) {
          setSending(got.refused);
          setTimeout(() => setSending(""), 6000);
        }
      }
    },
  } : {};
  const overlays = (
    <>
      {findBar}
      {dropping && (
        <div className="pointer-events-none absolute inset-2 z-20 flex items-center justify-center rounded-lg border-2 border-dashed border-accent/70 bg-bg/70 text-[13px] text-fg">
          {dropping === "path" ? "Drop to type its path" : "Drop to put it in the worktree and type its path"}
        </div>
      )}
      {sending && (
        <div className="absolute bottom-3 left-1/2 z-20 max-w-[90%] -translate-x-1/2 truncate rounded-md border border-line-strong bg-raised px-3 py-1.5 text-[12px] text-fg-dim shadow-xl">{sending}</div>
      )}
    </>
  );

  if (!touch || readOnly) {
    return (
      <div className="relative h-full w-full" {...dropTarget}>
        <div ref={host} className="h-full w-full overflow-hidden" />
        {overlays}
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col">
      <div className="relative min-h-0 w-full flex-1" {...dropTarget}>
        <div ref={host} className="h-full w-full overflow-hidden" />
        {overlays}
      </div>
      <KeyBar onKey={(bytes) => sendRef.current?.(bytes)} onAttach={files ? () => picker.current?.click() : undefined} />
      {files && <input ref={picker} type="file" multiple hidden onChange={(e) => { take.current(Array.from(e.target.files || [])); e.target.value = ""; }} />}
    </div>
  );
}

/** Under a terminal on a touch screen: the keys a phone's keyboard does not have, and attaching a file. They share the
 *  one row, each as wide as the screen leaves it (about 38 pixels on a 390-pixel phone) and 44 tall, so none runs off
 *  the screen to be scrolled to. A tap on one keeps the phone's keyboard where it is (the focus stays in the terminal). */
export function KeyBar({ onKey, onAttach }: { onKey: (bytes: string) => void; onAttach?: () => void }) {
  const key = "btn btn-sm h-11 min-w-0 flex-1 justify-center px-0 text-[14px]";
  return (
    <div role="toolbar" aria-label="Keys" className="flex flex-none gap-1 border-t border-line bg-panel px-2 py-1.5" style={{ paddingBottom: "max(6px, env(safe-area-inset-bottom, 0px))" }}>
      {KEYS.map(([label, bytes, name]) => (
        <button key={label} className={`${key} font-mono`} aria-label={name} onMouseDown={(e) => e.preventDefault()} onClick={() => onKey(bytes)}>{label}</button>
      ))}
      {onAttach && (
        <button className={key} title="Attach a file: it goes to the worktree and its path is typed" aria-label="Attach a file"
          onMouseDown={(e) => e.preventDefault()} onClick={onAttach}>
          <Paperclip size={14} />
        </button>
      )}
    </div>
  );
}
