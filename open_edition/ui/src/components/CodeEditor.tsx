// A file of the worktree, edited on the page: CodeMirror 6, its language loaded when the file is opened (only the one it
// needs), coloured with the same classes as the read-only view, and following the app's light or dark theme through
// its colour variables. Loaded only when a file is first edited.
import { useEffect, useImperativeHandle, useRef, type Ref } from "react";
import { EditorState, Compartment, type Text } from "@codemirror/state";
import { EditorView, drawSelection, highlightActiveLine, highlightActiveLineGutter, highlightSpecialChars, keymap, lineNumbers } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { HighlightStyle, LanguageDescription, bracketMatching, indentOnInput, syntaxHighlighting } from "@codemirror/language";
import { highlightSelectionMatches, searchKeymap } from "@codemirror/search";
import { languages } from "@codemirror/language-data";
import { tags as t } from "@lezer/highlight";

export interface EditorHandle {
  /** The text as it is now, with the file's own line endings. */
  text(): string;
  /** What is in the editor is now what the file holds: it is no longer unsaved. */
  saved(): void;
  focus(): void;
}

// the read-only view's classes (index.css), so a file is coloured the same whether it is read or edited
const STYLE = HighlightStyle.define([
  { tag: [t.comment, t.lineComment, t.blockComment, t.docComment], class: "token comment" },
  { tag: [t.punctuation, t.bracket, t.separator, t.paren, t.brace, t.squareBracket, t.angleBracket], class: "token punctuation" },
  { tag: [t.propertyName, t.tagName, t.constant(t.variableName), t.standard(t.variableName), t.atom, t.deleted], class: "token property" },
  { tag: [t.bool, t.number, t.integer, t.float, t.null], class: "token number" },
  { tag: [t.string, t.special(t.string), t.character, t.attributeName, t.inserted, t.docString], class: "token string" },
  { tag: [t.operator, t.url, t.escape, t.link], class: "token operator" },
  { tag: [t.keyword, t.controlKeyword, t.definitionKeyword, t.moduleKeyword, t.operatorKeyword, t.modifier, t.self, t.meta, t.annotation, t.processingInstruction, t.attributeValue], class: "token keyword" },
  { tag: [t.function(t.variableName), t.function(t.propertyName), t.definition(t.function(t.variableName)), t.className, t.typeName, t.namespace], class: "token function" },
  { tag: [t.regexp, t.special(t.variableName), t.labelName], class: "token regex" },
  { tag: [t.heading, t.strong], class: "token bold" },
  { tag: t.emphasis, class: "token italic" },
  { tag: t.invalid, class: "text-fail" },
]);

const THEME = EditorView.theme({
  "&": { height: "100%", backgroundColor: "var(--color-bg)", color: "var(--color-fg)", fontSize: "12px" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.6" },
  ".cm-content": { caretColor: "var(--color-accent)", paddingBlock: "4px" },
  ".cm-gutters": { backgroundColor: "var(--color-bg)", color: "var(--color-faint)", borderRight: "1px solid var(--color-line)" },
  ".cm-lineNumbers .cm-gutterElement": { paddingInline: "10px 10px", minWidth: "40px" },
  ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--color-hover) 70%, transparent)" },
  ".cm-activeLineGutter": { backgroundColor: "transparent", color: "var(--color-fg-dim)" },
  "&.cm-focused .cm-cursor": { borderLeftColor: "var(--color-accent)", borderLeftWidth: "2px" },
  "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
    { backgroundColor: "color-mix(in srgb, var(--color-accent) 28%, transparent)" },
  ".cm-selectionMatch": { backgroundColor: "color-mix(in srgb, var(--color-accent) 14%, transparent)" },
  ".cm-matchingBracket, &.cm-focused .cm-matchingBracket": { backgroundColor: "color-mix(in srgb, var(--color-accent) 22%, transparent)", outline: "none" },
  ".cm-searchMatch": { backgroundColor: "color-mix(in srgb, var(--color-wait) 30%, transparent)" },
  ".cm-searchMatch-selected": { backgroundColor: "color-mix(in srgb, var(--color-wait) 55%, transparent)" },
  ".cm-panels": { backgroundColor: "var(--color-panel)", color: "var(--color-fg)", borderColor: "var(--color-line)" },
  ".cm-panels input, .cm-panels button": { fontSize: "12px" },
  ".cm-textfield": { backgroundColor: "var(--color-bg)", border: "1px solid var(--color-line-strong)", color: "var(--color-fg)", borderRadius: "4px" },
  ".cm-button": { backgroundImage: "none", backgroundColor: "var(--color-raised)", border: "1px solid var(--color-line-strong)", color: "var(--color-fg)", borderRadius: "4px" },
});

