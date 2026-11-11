import type { Usage } from "./api";

/** A session's tokens as its program wrote them down (usage.py): what went in, cache included, and what came out;
 *  null when there is nothing to show (another program, or nothing written yet). Claude Code counts what it read from
 *  its cache apart from its input; Codex counts it within. */
export function tokensOf(u: Usage | null | undefined): { total: number; in: number; out: number; cached: number } | null {
  if (!u || !u.available) return null;
  const n = (v: unknown) => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : 0);
  const r = u as unknown as Record<string, unknown>;
  const codex = u.program === "codex";
  const cached = codex ? n(u.cached_input_tokens) : n(r.cache_read_input_tokens);
  const input = codex ? n(u.input_tokens) : n(u.input_tokens) + n(r.cache_creation_input_tokens) + cached;
  const out = n(u.output_tokens);
  return input + out > 0 ? { total: input + out, in: input, out, cached } : null;
}

/** A count of tokens in a few characters: 950, 12.3k, 4.1M. */
export function count(n: number): string {
  if (n < 1000) return String(Math.round(n));
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(n < 10_000_000 ? 1 : 0)}M`;
}
