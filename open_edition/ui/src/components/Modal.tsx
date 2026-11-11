import { useEffect, useRef, type ReactNode, type RefObject } from "react";
import { X } from "lucide-react";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** What in `box` the keyboard can reach, in order: not disabled, and not in a part that is hidden. */
export function focusables(box: HTMLElement): HTMLElement[] {
  return Array.from(box.querySelectorAll<HTMLElement>(FOCUSABLE))
    .filter((e) => !e.closest("[hidden], [aria-hidden='true'], [inert]") && getComputedStyle(e).display !== "none");
}

/** A layer over the page that is not a dialog (a drawer, the palette): the keyboard goes into it when it opens, to the
 *  layer itself (whose label is then read, and from which Tab goes into it) unless something in it has taken the
 *  keyboard already, and back to what opened it when it closes. Tab is not kept in it: it closes when anything else is
 *  clicked. */
export function useFocusInto(ref: RefObject<HTMLElement | null>) {
  const opener = useRef<Element | null>(null);
  if (opener.current === null && typeof document !== "undefined") opener.current = document.activeElement;
  useEffect(() => {
    const el = ref.current;
    if (el && !el.contains(document.activeElement)) el.focus();
    const back = opener.current;
    return () => {
      if (back instanceof HTMLElement && document.contains(back)) back.focus();
    };
  }, []);       // eslint-disable-line react-hooks/exhaustive-deps
}

/** The keyboard in a dialog (`box`, the element with role="dialog" and aria-modal): it stays in it while it is open
 *  (Tab and Shift+Tab go round its fields and buttons), it starts on what asks for it (autoFocus), else on what
 *  `first` selects in it, else its first field but its Close; Escape closes it unless a field in it used the key or
 *  another layer is open over it; and on closing the keyboard goes back to what opened it. */
export function useDialog(box: RefObject<HTMLElement | null>, onClose: () => void, first?: string) {
  const close = useRef(onClose);
  close.current = onClose;
  // what had the keyboard when the dialog opened: read while it first renders, before anything in it takes it
  const opener = useRef<Element | null>(null);
  if (opener.current === null && typeof document !== "undefined") opener.current = document.activeElement;

  useEffect(() => {
    const el = box.current;
    if (el && !el.contains(document.activeElement)) {
      const all = focusables(el);
      const asked = first ? el.querySelector<HTMLElement>(first) : null;
      (asked || all.find((e) => !e.hasAttribute("data-modal-close")) || all[0] || el).focus();
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        // the top layer's alone: a palette or another dialog opened over this one takes it, and this stays
        const layers = document.querySelectorAll('[role="dialog"][aria-modal="true"], [cmdk-root]');
        const top = layers[layers.length - 1];
        if (!e.defaultPrevented && (!top || top === el || !!el?.contains(top))) close.current();
        return;
      }
      if (e.key !== "Tab" || !el) return;
      const all = focusables(el);
      if (!all.length) {
        e.preventDefault();
        return;
      }
      const head = all[0];
      const last = all[all.length - 1];
      const here = document.activeElement;
      // the dialog itself (where the keyboard is when nothing in it can take it) comes before its first field
      if (e.shiftKey ? here === head || here === el || !el.contains(here) : here === last || !el.contains(here)) {
        e.preventDefault();
        (e.shiftKey ? last : head).focus();
      }
    };
    window.addEventListener("keydown", onKey);
    const back = opener.current;
    return () => {
      window.removeEventListener("keydown", onKey);
      if (back instanceof HTMLElement && document.contains(back)) back.focus();
    };
  }, []);       // eslint-disable-line react-hooks/exhaustive-deps
}

/** A dialog over the page, with the keyboard as useDialog keeps it. */
export function Modal(props: { title: string; onClose: () => void; children: ReactNode; width?: number; footer?: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  useDialog(box, props.onClose);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/55 px-4 pt-[12vh] backdrop-blur-[2px]" onMouseDown={props.onClose}>
      <div
        ref={box}
        role="dialog"
        aria-modal="true"
        aria-label={props.title}
        tabIndex={-1}
        className="flex max-h-[76vh] w-full flex-col overflow-hidden rounded-xl border border-line-strong bg-panel shadow-2xl shadow-black/60 outline-none"
        style={{ maxWidth: props.width ?? 560 }}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex h-11 flex-none items-center justify-between border-b border-line px-4">
          <span className="text-[13.5px] font-semibold">{props.title}</span>
          <button className="icon-btn" onClick={props.onClose} title="Close (Esc)" aria-label="Close" data-modal-close><X size={15} /></button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">{props.children}</div>
        {props.footer && <div className="flex flex-none items-center justify-end gap-2 border-t border-line px-4 py-3">{props.footer}</div>}
      </div>
    </div>
  );
}

/** A sheet from the page's left edge over the dimmed page (the phone's list of sessions): a dialog named `label`, with
 *  the keyboard as useDialog keeps it; a tap on the page beside it closes it. Whatever it holds brings its own Close. */
export function Sheet(props: { label: string; onClose: () => void; children: ReactNode; first?: string }) {
  const box = useRef<HTMLDivElement>(null);
  useDialog(box, props.onClose, props.first);
  return (
    <div className="fixed inset-0 z-40 bg-black/50" onMouseDown={props.onClose}>
      <div ref={box} role="dialog" aria-modal="true" aria-label={props.label} tabIndex={-1}
        className="h-full w-[86%] max-w-[340px] border-r border-line shadow-2xl outline-none"
        style={{ paddingTop: "env(safe-area-inset-top, 0px)" }} onMouseDown={(e) => e.stopPropagation()}>
        {props.children}
      </div>
    </div>
  );
}
