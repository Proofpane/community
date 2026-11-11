import type { ReactNode } from "react";
import { DESKTOP, server } from "../api";
import { places } from "../places";
import { useStore } from "../store";

const SHOWN = 100;          // characters of a link's address shown when it is not opened

/** The sites whose pages are this page's own: its own, its server's, the address its server gives for itself, and in
 *  the desktop app each place's server. */
export function ownOrigins(desktop = DESKTOP): string[] {
  const own = [location.origin];
  const add = (address: string | null | undefined) => {
    try { if (address) own.push(new URL(address).origin); } catch { /* not an address */ }
  };
  add(server());
  add(useStore.getState().links?.public_url);
  if (desktop) for (const p of places()) add(p.server);
  return own;
}

/** Where a link in words this page did not write opens, in a new tab: only an http or https address on another site.
 *  Null for any other. A fragment, a relative address, or an address on this page's own site or its server's opens
 *  this page, whose address can carry its device and app-consent panes (#device=, #connect=) with what the words chose
 *  filled in; another scheme leaves the page altogether. */
export function linkOut(href: string | null | undefined, own: readonly string[] = ownOrigins()): string | null {
  if (!href) return null;
  let u: URL;
  try { u = new URL(href); } catch { return null; }
  if (u.protocol !== "http:" && u.protocol !== "https:") return null;
  return own.includes(u.origin) ? null : u.href;
}

/** A link in words this page did not write (a linked session's, a chat's, a Markdown file's in the Files tab): opened
 *  beside the page when linkOut allows it; otherwise its words as text and where it pointed, so the reader sees what
 *  the words tried. */
export function OutLink({ href, children }: { href?: string; children?: ReactNode }) {
  const out = linkOut(href);
  if (out) return <a href={out} target="_blank" rel="noopener noreferrer">{children}</a>;
  const words = typeof children === "string" ? children : Array.isArray(children) && children.every((c) => typeof c === "string") ? children.join("") : null;
  const shown = [...(href ?? "")];
  const where = !href || words === href ? "" : `: ${shown.length > SHOWN ? `${shown.slice(0, SHOWN).join("")}…` : href}`;
  return <span>{children}<span className="text-muted"> [link not opened{where}]</span></span>;
}
