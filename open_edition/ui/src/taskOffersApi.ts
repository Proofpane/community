// Handing a task to a teammate, the page's side of /api/ade/teammate (proofpane_open/ade/task_offers.py).
//
// The types are the routes' shapes. Each function is one route, sent through the page's api(), so a refusal
// reaches the page as api()'s ApiError, with the route's status and its detail. Where the track is off (the setting,
// the local provider, OPEN_EDITION_OPERATOR unset), a route that starts or advances a hand-off answers 409 with
// {detail, off: "teammate"}, an acceptance among them, and api() keeps the status and the detail alone; those that only
// end or undo one answer as while it is on, and the two reads answer with `off`, why, from which the hand-off
// components say it.

import { api } from "./api";

export type OfferKind = "pressed" | "custody";
// "draft": made so its confirmation can name it (task-offer:<id>), never shown to the teammate
export type OfferState = "draft" | "held" | "offered" | "accepted" | "declined" | "withdrawn" | "expired" | "failed" | "returned";
// "asked" is written before the owner's confirmation names it (task-custody:<id>); the custodian sees it once confirmed;
// "declined" (the custodian's) and "expired" (not answered in 7 days after the confirmation) end a request, as "ended"
// does while it is "asked", its custodian and the typed address cleared
export type CustodyState = "asked" | "accepted" | "suspended" | "ended" | "declined" | "expired";

/** A hold on an offer, a custody hand-off or custody's crossing tick, as the page lists it with Cancel. */
export interface OfferHold { id: string; kind: "task.offer" | "task.crossing"; takes_effect_at: number }

/** Risk point 13's text as the routes return it: this file's own copy of the text's shape, so
 * that it reads no type of api.ts; `{owner}`, `{operator}` and `{record_keeping}` filled. */
export interface OfferTerms {
  point: number; title: string;
  design: { case: string | null; text: string }[];
  quotations: { id: string; case: string | null; source: string; section: string | null; address: string;
    source_date: string | null; read_on: string | null; words: string | null; unread: string | null }[];
  unread: boolean; digest: string;
}

/** An offer, made (the owner's) or received (the teammate's). */
export interface OfferView {
  id: string; kind: OfferKind; state: OfferState; task_id: string; title: string; goal: string;
  steps: { n: number; objective: string; check: string | null }[];   // the owner's checks, word for word, to adopt or leave
  from_email: string | null; to_email: string | null;
  repo_slug: string | null; branch: string; commit: string | null; packet_digest: string | null;
  loaded_changes: string[];                // files a next run would load that the offered commit changes
  hold: OfferHold | null; why: string | null; created_at: number; expires_at: number;
  // received offers only (null and [] on the owner's): point 13's text, and the teammate's agents whose account is in
  // another group than the task's, for which the acceptance shows the case "other_group"
  terms: OfferTerms | null; other_group_agents: string[];
  // received offers only (null on the owner's, and where no packet is kept; absent from an app before it): what the
  // packet kept with the offer holds
  packet?: OfferPacket | null;
  // the owner's accepted offers only (null otherwise; absent from an app before them): the teammate's task's state in
  // three words, and its step while it is not done
  their_state?: "running" | "waiting" | "done" | null;
  their_step?: number | null;
}

/** What the packet kept with a received offer holds, as its digest covers it: the owner's decisions, protected paths and external commands, and, as
 *  information, the steps done before the hand-off and the outward effects with what is known of each. */
export interface OfferPacket {
  decisions: { n: number; text: string }[]; protected: string[]; external: string[];
  done_steps: number[];
  outward_effects: { tool: string | null; kind: string | null; target: string | null; state: string | null }[];
}

/** Custody: the owner's custodian, or a teammate's request to be theirs. */
export interface CustodyView {
  id: string; state: CustodyState; owner_email: string | null; custodian_email: string | null;
  confirmed_at: number | null;
  agents: { agent_id: string; name: string; grp: string | null }[];   // the receiving agents, in order
  why: string | null; created_at: number; updated_at: number;
  terms: OfferTerms | null;                // point 13's text on a request as the custodian sees it; null on the owner's
}

