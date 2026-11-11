// Searching a session's worktree (the workspace's git grep): the matches grouped by file, and each line cut into the
// parts that matched and the parts that did not, for the page to mark. git matched the line; the page only shows where.

export interface Match { path: string; line: number; column: number; text: string; cut: boolean }
export interface Segment { text: string; hit: boolean }

/** The matches by file, in the order git gave them. */
export function byFile(matches: Match[]): { path: string; matches: Match[] }[] {
  const out: { path: string; matches: Match[] }[] = [];
  const at = new Map<string, number>();
  for (const m of matches) {
    let i = at.get(m.path);
    if (i === undefined) { i = out.length; at.set(m.path, i); out.push({ path: m.path, matches: [] }); }
    out[i].matches.push(m);
  }
  return out;
}

/** The expression the page marks with: the query itself, literally, unless it is an expression; null when the
 *  browser cannot read it (git's POSIX expressions and JavaScript's differ at the edges): then nothing is marked. */
export function matcher(query: string, matchCase: boolean, regex: boolean): RegExp | null {
  if (!query) return null;
  const source = regex ? query : query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  try { return new RegExp(source, matchCase ? "g" : "gi"); } catch { return null; }
}

/** A line in parts, the matched ones marked. An expression that matches nothing visible (an empty match) marks
 *  nothing, and is stepped past, never looped on. */
export function segments(text: string, re: RegExp | null): Segment[] {
  if (!re) return [{ text, hit: false }];
  const out: Segment[] = [];
  let last = 0;
  re.lastIndex = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m[0] === "") { re.lastIndex += 1; if (re.lastIndex > text.length) break; continue; }
    if (m.index > last) out.push({ text: text.slice(last, m.index), hit: false });
    out.push({ text: m[0], hit: true });
    last = m.index + m[0].length;
  }
  if (last < text.length || !out.length) out.push({ text: text.slice(last), hit: false });
  return out;
}
