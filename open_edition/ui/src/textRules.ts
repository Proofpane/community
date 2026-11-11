// The server's rules for what linked sessions send, as the page holds them: what it refuses before signing a command,
// how it shows what a session wrote (visible), and what a sealed text must be once opened. The server cannot read a
// sealed text, so it cannot check one: the page checks the opened value's shape and holds it to the rules the server
// holds that field to in the clear (link.py's _text, _shown and _history_row, link_commands.check_text).

/** A character that does not show what it is, as the server's link_commands.hidden_character finds it: a format
 *  character (the bidi controls, the zero-width space, non-joiner and joiner, the word joiner and invisible operators,
 *  the soft hyphen, the byte order mark, the tag characters), private use, a line or paragraph separator, anything in
 *  plane 14, a character drawn as nothing (the combining grapheme joiner, the Hangul fillers, the Khmer inherent
 *  vowels, the Mongolian free variation selectors and vowel separator, the blank Braille pattern), a variation
 *  selector but VS15 and VS16, or one of those two at the start or right after another. */
export const HIDDEN = /[\p{Cf}\p{Co}\p{Zl}\p{Zp}\u{E0000}-\u{EFFFF}\u{FE00}-\u{FE0D}\u{034F}\u{115F}\u{1160}\u{17B4}\u{17B5}\u{180B}-\u{180F}\u{2800}\u{3164}\u{FFA0}]|(?:^|[\u{FE0E}\u{FE0F}])[\u{FE0E}\u{FE0F}]/u;
/** The shapes the server's link.check_secrets refuses, named as it names them, so a command holding one is refused
 *  before the passkey prompt rather than after it. */
export const SECRET_SHAPES: [string, RegExp][] = [
  ["an Anthropic key (sk-ant-)", /\bsk-ant-/],
  ["a key (sk- and 16 or more characters)", /\bsk-[A-Za-z0-9_-]{16,}/],
  ["a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)", /\bgh[pousr]_/],
  ["a GitHub token (github_pat_)", /\bgithub_pat_/],
  ["a Fly token (FlyV1)", /\bFlyV1 /],
  ["an AWS key (AKIA)", /\bAKIA/],
  ["a private key (-----BEGIN ... PRIVATE KEY)", /-----BEGIN[^\n]*PRIVATE KEY/],
  ["an ADE hook token (adeh_)", /\badeh_/],
  ["an ADE ticket (PP-)", /\bPP-[A-Z2-7]{8}\b/i],
  ["a Stripe key (sk_live_, rk_live_)", /\b[rs]k_live_/],
  ["a Slack token (xoxb-, xoxp-, ...)", /\bxox[abprs]-/],
  ["a Google API key (AIza)", /\bAIza[A-Za-z0-9_-]{30}/],
  ["a GitLab token (glpat-)", /\bglpat-/],
  ["an npm token (npm_)", /\bnpm_[A-Za-z0-9]{36}/],
  ["a signed web token (eyJ...)", /\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\./],
];
/** Marks drawn over the character before them, so a line reads otherwise than its letters say ("not" struck through,
 *  "=" read as "not equal"), or around it (a circle, a prohibition sign, a keycap): Unicode's overlays (canonical
 *  combining class 1) and its enclosing marks (category Me), as Unicode 15.1 lists them and the server's
 *  link_commands.DRAWN_OVER holds them. A command, an answer and a question may hold none. */
export const DRAWN_OVER = /[\u{0334}-\u{0338}\u{1CD4}\u{1CE2}-\u{1CE8}\u{20D2}\u{20D3}\u{20D8}-\u{20DA}\u{20E5}\u{20E6}\u{20EA}\u{20EB}\u{10A39}\u{16AF0}-\u{16AF4}\u{1BC9E}\u{1D167}-\u{1D169}\u{0488}\u{0489}\u{1ABE}\u{20DD}-\u{20E0}\u{20E2}-\u{20E4}\u{A670}-\u{A672}]/u;
/** The non-spacing marks one character may carry in what a connected app proposes (the server's
 *  link_commands.MARKS_MAX): a stack of more is drawn over the lines around it. */
export const MARKS_MAX = 2;
const NON_SPACING = /^\p{Mn}$/u;

/** The first character of `text` that more than `most` non-spacing marks follow (a mark itself where the text begins
 *  with them), as the server's link_commands.stacked finds it; null for none. */
export function stackedMarks(text: string, most = MARKS_MAX): string | null {
  let run = 0;
  let base: string | null = null;
  for (const ch of text) {
    if (NON_SPACING.test(ch)) {
      run += 1;
      if (run > most) return base ?? ch;
    } else {
      run = 0;
      base = ch;
    }
  }
  return null;
}

