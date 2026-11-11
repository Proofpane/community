// Starting several sessions on one task (a fan-out): each in turn, every result kept, so that one agent that cannot
// start neither stops the others nor leaves the person retrying the ones already started.

export interface Started<T> { created: T[]; failed: { label: string; error: string }[] }

/** Make each of `items` with `make`, in order; what one throws is noted with its label and the next is made. */
export async function startEach<I, T>(items: I[], label: (item: I) => string, make: (item: I) => Promise<T>): Promise<Started<T>> {
  const out: Started<T> = { created: [], failed: [] };
  for (const item of items) {
    try {
      out.created.push(await make(item));
    } catch (e) {
      out.failed.push({ label: label(item), error: e instanceof Error ? e.message : String(e) });
    }
  }
  return out;
}

/** What to tell the person of the ones that did not start: none started, the reason alone (one) or each's; some did,
 *  how many of how many, and why the others did not. Empty when all started. */
export function failedWords<T>(r: Started<T>): string {
  if (!r.failed.length) return "";
  const why = r.failed.length === 1 && !r.created.length ? r.failed[0].error
    : r.failed.map((f) => `${f.label}: ${f.error}`).join(" ");
  return r.created.length ? `Started ${r.created.length} of ${r.created.length + r.failed.length}. ${why}` : why;
}
