// The workspace's link to the audit in Proofpane One once it is paired (AUDIT-BUTTON, the work queue's section 4.311),
// in the title bar and on the Proofpane card, and a session's link to its own rows there, in its header.
import type { ProofpaneStatus, SessionRecord } from "./api";
import { linkOut } from "./components/OutLink";

/** What the links say, as their names and their titles, each naming the site it opens: a site this browser took for
 *  the device, which the card names before it is taken (takeSite). */
export const AUDIT_WORDS = "Open the audit in Proofpane One";
export const SESSION_AUDIT_WORDS = "This session in Proofpane One's audit";
const at = (href: string) => { try { return ` at ${new URL(href).host}`; } catch { return ""; } };
export const auditWords = (href: string) => AUDIT_WORDS + at(href);
export const sessionAuditWords = (href: string) => SESSION_AUDIT_WORDS + at(href);

/** The programs whose sessions have rows in the audit that name them: a Claude Code terminal's and a chat's, whose
 *  hooks send the session's id (PP_SESSION_ID) as `workspace_session` and whose usage rows the agent sends with it as
 *  `session_id`; and a Codex session's, whose usage rows the agent sends the same way. No other program's rows name
 *  its session. */
const AUDITED = new Set(["claude", "chat", "codex"]);

/** Where this browser keeps, for each device, the site it took: the one its own pairing gave, or one the owner took on
 *  the card. The workspace's record is a file a program in a session can write; a hosted workspace's session cannot
 *  write this browser's storage, so a site written in the record later, with or without a new cloud beside it, opens
 *  nothing until the owner takes it. On the owner's own Mac, where a session runs as the owner, it can write the
 *  desktop app's storage too, and this is no wall there. */
const TAKEN = "pp-audit-site:";

/** Take a pairing's site for its device in this browser, as a pairing made from this page does: whether it is kept. */
export function takeSite(st: ProofpaneStatus | null | undefined): boolean {
  if (!st?.paired || !st.device_id || !st.site_url) return false;
  try { localStorage.setItem(TAKEN + st.device_id, st.site_url); return true; } catch { return false; }
}

/** A device's id as Proofpane One makes it, a UUID: any other value in the record, such as one a program put there,
 *  is not named in an address. */
const ONE_DEVICE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Whether the status's site is the one this browser took for its device. */
export function siteTaken(st: ProofpaneStatus | null | undefined): boolean {
  if (!st?.device_id || !st.site_url) return false;
  try { return localStorage.getItem(TAKEN + st.device_id) === st.site_url; } catch { return false; }
}

/** Where the link opens: One's audit at the address its people sign in at, which One gave at pairing (`site_url`; the
 *  kept cloud_url is its API's), naming this workspace's device, or, for a session, the session alone, whose rows
 *  under an earlier pairing's device are its own too. Null unpaired, when One gave no address, for a device id not in
 *  One's form, at a site this browser did not take (takeSite), or at an address that is not an http or https address
 *  on another site (linkOut). Made of the site and the device or the session alone: the status carries neither the
 *  device's token nor a pairing code, and nothing else of it reaches the address. */
export function auditAddress(
  st: ProofpaneStatus | null | undefined, own?: readonly string[], session?: string,
): string | null {
  if (!st?.paired || !st.site_url || !st.device_id || !ONE_DEVICE.test(st.device_id) || !siteTaken(st)) return null;
  const of = session ? `session=${encodeURIComponent(session)}` : `device=${encodeURIComponent(st.device_id)}`;
  return linkOut(`${st.site_url}/audit?${of}`, own);
}

/** A session's own rows in the audit, for the programs whose rows name the session (AUDITED). */
export function sessionAudit(
  st: ProofpaneStatus | null | undefined, rec: SessionRecord, own?: readonly string[],
): string | null {
  return AUDITED.has(rec.program) ? auditAddress(st, own, rec.id) : null;
}
