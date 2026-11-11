import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { ImageOff, Loader2 } from "lucide-react";
import { worktreeImage } from "../api";
import { inWorktree } from "../preview";
import { MarkdownCode } from "./MarkdownCode";
import { OutLink } from "./OutLink";

/** An image of the worktree, fetched with the account's token and shown from memory (the page's policy allows blob:
 *  images, which only its own scripts make); what went wrong, in its place, when it cannot be shown. */
export function WorktreeImage({ session, path, alt, className, onSize }: {
  session: string; path: string; alt?: string; className?: string; onSize?: (w: number, h: number) => void;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const stop = new AbortController();
    let made: string | null = null;
    setUrl(null); setError("");
    worktreeImage(session, path, stop.signal).then((blob) => {
      made = URL.createObjectURL(blob);
      setUrl(made);
    }, (e) => { if (!stop.signal.aborted) setError(e instanceof Error ? e.message : String(e)); });
    return () => { stop.abort(); if (made) URL.revokeObjectURL(made); };
  }, [session, path]);
  if (error) {
    return <span className="inline-flex items-center gap-1.5 text-[12px] text-muted"><ImageOff size={13} /> {alt ? `${alt}: ` : ""}{error}</span>;
  }
  if (!url) return <Loader2 size={14} className="inline animate-spin text-muted" />;
  return (
    <img
      src={url}
      alt={alt || path}
      className={className}
      onLoad={(e) => onSize?.(e.currentTarget.naturalWidth, e.currentTarget.naturalHeight)}
      onError={() => setError("The browser could not show it.")}
    />
  );
}

/** A PDF of the worktree, in the browser's own viewer: fetched as an image is, with the account's token, and always
 *  typed as a PDF by the page itself, whatever the reply says, so that its bytes are never a page of the app's. */
export function PdfView({ session, path, size }: { session: string; path: string; size?: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const stop = new AbortController();
    let made: string | null = null;
    setUrl(null); setError("");
    worktreeImage(session, path, stop.signal).then((blob) => {
      made = URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
      setUrl(made);
    }, (e) => { if (!stop.signal.aborted) setError(e instanceof Error ? e.message : String(e)); });
    return () => { stop.abort(); if (made) URL.revokeObjectURL(made); };
  }, [session, path]);
  if (error) return <div className="p-4 text-[12.5px] text-fail">{error}</div>;
  if (!url) return <div className="flex items-center gap-2 p-4 text-[12.5px] text-muted"><Loader2 size={13} className="animate-spin" /> Reading…</div>;
  return (
    <div className="flex flex-col">
      <iframe src={url} title={`${path}, a PDF`} className="h-[75vh] min-h-[320px] w-full border-0" />
      {size && <div className="border-t border-line px-3 py-1 text-[11.5px] text-faint">{size}</div>}
    </div>
  );
}

/** An image file, at its own size up to the pane's, on a checkerboard so transparency shows, with its dimensions. */
export function ImageView({ session, path, size }: { session: string; path: string; size?: string }) {
  const [dims, setDims] = useState<string>("");
  return (
    <div className="flex min-h-full flex-col items-center justify-center gap-3 p-6">
      <div className="max-w-full rounded-md border border-line p-px [background-image:repeating-conic-gradient(var(--color-hover)_0_25%,transparent_0_50%)] [background-size:16px_16px]">
        <WorktreeImage session={session} path={path} className="block max-h-[70vh] max-w-full" onSize={(w, h) => setDims(`${w} × ${h}`)} />
      </div>
      <div className="text-[11.5px] text-faint">{[dims, size].filter(Boolean).join(" · ")}</div>
    </div>
  );
}

/** A Markdown file rendered: its links into the worktree open there (another file, in the Files tab), its links to
 *  another site open in a new tab and any other is shown as text (OutLink), and its images in the worktree are shown;
 *  an image from another site is named, not fetched, so reading a README does not tell another site who read it. Raw
 *  HTML in it stays out, as in a chat. */
export function MarkdownView({ session, path, text, onOpen }: { session: string; path: string; text: string; onOpen: (path: string) => void }) {
  const open = useRef(onOpen);
  open.current = onOpen;
  // made once a file: a new set of components each render would be new kinds of element to React, which would
  // remount every image (and fetch it again) whenever the page re-renders
  const components = useMemo<Components>(() => ({
    code: MarkdownCode,
    a: ({ href, children }) => {
      const target = href ? inWorktree(path, href) : null;
      if (target) {
        return <a href="#" onClick={(e) => { e.preventDefault(); open.current(target); }} title={target}>{children}</a>;
      }
      if (href && href.startsWith("#")) return <span className="text-accent">{children}</span>;
      return <OutLink href={href}>{children}</OutLink>;
    },
    img: ({ src, alt }) => {
      const target = typeof src === "string" ? inWorktree(path, src) : null;
      if (target) return <WorktreeImage session={session} path={target} alt={alt || undefined} className="inline max-w-full" />;
      return <span className="text-[12px] text-muted" title={typeof src === "string" ? src : undefined}>[{alt || "an image from another site"}]</span>;
    },
  }), [session, path]);
  return (
    <div className="markdown mx-auto max-w-[860px] px-6 py-5 text-[13.5px] leading-[1.65] text-fg">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{text}</ReactMarkdown>
    </div>
  );
}

/** Preview or source: the two ways a Markdown or SVG file is shown, side by side in the file's bar. */
export function ShowAs({ value, onChange }: { value: "preview" | "source"; onChange: (v: "preview" | "source") => void }): ReactNode {
  const item = (v: "preview" | "source", label: string) => (
    <button
      className={`h-6 rounded px-2 text-[11.5px] ${value === v ? "bg-active text-fg" : "text-muted hover:text-fg-dim"}`}
      aria-pressed={value === v}
      onClick={() => onChange(v)}
    >
      {label}
    </button>
  );
  return <span className="flex flex-none items-center gap-0.5 rounded-md border border-line p-0.5">{item("preview", "Preview")}{item("source", "Source")}</span>;
}
