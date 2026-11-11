// Text cut to a length without splitting a character: an emoji, and any character past the first 65,536, is two
// UTF-16 units (a surrogate pair), and half of one is not text: a JSON reader may refuse it, and nothing that writes
// UTF-8 can write it.

const isHigh = (c: number) => c >= 0xd800 && c <= 0xdbff;
const isLow = (c: number) => c >= 0xdc00 && c <= 0xdfff;

/** At most `most` UTF-16 units of `text` from its start: one fewer when the last would be the first half of a pair. */
export function head(text: string, most: number): string {
  if (text.length <= most) return text;
  const end = Math.max(0, most);
  return text.slice(0, end > 0 && isHigh(text.charCodeAt(end - 1)) ? end - 1 : end);
}

/** At most `most` UTF-16 units of `text` from its end: one fewer when the first would be the second half of a pair. */
export function tail(text: string, most: number): string {
  if (text.length <= most) return text;
  const start = text.length - Math.max(0, most);
  return text.slice(isLow(text.charCodeAt(start)) ? start + 1 : start);
}

const HIDDEN = new Set([0x115f, 0x1160, 0x3164, 0xffa0]);       // the Hangul fillers, which show as nothing
const isSelector = (c: number) => (c >= 0xfe00 && c <= 0xfe0d) || (c >= 0xe0100 && c <= 0xe01ef);   // not the emoji ones
const UNSEEN = /[\p{Cc}\p{Cf}\p{Co}\p{Cn}\p{Cs}]/u;

/** `text` as a person reads it: without control and format characters (but line breaks and tabs), private-use and
 *  unassigned code points, halves of pairs, the Hangul fillers and the variation selectors but the two emoji ones;
 *  line and paragraph separators become line breaks. Someone else's text (an issue, a pull request) given to an agent
 *  is then the text the person saw, not words hidden in it (tag characters can spell out a whole sentence unseen). */
export function visible(text: string): string {
  let out = "";
  for (const ch of text) {
    const c = ch.codePointAt(0)!;
    if (ch === "\n" || ch === "\t") out += ch;
    else if (c === 0x2028 || c === 0x2029) out += "\n";
    else if (!UNSEEN.test(ch) && !HIDDEN.has(c) && !isSelector(c)) out += ch;
  }
  return out;
}

/** `text` with each character a person would not see, or that a terminal would act on (control characters, line
 *  breaks and tabs among them), written as \u{…}: a file's name put in a message for an agent, which reads it as it is
 *  and which no terminal takes as a key. */
export function escaped(text: string): string {
  let out = "";
  for (const ch of text) {
    const c = ch.codePointAt(0)!;
    out += UNSEEN.test(ch) || c === 0x2028 || c === 0x2029 || HIDDEN.has(c) || isSelector(c) ? `\\u{${c.toString(16)}}` : ch;
  }
  return out;
}
