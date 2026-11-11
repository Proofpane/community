import { useMemo, type ReactNode } from "react";
import { fenceLanguage, highlightLines } from "../highlight";

const LONGEST = 100_000;    // a longer block is shown without colour, to stay quick

/** A Markdown code element: a fenced block that names a language this page knows is coloured as the file viewer
 *  colours it (its tokens become elements, never HTML); anything else is shown as it is. */
export function MarkdownCode({ className, children }: { className?: string; children?: ReactNode }) {
  const lang = /language-([\w+#.-]+)/.exec(className || "")?.[1];
  const text = typeof children === "string" ? children : Array.isArray(children) && children.every((c) => typeof c === "string") ? children.join("") : null;
  const grammar = fenceLanguage(lang);
  const coloured = useMemo(() => {
    if (!grammar || text == null || text.length > LONGEST) return null;
    try { return highlightLines(text.replace(/\n$/, ""), grammar); } catch { return null; }
  }, [grammar, text]);
  if (!coloured) return <code className={className}>{children}</code>;
  return (
    <code className={className}>
      {coloured.map((line, i) => (
        <span key={i}>
          {line.map((seg, j) => (seg.cls ? <span key={j} className={seg.cls}>{seg.text}</span> : seg.text))}
          {i < coloured.length - 1 ? "\n" : null}
        </span>
      ))}
    </code>
  );
}
