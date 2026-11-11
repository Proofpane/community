import { useEffect, useState } from "react";
import { ArrowLeft, FileText, Loader2 } from "lucide-react";
import { APP, DESKTOP } from "../api";
import { Modal } from "./Modal";

/** A notice a build carries beside its page, and what it is about. */
export interface Notice { file: string; label: string }

export type Build = "web" | "phone" | "desktop";

/** The notices a build of the page carries beside it (open_edition/NOTICE): in every build, Proofpane Community's own
 *  LICENSE and NOTICE, the Apache License 2.0's text for the third-party works under it, and its npm packages' notice
 *  (ui/licenses.ts writes all four); in an app, its Rust crates' and
 *  the Rust standard library's (desktop/licenses.py, run by the phone's build and by desktop/bundle.py); in the
 *  desktop app, its Python's too. */
export function noticesFor(build: Build): Notice[] {
  return [
    { file: "LICENSE", label: "Proofpane Community's license: the Apache License 2.0" },
    { file: "NOTICE", label: "Proofpane Community's notice: what it includes, under whose license" },
    { file: "LICENSE-APACHE-2.0", label: "The Apache License 2.0: the third-party works the notice names are under it" },
    { file: "THIRD_PARTY_LICENSES.txt", label: "The npm packages in these pages, and their licenses" },
    ...(build === "web" ? [] : [
      { file: "THIRD_PARTY_LICENSES-rust.txt", label: "The Rust crates the app is built from, and their licenses" },
      { file: "THIRD_PARTY_LICENSES-rust-std.html", label: "The Rust standard library's copyright notices" },
    ]),
    ...(build === "desktop" ? [{ file: "THIRD_PARTY_LICENSES-python.txt", label: "The Python the app carries, its packages, and their licenses" }] : []),
  ];
}

/** This build's: a browser's, the phone app's or the desktop app's. */
export const NOTICES = noticesFor(DESKTOP ? "desktop" : APP ? "phone" : "web");

/** A notice's text as the dialog shows it: an HTML one (the Rust standard library's, as its toolchain writes it) as
 *  its text, read without running anything in it. */
export function noticeText(file: string, body: string): string {
  if (!file.endsWith(".html")) return body;
  const doc = new DOMParser().parseFromString(body, "text/html");
  doc.querySelectorAll("script, style").forEach((e) => e.remove());
  return (doc.body?.textContent ?? "").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}

/** The licenses of what this build of the app carries: each notice it has beside its page, and the one chosen as text.
 *  `base` is where the page's own files are (Vite's base: /app/ in a browser, / in an app). */
export function Licenses({ onClose, notices = NOTICES, base = import.meta.env.BASE_URL }: { onClose: () => void; notices?: Notice[]; base?: string }) {
  const [open, setOpen] = useState<Notice | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    let stop = false;
    setText(null);
    setError("");
    fetch(`${base}${open.file}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`the app answered ${r.status}`);
        const body = await r.text();
        if (!stop) setText(noticeText(open.file, body));
      })
      .catch((e: unknown) => { if (!stop) setError(`${open.file} could not be read: ${e instanceof Error ? e.message : String(e)}.`); });
    return () => { stop = true; };
  }, [open, base]);

  return (
    <Modal title={open ? open.label : "Licenses"} onClose={onClose} width={780}>
      {!open ? (
        <div className="flex flex-col gap-1">
          <div className="mb-2 text-[12.5px] text-muted">
            What this app carries, and under whose license: Proofpane Community&rsquo;s own, and the notices of the
            third-party software in it, as its build writes them.
          </div>
          {notices.map((n) => (
            <button key={n.file} className="flex w-full items-start gap-2.5 rounded-md px-2.5 py-2 text-left text-[12.5px] text-fg-dim hover:bg-hover hover:text-fg [@media(pointer:coarse)]:min-h-11"
              onClick={() => setOpen(n)}>
              <FileText size={14} className="mt-0.5 flex-none" />
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span>{n.label}</span>
                <span className="break-all font-mono text-[11px] text-muted">{n.file}</span>
              </span>
            </button>
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <button className="btn btn-sm self-start" onClick={() => setOpen(null)}><ArrowLeft size={13} /> Licenses</button>
          {error ? <div role="alert" className="text-[12.5px] text-fail">{error}</div>
            : text === null ? <div className="flex items-center gap-2 text-[12.5px] text-muted"><Loader2 size={13} className="animate-spin" /> Reading {open.file}</div>
            : <pre aria-label={open.file} className="whitespace-pre-wrap break-words font-mono text-[11.5px] leading-relaxed text-fg-dim">{text}</pre>}
        </div>
      )}
    </Modal>
  );
}