/** One line ending for the file when it has only one kind: Windows' kept as Windows', so a save changes only what was
 *  edited. A file with both is shown with either and saved with Unix ones. */
export function lineEnding(text: string): "\r\n" | null {
  const crlf = (text.match(/\r\n/g) || []).length;
  const lf = (text.match(/\n/g) || []).length;
  return crlf > 0 && crlf === lf ? "\r\n" : null;
}

export function CodeEditor({ text, clean: cleanText, path, line, onDirty, onSave, onLeave, handle }: {
  text: string;
  /** What the file holds, when the editor opens on something else (an unsaved draft): changes are counted from it. */
  clean?: string;
  path: string;
  /** Where to put the cursor when it opens, a line number from 1. */
  line?: number;
  onDirty: (dirty: boolean) => void;
  onSave: () => void;
  /** As the editor closes (another file, a tab, a reload): its text, and whether it differs from the file. */
  onLeave?: (text: string, unsaved: boolean) => void;
  handle: Ref<EditorHandle>;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const clean = useRef<Text | null>(null);
  const calls = useRef({ onDirty, onSave, onLeave });
  calls.current = { onDirty, onSave, onLeave };

  useImperativeHandle(handle, () => ({
    text: () => view.current?.state.sliceDoc() ?? "",
    saved: () => {
      if (!view.current) return;
      clean.current = view.current.state.doc;
      calls.current.onDirty(false);
    },
    focus: () => view.current?.focus(),
  }), []);

  useEffect(() => {
    const language = new Compartment();
    const ending = lineEnding(text);
    const state = EditorState.create({
      doc: text,
      extensions: [
        ...(ending ? [EditorState.lineSeparator.of(ending)] : []),
        lineNumbers(), highlightActiveLineGutter(), highlightSpecialChars(), history(), drawSelection(), indentOnInput(),
        bracketMatching(), highlightActiveLine(), highlightSelectionMatches(), syntaxHighlighting(STYLE), language.of([]),
        keymap.of([
          { key: "Mod-s", preventDefault: true, run: () => { calls.current.onSave(); return true; } },
          indentWithTab, ...defaultKeymap, ...historyKeymap, ...searchKeymap,
        ]),
        EditorView.updateListener.of((u) => {
          if (u.docChanged && clean.current) calls.current.onDirty(!u.state.doc.eq(clean.current));
        }),
        THEME,
      ],
    });
    clean.current = cleanText === undefined ? state.doc : state.toText(cleanText);
    calls.current.onDirty(!state.doc.eq(clean.current));
    const v = new EditorView({ state, parent: host.current! });
    view.current = v;
    if (line && line <= v.state.doc.lines) {
      const at = v.state.doc.line(line).from;
      v.dispatch({ selection: { anchor: at }, effects: EditorView.scrollIntoView(at, { y: "center" }) });
    }
    v.focus();
    const desc = LanguageDescription.matchFilename(languages, path.split("/").pop() || path);
    let gone = false;
    desc?.load().then((support) => { if (!gone) v.dispatch({ effects: language.reconfigure(support) }); }).catch(() => { /* uncoloured, still editable */ });
    return () => {
      gone = true;
      calls.current.onLeave?.(v.state.sliceDoc(), !!clean.current && !v.state.doc.eq(clean.current));
      v.destroy();
      view.current = null;
    };
    // a new file, or the same one read again, is a new editor; `line` only places the cursor when it opens
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, path]);

  return <div ref={host} className="h-full min-h-0" />;
}