/** The few fields of a task the hand-off parts need, so they read no type of the task pane's. The task pane passes its
 * own TaskView as it is, which
 * carries every member here with a type that assigns to it. `crossings_ended`: a press
 * ended the task's crossings, so custody's crossing tick is not offered on it (the route refuses it, 412). */
export interface HandOffTask {
  id: string; agent_id: string; state: string; state_version: number; title: string;
  repo_address: string | null; continued_from: { task: string; offer: string } | null;
  custody_confirmed: boolean; custody_cross: boolean; crossings_ended: boolean;
  offer: { id: string; state: string } | null;   // state: one of OfferState, as the app writes it
  // the task's holds as its view lists them, each with Cancel on its pane (the task pane's TaskView carries them; the
  // custody read's tasks do not): a hold listed there is not listed again here, so it has one Cancel
  holds?: { id: string; kind: string; takes_effect_at: number }[];
}

/** One place a push's secret scan found something shaped like a secret, never its value: a line a commit adds (`line`, in `file`), a path a commit adds (`name`, as `file`), or a line of a
 *  commit object, its message or a header such as its author (`commit`, no file). `file` is the path as the scan named
 *  it, each part shaped like a secret shown as "\u2026"; `column` where on its line or path the shape begins, from 1
 *  (null from a workspace that gives none); `finding` its id, which names that value at that place on a line that
 *  reads the same in that file, on whichever line of it, and nothing else, so two values on one line are two
 *  findings; `lifted` whether this user lifted it for the repository. */
export interface SecretFinding {
  finding: string; kind: "line" | "name" | "commit"; shape: string; file: string | null; column?: number | null;
  /** where it stands (the workspace's _scan_all): a line's number in the file as the commit leaves it, or a line's in
   *  the commit object; none for a name, and from a workspace that says none */
  line?: number | null;
  /** a line's or a name's path's place among those its commit changes, as `git diff-tree -r -m --no-renames --root
   *  <commit>` lists them, counted from 1 on through a merge's parents, at the first entry that names it; none for a
   *  commit's own line, and from a workspace that says none */
  place?: number | null;
  commit: string | null; lifted: boolean;
}

/** The digest a lift's confirmation names after the task (the app's task_offers.lift_subject, whose change is
 *  task-secret-lift:<task id>:<this>): the first 32 hex of the SHA-256 of the ids lifted, sorted, once each, joined by
 *  commas, so that one made for some findings is spent on no others. */