/** What visible() writes as a code point: what the server's link.hides does, every control and separator but those
 *  that show as what they are (a line break, a carriage return and a tab, the text being shown on lines, and every
 *  space: a no-break space, a figure or thin one, the full-width space an input method types), and HIDDEN's own; a
 *  command is held to HIDDEN (commandProblem), as the server's check_text holds it. */
const HIDDEN_ALL = new RegExp(`(?![\\n\\r\\t\\p{Zs}])[\\p{C}\\p{Z}]|${HIDDEN.source}`, "gu");
export const codePoint = (c: string) => `U+${(c.codePointAt(0) ?? 0).toString(16).toUpperCase().padStart(4, "0")}`;

/** Text with every character that is not shown as itself written as [U+XXXX], so a session's text shows what is in
 *  it. Commands and questions refuse them; older texts may still hold them. */
export function visible(text: string): string {
  return String(text).replace(HIDDEN_ALL, (m) => [...m].map((c) => `[${codePoint(c)}]`).join(""));
}

// ---- what an opened text must be -----------------------------------------------------------------------------------

/** Why an opened text is not shown in its place: it is not what its path seals (its shape, its types, or what it says
 *  of itself against what the server serves beside it), it holds a character the ADE does not show, or it breaks
 *  another of the server's rules for its field (`rule` says which). */
export type Refusal = { why: "unreadable" } | { why: "hidden" } | { why: "rule"; rule: string };
export type Checked<T> = { ok: true; value: T } | { ok: false; refusal: Refusal };

const UNREADABLE: Refusal = { why: "unreadable" };
const HIDES: Refusal = { why: "hidden" };
const refuse = <T>(refusal: Refusal): Checked<T> => ({ ok: false, refusal });

/** A refusal in the words the page shows in place of the text. */
export function refusalWords(r: Refusal): string {
  if (r.why === "unreadable") return "This text could not be read";
  if (r.why === "hidden") return "This text holds characters the ADE does not show";
  return `This text is not shown: ${r.rule}`;
}

const SELECTOR_FIRST = 0xfe00;
const SELECTOR_LAST = 0xfe0d;
const PRESENTATION = new Set([0xfe0e, 0xfe0f]);
const BLANK = new Set([0x034f, 0x115f, 0x1160, 0x17b4, 0x17b5, 0x180b, 0x180c, 0x180d, 0x180e, 0x180f, 0x2800, 0x3164,
  0xffa0]);
const OTHER_OR_SEPARATOR = /^[\p{C}\p{Z}]$/u;

/** Whether `ch`, after the character whose code is `prev` (null at the start), does not show itself: the server's
 *  link.hides. Every control, format character, separator but the space (a no-break space too), surrogate, private
 *  use or unassigned code point; anything in plane 14; a variation selector but VS15 and VS16, and either of those at
 *  the start or right after another; and the characters drawn as nothing. */
export function hides(ch: string, prev: number | null): boolean {
  const code = ch.codePointAt(0) ?? 0;
  return (ch !== " " && OTHER_OR_SEPARATOR.test(ch)) || (code >= 0xe0000 && code <= 0xeffff)
    || (code >= SELECTOR_FIRST && code <= SELECTOR_LAST) || BLANK.has(code)
    || (PRESENTATION.has(code) && (prev === null || PRESENTATION.has(prev)));
}

/** Whether every character of `value` shows itself, as the server's _shown holds a History field: no line break and no
 *  tab either. */
export function allShown(value: string): boolean {
  let prev: number | null = null;
  for (const ch of value) {
    if (ch === "\n" || ch === "\r" || ch === "\t" || hides(ch, prev)) return false;
    prev = ch.codePointAt(0) ?? 0;
  }
  return true;
}

// Python's str.strip() takes away what str.isspace() names, which is not what String.prototype.trim() takes away
const PY_BLANK = /^[\t\n\v\f\r\x1c-\x1f \x85\xa0\u{1680}\u{2000}-\u{200A}\u{2028}\u{2029}\u{202F}\u{205F}\u{3000}]*$/u;
const lengthWords = (n: number) => n.toLocaleString("en-US");

function secretRule(value: string): Refusal | null {
  const shape = SECRET_SHAPES.find(([, pattern]) => pattern.test(value));
  return shape ? { why: "rule", rule: `it holds something shaped like ${shape[0]}` } : null;
}

/** The server's _text: why `value` is not text of at most `limit` characters (by code point, as Python counts them),
 *  not empty where it is `required`, with no NUL and nothing shaped like a secret; null when it is. A value that is not
 *  text, or is missing where it is required, is not what the path seals. */
