// What a linked session may take on the owner's Approve from the page: the kinds of step its guard keeps for the owner
// at the machine, each by the phrase the session's client and this page show it by; the owner's allowance, as the
// client reports it with its heartbeat (given at the machine, with its pin --phone-take): the kinds, the owner keys
// whose Approve counts, and when; and a question's step, as the client posts one (post question --step): the
// question's own words, a blank line, then the step's block. A forced push and a recursive or forced delete are none
// of these kinds: they stay the owner's to do at the machine, and no Approve from the page takes them.

/** The kinds of step, in their order, each by its name. */
export const STEP_KINDS = ["deploy", "push", "send", "write"] as const;
export type StepKind = (typeof STEP_KINDS)[number];
/** Each kind's phrase, as the session's client writes it. */
export const KIND_PHRASES: Readonly<Record<StepKind, string>> = {
  deploy: "a deployment",
  push: "a push to a protected branch",
  send: "sending data out",
  write: "a write outside the working folder",
};
/** The kinds an Approve counts for only where its passkey's signature says the passkey cannot be backed up (its
 *  authenticator data's BE flag clear): a key bound to one device, never a synced passkey. */
export const DEVICE_BOUND_KINDS: readonly StepKind[] = ["deploy", "push"];
/** Such a key, as the page names it. Whether a key is a security key is the owner's word at the machine, where he named
 *  it: no signature here says so. */
export const NO_BACKUP = "a key that cannot be backed up, such as a security key";

/** What an Approve on the page is, and what the session checks of it: the one text the session's client, its status,
 *  this page's Approve and the manual say. */
export const PAGE_TRUST = "An Approve on the page is your passkey's signature over what that page composed and shows "
  + "you. If the page, or the server or browser behind it, is not honest, it can show you one command while this "
  + "session runs the one it asked: the session checks that you signed its own question, never what you saw.";

/** What stays open under an allowance, said on the link's card beside it, as the manual says it: the guard holds a
 *  session's shell and file-tool calls that write the files the link reads to let a step through, judged by their
 *  words and paths, not a program the session starts, another tool, or another path to those files. */
export const GUARD_OPEN = "What stays open: its guard holds the session's shell and file-tool calls that write the "
  + "files the link reads to let a step through, judged by their words and paths, but a program the session starts, "
  + "another tool or another path to those files can still write them, and the manual says what else stays open.";

/** The one word an answer approves a step with (the client's prompt_answer_form reads it so, and nothing else). */
export const APPROVE = "Approve";

/** The kinds a list names, each a known kind (`by` its name or its phrase) and none twice, in the kinds' order; null
 *  for any other list, and for an empty one. */
function kindsOf(items: readonly unknown[], by: (k: StepKind) => string): StepKind[] | null {
  const found = items.map((x) => STEP_KINDS.find((k) => by(k) === x));
  if (!found.length || found.some((k) => k === undefined) || new Set(found).size !== found.length) return null;
  return STEP_KINDS.filter((k) => found.includes(k));
}
const byName = (k: StepKind) => k;
const byPhrase = (k: StepKind) => KIND_PHRASES[k];

/** Whether `have` holds every kind of `need`. */
export const covers = (have: readonly StepKind[], need: readonly StepKind[]) => need.every((k) => have.includes(k));

/** Kinds as the client joins their phrases: "a push to a protected branch, sending data out". */
export const phrasesOf = (kinds: readonly StepKind[]) => kinds.map(byPhrase).join(", ");

/** Kinds in words, as a choice of steps: "a deployment", "a deployment or sending data out", "a, b or c". */
export function kindsWords(kinds: readonly StepKind[]): string {
  const p = kinds.map(byPhrase);
  return p.length < 2 ? p.join("") : `${p.slice(0, -1).join(", ")} or ${p[p.length - 1]}`;
}

/** The kinds a prompt's text says the guard kept for the owner (its `kept`, their phrases); null where it says none,
 *  or names them in no form the client writes. */
export const keptOf = (v: unknown): StepKind[] | null => (Array.isArray(v) ? kindsOf(v, byPhrase) : null);

/** The owner's allowance as a link's client reports it: the kinds of step it may take on an Approve from the page, the
 *  fingerprints of the owner keys whose Approve counts (as the session pinned them), and when it was given (unix
 *  seconds). The session it was given for is not reported. */
export interface PhoneTake { kinds: StepKind[]; keys: string[]; at: number }

/** The most keys an allowance names: an account's passkeys' most (the server's passkeys.MAX_PASSKEYS). */
const KEYS_MOST = 20;
const FINGERPRINT = /^[0-9a-f]{32}$/;

/** A link's phone_take as the server serves it, read: null for none, and for a value in no form the client sends
 *  (exactly {kinds, keys, at}: kinds one or more known, none twice; keys one to 20 fingerprints, none twice; at a
 *  positive whole number). */
export function phoneTakeOf(v: unknown): PhoneTake | null {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const parts = Object.keys(v);
  if (parts.length !== 3 || !["kinds", "keys", "at"].every((k) => parts.includes(k))) return null;
  const { kinds, keys, at } = v as { kinds: unknown; keys: unknown; at: unknown };
  if (!Array.isArray(kinds) || typeof at !== "number" || !Number.isSafeInteger(at) || at <= 0) return null;
  if (!Array.isArray(keys) || !keys.length || keys.length > KEYS_MOST || new Set(keys).size !== keys.length
      || !keys.every((k) => typeof k === "string" && FINGERPRINT.test(k))) return null;
  const read = kindsOf(kinds, byName);
  return read ? { kinds: read, keys: keys as string[], at } : null;
}