export async function liftDigest(findings: string[]): Promise<string> {
  const joined = [...new Set(findings)].sort().join(",");
  const digest = new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(joined)));
  return Array.from(digest, (b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}
/** A push of this user's that its secret scan held, which they may lift: on their own task (`owner`), its newest
 *  offer's; on a task they received (`teammate`), their side's push of their work to the owner. Never the other
 *  side's, whose push goes out with that person's own sign-in. */
export interface SecretHold { task_id: string; offer: string; side: "owner" | "teammate"; findings: SecretFinding[] }

// `off`: why the track is off, in the words its refusals give, while it is; absent while it is on. Every received offer
// and custody request then carries no `terms`, since nothing can be accepted. `secret_holds`: absent from an app from
// before them.
export interface OffersView { made: OfferView[]; received: OfferView[]; secret_holds?: SecretHold[]; off?: string }
export interface CustodyOverview {
  as_owner: CustodyView | null; as_custodian: CustodyView[]; tasks: HandOffTask[]; off?: string;
}

const BASE = "/api/ade/teammate";

/** An id as a part of a route's path. The routes give ids of 32 hex, which this leaves as they are; escaped all the
 *  same, so that no id the page holds can name another path. */
const part = (id: string) => encodeURIComponent(id);

/** A change sent to one of the routes: a POST, its body JSON (`{}` where the route takes nothing). */
function post<T>(path: string, body: Record<string, unknown>): Promise<T> {
  return api<T>(`${BASE}${path}`, { method: "POST", body });
}

/** The teammate's routes, each a function of the page's api(): GET or POST under /api/ade/teammate. */
export const TaskOffersApi = {
  offers: (): Promise<OffersView> => api<OffersView>(`${BASE}/offers`),
  offer: (taskId: string, stateVersion: number, address: string): Promise<{ offer: OfferView }> =>
    post<{ offer: OfferView }>("/offers", { task_id: taskId, state_version: stateVersion, address }),
  confirmOffer: (offerId: string, confirmation: string): Promise<{ offer: OfferView; hold: OfferHold }> =>
    post<{ offer: OfferView; hold: OfferHold }>(`/offers/${part(offerId)}/confirm`, { confirmation }),
  withdraw: (offerId: string): Promise<{ offer: OfferView }> => post<{ offer: OfferView }>(`/offers/${part(offerId)}/withdraw`, {}),
  accept: (offerId: string, agentId: string, adoptChecks: number[]): Promise<{ task_id: string }> =>
    post<{ task_id: string }>(`/offers/${part(offerId)}/accept`, { agent_id: agentId, adopt_checks: adoptChecks }),
  decline: (offerId: string): Promise<{ offer: OfferView }> => post<{ offer: OfferView }>(`/offers/${part(offerId)}/decline`, {}),
  // no version: Take back acts on whatever the teammate's side is doing, as Stop does
  takeBack: (taskId: string): Promise<{ task: HandOffTask }> => post<{ task: HandOffTask }>(`/tasks/${part(taskId)}/take-back`, {}),
  custody: (): Promise<CustodyOverview> => api<CustodyOverview>(`${BASE}/custody`),
  askCustody: (address: string): Promise<{ custody: CustodyView }> => post<{ custody: CustodyView }>("/custody", { address }),
  confirmCustody: (custodyId: string, confirmation: string): Promise<{ custody: CustodyView }> =>
    post<{ custody: CustodyView }>(`/custody/${part(custodyId)}/confirm`, { confirmation }),
  acceptCustody: (custodyId: string, agents: string[], confirmation: string): Promise<{ custody: CustodyView }> =>
    post<{ custody: CustodyView }>(`/custody/${part(custodyId)}/accept`, { agents, confirmation }),
  declineCustody: (custodyId: string): Promise<{ custody: CustodyView }> =>
    post<{ custody: CustodyView }>(`/custody/${part(custodyId)}/decline`, {}),
  endCustody: (custodyId: string): Promise<{ custody: CustodyView }> => post<{ custody: CustodyView }>(`/custody/${part(custodyId)}/end`, {}),
  confirmForCustody: (taskId: string, stateVersion: number, confirmation: string): Promise<{ task: HandOffTask }> =>
    post<{ task: HandOffTask }>(`/tasks/${part(taskId)}/custody-confirm`, { state_version: stateVersion, confirmation }),
  custodyCross: (taskId: string, stateVersion: number, confirmation: string): Promise<{ task: HandOffTask; hold: OfferHold }> =>
    post<{ task: HandOffTask; hold: OfferHold }>(`/tasks/${part(taskId)}/custody-cross`, { state_version: stateVersion, confirmation }),
  liftSecret: (taskId: string, findings: string[], confirmation: string): Promise<{ hold: SecretHold; task: HandOffTask }> =>
    post<{ hold: SecretHold; task: HandOffTask }>(`/tasks/${part(taskId)}/secret-lift`, { findings, confirmation }),
  keep: (taskId: string, stateVersion: number): Promise<{ task: HandOffTask }> =>
    post<{ task: HandOffTask }>(`/tasks/${part(taskId)}/keep`, { state_version: stateVersion }),
  giveBack: (taskId: string, stateVersion: number): Promise<{ task: HandOffTask }> =>
    post<{ task: HandOffTask }>(`/tasks/${part(taskId)}/give-back`, { state_version: stateVersion }),
};