function textRule(value: unknown, limit: number, required: boolean): Refusal | null {
  if ((value === null || value === undefined) && !required) return null;
  if (typeof value !== "string") return UNREADABLE;
  if (required && PY_BLANK.test(value)) return { why: "rule", rule: "it is empty" };
  if ([...value].length > limit) return { why: "rule", rule: `it is longer than ${lengthWords(limit)} characters` };
  if (value.includes("\x00")) return HIDES;
  return secretRule(value);
}

/** A History field held as the server holds it: _text, then _shown. */
function fieldRule(value: unknown, limit: number, required: boolean): Refusal | null {
  const broken = textRule(value, limit, required);
  if (broken) return broken;
  return typeof value === "string" && !allShown(value) ? HIDES : null;
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const onlyKeys = (v: Record<string, unknown>, keys: string[]) => Object.keys(v).every((k) => keys.includes(k));
const whole = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v);

export const HISTORY_TITLE = 120;
export const HISTORY_PROJECT = 300;
export const HISTORY_BRANCH = 120;
export const HISTORY_REPOS = 20;

/** A History row's sealed fields, once open. */
export interface RowFields { title: string | null; project: string | null; branch: string | null; repositories: string[] | null }

/** A History row's sealed fields once opened ({title, project, branch, repositories}, or {project} for a withheld row),
 *  held to the rules the server's _history_row holds them to in the clear. A title, a branch or a project that breaks
 *  one refuses them all, as the server leaves such a row out; a repository that breaks one is left out alone, as
 *  there, and so is one named twice; at most HISTORY_REPOS are kept. */
export function historyFields(value: unknown, withheld: boolean): Checked<RowFields> {
  if (!isRecord(value)) return refuse(UNREADABLE);
  if (withheld) {
    // a withheld row's title, branch and repositories stay on its machine: its sealed text names its folder alone
    if (!onlyKeys(value, ["project"])) return refuse(UNREADABLE);
    const broken = fieldRule(value.project, HISTORY_PROJECT, false);
    if (broken) return refuse(broken);
    return { ok: true, value: { title: null, project: (value.project as string | null | undefined) ?? null, branch: null, repositories: null } };
  }
  if (!onlyKeys(value, ["title", "project", "branch", "repositories"])) return refuse(UNREADABLE);
  const broken = fieldRule(value.title, HISTORY_TITLE, true) ?? fieldRule(value.branch, HISTORY_BRANCH, false)
    ?? fieldRule(value.project, HISTORY_PROJECT, false);
  if (broken) return refuse(broken);
  const repos = value.repositories;
  if (repos !== null && repos !== undefined && !Array.isArray(repos)) return refuse(UNREADABLE);
  const tops: string[] = [];
  for (const top of repos ?? []) {
    if (fieldRule(top, HISTORY_PROJECT, true) === null && !tops.includes(top as string) && tops.length < HISTORY_REPOS) {
      tops.push(top as string);
    }
  }
  return { ok: true, value: {
    title: value.title as string, project: (value.project as string | null | undefined) ?? null,
    branch: (value.branch as string | null | undefined) ?? null, repositories: tops.length ? tops : null,
  } };
}

const QUESTION_MAX = 4000;

/** The server's link_commands.check_text, which holds a question's words (an answer carries their digest): why `value`
 *  breaks it, or null. Not empty, at most 4,000 characters, no lone surrogate, no control character but a line break
 *  and a tab, nothing HIDDEN finds, no mark DRAWN_OVER finds, in Unicode normal form C, nothing shaped like a secret. */
export function questionRule(value: string): Refusal | null {
  if (PY_BLANK.test(value)) return { why: "rule", rule: "it is empty" };
  if ([...value].length > QUESTION_MAX) return { why: "rule", rule: `it is longer than ${lengthWords(QUESTION_MAX)} characters` };
  for (const ch of value) {
    const code = ch.codePointAt(0) ?? 0;
    if ((code >= 0xd800 && code <= 0xdfff) || (code < 0x20 && ch !== "\n" && ch !== "\t") || (code >= 0x7f && code <= 0x9f)) return HIDES;
  }
  if (HIDDEN.test(value)) return HIDES;
  if (DRAWN_OVER.test(value)) return { why: "rule", rule: "it holds a mark drawn over or around a character, which could change how it reads" };
  if (value.normalize("NFC") !== value) return { why: "rule", rule: "it is not in Unicode normal form C" };
  return secretRule(value);
}

const SESSION_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/;
/** The longest text the server takes in the clear: an event's (link.py MAX_TEXT), and a conversation's message
 *  (TALK_TEXT: ade_link.py cuts one at 4,000 characters and says so). */
export const EVENT_TEXT = 4000;
export const TALK_TEXT = 4100;

