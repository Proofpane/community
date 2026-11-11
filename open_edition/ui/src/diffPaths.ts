// The names of each file in a git diff, read from the header lines git writes for it. The diff parser the page uses
// (react-diff-view's) splits a name with a space in it at the space, keeps the tab git ends such a name with on its
// "---" and "+++" lines, and keeps a quoted name's quotes and escapes; a file's actions (discard it, open it, drag it
// onto the agent) then name a file that is not there.

const ESCAPES: Record<string, number> = { a: 7, b: 8, t: 9, n: 10, v: 11, f: 12, r: 13, '"': 34, "\\": 92 };

/** A name git wrote in double quotes, starting at `chars[start]` (the opening quote), with its C escapes (\" \\ \t,
 *  and \ooo for each byte of a character git quotes) read back: [the name, the index after its closing quote], or null
 *  when it is not one. `chars` is the text as code points. */
function quoted(chars: string[], start: number): [string, number] | null {
  if (chars[start] !== '"') return null;
  const bytes: number[] = [];
  const encoder = new TextEncoder();
  for (let i = start + 1; i < chars.length; ) {
    const c = chars[i];
    // ignoreBOM: a name that starts with U+FEFF keeps it, as git wrote it
    if (c === '"') return [new TextDecoder("utf-8", { ignoreBOM: true }).decode(new Uint8Array(bytes)), i + 1];
    if (c !== "\\") {
      bytes.push(...encoder.encode(c));
      i += 1;
      continue;
    }
    const e = chars[i + 1];
    if (e !== undefined && e in ESCAPES) {
      bytes.push(ESCAPES[e]);
      i += 2;
    } else if (/^[0-7]{3}$/.test(chars.slice(i + 1, i + 4).join("")) && parseInt(chars.slice(i + 1, i + 4).join(""), 8) < 256) {
      bytes.push(parseInt(chars.slice(i + 1, i + 4).join(""), 8));
      i += 4;
    } else {
      return null;
    }
  }
  return null;
}

/** A name as git wrote it on a rename, "---" or "+++" line: read back from its quotes when it is in them. */
export function unquote(name: string): string {
  const chars = Array.from(name);
  const got = quoted(chars, 0);
  return got && got[1] === chars.length ? got[0] : name;
}

/** The two names of a `diff --git` line, each without its a/ or b/: [as it was, as it is], or null. A name that holds
 *  " b/" is read as git wrote it when both names are the same, as they are for a file not renamed (a renamed file's
 *  names are on its rename lines too, which win). */
export function headerPaths(line: string): [string, string] | null {
  if (!line.startsWith("diff --git ")) return null;
  const rest = Array.from(line.slice("diff --git ".length));
  const text = rest.join("");
  let old: string, now: string;
  const first = quoted(rest, 0);
  if (first) {
    const [name, at] = first;
    if (rest[at] !== " ") return null;
    const second = quoted(rest, at + 1);
    if (second && second[1] === rest.length) now = second[0];
    else if (!second && rest.slice(at + 1, at + 3).join("") === "b/") now = rest.slice(at + 1).join("");
    else return null;
    old = name;
  } else if (text.endsWith('"') && text.includes(' "b/')) {
    const at = Array.from(text.slice(0, text.lastIndexOf(' "b/'))).length;
    const second = quoted(rest, at + 1);
    if (!second || second[1] !== rest.length) return null;
    old = rest.slice(0, at).join("");
    now = second[0];
  } else if (rest.length % 2 === 1 && rest[(rest.length - 1) / 2] === " " &&
             rest.slice(2, (rest.length - 1) / 2).join("") === rest.slice((rest.length - 1) / 2 + 3).join("")) {
    old = rest.slice(0, (rest.length - 1) / 2).join("");
    now = rest.slice((rest.length - 1) / 2 + 1).join("");
  } else {
    const m = /^a\/([^\n]+?) b\/([^\n]+)$/.exec(text);       // "." would stop at U+2028 and U+2029, which a name may hold
    return m ? [m[1], m[2]] : null;
  }
  if (!old.startsWith("a/") || !now.startsWith("b/") || old.length < 3 || now.length < 3) return null;
  return [old.slice(2), now.slice(2)];
}

/** One file's names from its part of a diff (its `diff --git` line up to the next one): [as it was, as it is], with
 *  "/dev/null" for a side it has not (an added file was not, a deleted one is not), as the diff parser gives them. */
export function sectionPaths(section: string): [string, string] | null {
  const lines = section.split("\n");          // git ends its lines with "\n" only: a name may hold "\r" or U+2028
  const header = headerPaths(lines[0]);
  if (!header) return null;
  let [old, now] = header;
  let from: string | null = null, to: string | null = null, minus: string | null = null, plus: string | null = null;
  for (const line of lines.slice(1)) {
    if (line.startsWith("@@") || line.startsWith("Binary files ") || line === "GIT binary patch") break;
    if (line.startsWith("rename from ") || line.startsWith("copy from ")) from = unquote(line.slice(line.indexOf(" from ") + 6));
    else if (line.startsWith("rename to ") || line.startsWith("copy to ")) to = unquote(line.slice(line.indexOf(" to ") + 4));
    else if (line.startsWith("--- ")) minus = line.slice(4).replace(/\t$/, "");
    else if (line.startsWith("+++ ")) plus = line.slice(4).replace(/\t$/, "");
  }
  const side = (raw: string | null, prefix: string): string | null => {
    if (raw === null) return null;
    if (raw === "/dev/null") return raw;
    const name = unquote(raw);
    return name.startsWith(prefix) ? name.slice(prefix.length) : null;
  };
  old = from ?? side(minus, "a/") ?? old;
  now = to ?? side(plus, "b/") ?? now;
  return [old, now];
}

/** A diff's parts, one for each file, as git wrote them: split where a line (ended by "\n" only) starts with
 *  "diff --git ". A regex's line anchors would also split at "\r", U+2028 and U+2029, which a hunk's line can hold. */
export function diffSections(diff: string): string[] {
  const out: string[] = [];
  let start = -1;
  let at = 0;
  while (at <= diff.length) {
    if (diff.startsWith("diff --git ", at)) {
      if (start >= 0) out.push(diff.slice(start, at));
      start = at;
    }
    const next = diff.indexOf("\n", at);
    if (next < 0) break;
    at = next + 1;
  }
  if (start >= 0) out.push(diff.slice(start));
  return out;
}