/** Whether a passkey's signature says the passkey can be backed up: the BE flag (0x08) of its authenticator data's
 *  flags, as the server reads them (base64url, at least 37 bytes, the flags the 33rd); null where its authenticator
 *  data cannot be read. */
export function backupEligible(authenticatorData: unknown): boolean | null {
  if (typeof authenticatorData !== "string" || !/^[A-Za-z0-9_-]*$/.test(authenticatorData)) return null;
  let bytes: string;
  try {
    bytes = atob(authenticatorData.replace(/-/g, "+").replace(/_/g, "/")
      + "===".slice((authenticatorData.length + 3) % 4));
  } catch {
    return null;
  }
  return bytes.length < 37 ? null : (bytes.charCodeAt(32) & 0x08) !== 0;
}

/** The first line of a step's block, up to its kinds' phrases. */
export const STEP_HEAD = "The step, as it will run (kept for you at the machine: ";
const MARK = `\n\n${STEP_HEAD}`;
// a step's last line, in its two forms: allowed, with when (UTC) and the kinds allowed; or not, with the kinds not
// (its keys: an Approve counts only signed with a key the allowance names, and for a deployment or a push only with one
// that cannot be backed up, which the line then says)
const ALLOWED = new RegExp("^You allowed this session, at the machine at (\\d{4})-(\\d{2})-(\\d{2}) (\\d{2}):(\\d{2}) "
  + "UTC, to take (.+) on your Approve from the page, signed with a key you named there( that cannot be backed up)?: "
  + "Approve lets it run this once, before your answer expires\\.$");
const NOT_ALLOWED = new RegExp("^You have not allowed this session to take (.+) on your answer from the page: an "
  + "Approve here will not let it run\\.$");

/** A question's step, read from its text: the question's own words before it, the block's lines as posted (its
 *  first, the command, the folder, its last), the kinds its first line names, and what its last line said when it
 *  was posted: that the owner allowed the session, at the machine at `at` (the minute it names), to take them on an
 *  Approve from the page; or the kinds of them it was not allowed (`missing`). */
export interface Step {
  words: string;
  head: string;
  command: string;
  folder: string;
  last: string;
  kinds: StepKind[];
  said: { at: number } | { missing: StepKind[] };
}

/** Whether a question's text holds a step's block: its first line, after a blank line. */
export const holdsStep = (text: string) => text.includes(MARK);

/** What a step's last line said, where it is one of the two the client writes, of a step of `kinds`. */
function lastSaid(line: string, kinds: StepKind[]): Step["said"] | null {
  const yes = ALLOWED.exec(line);
  if (yes) {
    const [y, mo, d, h, mi] = yes.slice(1, 6).map(Number);
    const at = Date.UTC(y, mo - 1, d, h, mi);
    const back = new Date(at);                  // a day that is one, its parts read back as they were written
    const real = back.getUTCFullYear() === y && back.getUTCMonth() === mo - 1 && back.getUTCDate() === d && h < 24
      && mi < 60;
    const taken = kindsOf(yes[6].split(", "), byPhrase);
    // the backed-up rule said where, and only where, the step holds a deployment or a push
    const fits = (yes[7] !== undefined) === kinds.some((k) => DEVICE_BOUND_KINDS.includes(k));
    return real && taken && fits && covers(taken, kinds) ? { at: at / 1000 } : null;
  }
  const no = NOT_ALLOWED.exec(line);
  const missing = no ? kindsOf(no[1].split(", "), byPhrase) : null;
  return missing && covers(kinds, missing) ? { missing } : null;
}

/** A question's step, where its text holds one block and that block reads as the client writes one: its first line
 *  naming known kinds, the command on the lines after it, then the line with its folder and the last line; null
 *  otherwise, also where the block's first line is there twice, as its words or its command could put it. */
export function stepOf(text: string): Step | null {
  const at = text.indexOf(MARK);
  if (at < 0 || text.indexOf(MARK, at + 1) >= 0) return null;
  const lines = text.slice(at + 2).split("\n");
  if (lines.length < 4) return null;
  const head = lines[0], where = lines[lines.length - 2], last = lines[lines.length - 1];
  const command = lines.slice(1, -2).join("\n");
  if (!head.endsWith("):") || !command || !where.startsWith("in ") || where.length < 4) return null;
  const kinds = kindsOf(head.slice(STEP_HEAD.length, -2).split(", "), byPhrase);
  const said = kinds && lastSaid(last, kinds);
  return kinds && said ? { words: text.slice(0, at), head, command, folder: where.slice(3), last, kinds, said } : null;
}

/** Whether a link's report shows the very allowance a step's last line named: given in the minute that line names
 *  (the line names it to the minute), its kinds covering the step's. Another allowance, given since or before, may be
 *  another session's, which the report does not name. */
export const sameAllowance = (step: Step, reported: PhoneTake | null) =>
  "at" in step.said && !!reported && covers(reported.kinds, step.kinds)
    && Math.floor(reported.at / 60) * 60 === step.said.at;

/** Words that read as an approval, Approve or Approved, whatever their case, spaces and marks. */
export const readsAsApprove = (text: string) =>
  ["approve", "approved"].includes(text.toLowerCase().replace(/[\s.,;:!?'"]/gu, ""));