/** What the server serves beside an event's text: its mirror's word, and for a mirrored note the session it names
 *  (where the server kept the tag) and when its words were said. */
export interface EventServed { mirror?: string; session?: string; said_at?: number }

/** An event's sealed text once opened ({text, session, said_at}, either of the last two null or absent): its text, when
 *  that is the shape a link seals and what it says of its session and of when it was said is what the server serves
 *  beside it. A served session may be absent where the server dropped the tag of a session it does not have registered,
 *  so a mirrored note may then name one; any other event names none. The text is held to the server's _text for an
 *  event: not empty, at most EVENT_TEXT characters, no NUL, nothing shaped like a secret. */
export function eventText(value: unknown, served: EventServed): Checked<string> {
  if (!isRecord(value) || !onlyKeys(value, ["text", "session", "said_at"]) || typeof value.text !== "string") return refuse(UNREADABLE);
  const session = value.session ?? null;
  const saidAt = value.said_at ?? null;
  if (session !== null && (typeof session !== "string" || !SESSION_ID.test(session))) return refuse(UNREADABLE);
  if (saidAt !== null && (!whole(saidAt) || saidAt <= 0)) return refuse(UNREADABLE);
  if (saidAt !== (served.said_at ?? null)) return refuse(UNREADABLE);
  if (served.session !== undefined && served.session !== null ? session !== served.session : session !== null && !served.mirror) {
    return refuse(UNREADABLE);
  }
  const broken = textRule(value.text, EVENT_TEXT, true);
  return broken ? refuse(broken) : { ok: true, value: value.text };
}

/** A conversation message's sealed text once opened ({text}): its text, held to the server's rule for a message in the
 *  clear (_text: not empty, at most TALK_TEXT characters, no NUL, nothing shaped like a secret). */
export function talkText(value: unknown): Checked<string> {
  if (!isRecord(value) || !onlyKeys(value, ["text"]) || typeof value.text !== "string") return refuse(UNREADABLE);
  const broken = textRule(value.text, TALK_TEXT, true);
  return broken ? refuse(broken) : { ok: true, value: value.text };
}

// a heartbeat's report's lists (link.py, _report_check): their bounds and the words the server takes for them
export const REPORT_TASKS = 30;
export const REPORT_BACKGROUND = 12;
export const REPORT_TEXT = 160;
const REPORT_TASK_STATES = ["pending", "in_progress", "completed"];
const REPORT_KINDS = ["shell", "agent"];
const REPORT_BACKGROUND_STATES = ["running", "completed", "failed", "killed", "stopped"];
const REPORT_ID = /^[A-Za-z0-9_-]{1,64}$/;
export interface ReportedLists {
  tasks: { text: string; state: string }[];
  background: { id: string; kind: string; text: string; state: string }[];
}

/** A heartbeat's report's lists, in the clear or once a sealed one is opened ({tasks?, background?}), held whole to the
 *  server's rules for them in the clear (_report_check): at most REPORT_TASKS tasks of {text, state} and
 *  REPORT_BACKGROUND background tasks of {id, kind, text, state}, each text _text's (at most REPORT_TEXT characters, a
 *  task's not empty, no NUL, nothing shaped like a secret, an id's neither), each state, kind and id one it takes. */
export function reportedLists(value: unknown): Checked<ReportedLists> {
  if (!isRecord(value) || !onlyKeys(value, ["tasks", "background"])) return refuse(UNREADABLE);
  const tasks = value.tasks ?? [];
  const background = value.background ?? [];
  if (!Array.isArray(tasks) || tasks.length > REPORT_TASKS || !Array.isArray(background)
    || background.length > REPORT_BACKGROUND) return refuse(UNREADABLE);
  for (const t of tasks) {
    if (!isRecord(t) || !onlyKeys(t, ["text", "state"]) || !REPORT_TASK_STATES.includes(t.state as string)) {
      return refuse(UNREADABLE);
    }
    const broken = textRule(t.text, REPORT_TEXT, true);
    if (broken) return refuse(broken);
  }
  for (const b of background) {
    if (!isRecord(b) || !onlyKeys(b, ["id", "kind", "text", "state"]) || typeof b.id !== "string"
      || !REPORT_ID.test(b.id) || !REPORT_KINDS.includes(b.kind as string)
      || !REPORT_BACKGROUND_STATES.includes(b.state as string) || typeof b.text !== "string") return refuse(UNREADABLE);
    const broken = textRule(b.text, REPORT_TEXT, false) ?? secretRule(b.id);
    if (broken) return refuse(broken);
  }
  return { ok: true,
    value: { tasks: tasks as ReportedLists["tasks"], background: background as ReportedLists["background"] } };
}
