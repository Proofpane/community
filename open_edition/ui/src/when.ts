/** Times on the page, as the person reads them: in the time zone their device keeps (for a person in New Zealand,
 *  New Zealand's, not UTC), with the day before the month and on a 24-hour clock whatever language the browser asks
 *  for, so no date reads month first and no time needs an AM or a PM. A tooltip's full time adds the year, the
 *  seconds and the zone's name. A number is a time in seconds, as the server writes them; a string is ISO 8601. */

export type When = number | string | Date | null | undefined;

/** The time zone the device keeps; UTC where a browser cannot say. */
export function deviceZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function toDate(t: When): Date | null {
  if (t == null || t === "") return null;
  const d = t instanceof Date ? t : typeof t === "number" ? new Date(t * 1000) : new Date(t);
  return Number.isNaN(d.getTime()) ? null : d;
}

function parts(d: Date, zone: string, opts: Intl.DateTimeFormatOptions): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of new Intl.DateTimeFormat("en-NZ", { timeZone: zone, hourCycle: "h23", ...opts }).formatToParts(d)) {
    out[p.type] = p.value;
  }
  return out;
}

/** "05:20" */
export function clock(t: When, zone = deviceZone()): string {
  const d = toDate(t);
  if (!d) return "";
  const p = parts(d, zone, { hour: "2-digit", minute: "2-digit" });
  return `${p.hour}:${p.minute}`;
}

/** "4 Oct", with its year when that is not this year */
export function day(t: When, zone = deviceZone(), now: Date = new Date()): string {
  const d = toDate(t);
  if (!d) return "";
  const p = parts(d, zone, { day: "numeric", month: "short", year: "numeric" });
  return p.year === parts(now, zone, { year: "numeric" }).year ? `${p.day} ${p.month}` : `${p.day} ${p.month} ${p.year}`;
}

/** "4 Oct, 05:20" */
export function dayTime(t: When, zone = deviceZone(), now: Date = new Date()): string {
  const d = toDate(t);
  return d ? `${day(d, zone, now)}, ${clock(d, zone)}` : "";
}

/** A time within 12 hours of now, before or after it, by its clock alone, "05:20"; one further off with its day, "4 Oct,
 *  05:20": a reset days away never reads as a time of day (the 12 hours of the windows' own words, agents.ts) */
export function near(t: When, zone = deviceZone(), now: Date = new Date()): string {
  const d = toDate(t);
  if (!d) return "";
  return Math.abs(d.getTime() - now.getTime()) < 12 * 3600 * 1000 ? clock(d, zone) : dayTime(d, zone, now);
}

/** "Sun 05:20" */
export function weekdayTime(t: When, zone = deviceZone()): string {
  const d = toDate(t);
  return d ? `${parts(d, zone, { weekday: "short" }).weekday} ${clock(d, zone)}` : "";
}

/** "Sun 4 Oct 2026, 05:20:09 NZDT": a tooltip's */
export function full(t: When, zone = deviceZone()): string {
  const d = toDate(t);
  if (!d) return "";
  const p = parts(d, zone, { weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "2-digit",
                             minute: "2-digit", second: "2-digit", timeZoneName: "short" });
  return `${p.weekday} ${p.day} ${p.month} ${p.year}, ${p.hour}:${p.minute}:${p.second} ${p.timeZoneName}`;
}

// ---- the progress board's times --------------------------------------------------
// Unlike the rest of the page, the board writes a time in the viewer's own language's format, as Intl.DateTimeFormat
// gives it for the browser's languages, in the device's zone, unless the person chose another zone or another
// language's format (timeChoice.ts, kept in the browser). The functions above keep the page's own way everywhere else.

/** A time zone and a language's format chosen for the board; null for each: the device's zone, the browser's
 *  languages. */
export interface TimeChoice { zone: string | null; locale: string | null }

/** The languages the browser asks for, the one it wants most first, each one Intl takes; none where it cannot say, and
 *  Intl then uses its own. */
export function browserLocales(): string[] {
  if (typeof navigator === "undefined") return [];
  const asked = navigator.languages?.length ? navigator.languages : navigator.language ? [navigator.language] : [];
  return asked.filter((tag) => {
    try { return Intl.DateTimeFormat.supportedLocalesOf([tag]).length > 0; } catch { return false; }
  });
}

/** The languages a choice writes times in: its own, else the browser's. */
export function localesOf(choice: TimeChoice): string[] {
  return choice.locale ? [choice.locale] : browserLocales();
}

/** The zone a choice writes times in: its own, else the device's. */
export function zoneOf(choice: TimeChoice): string {
  return choice.zone || deviceZone();
}

/** A date's day in a zone, as "2026-10-18", to tell whether two times fall on the same day there. */
function dayIn(d: Date, zone: string): string {
  const p: Record<string, string> = {};
  for (const x of new Intl.DateTimeFormat("en-US", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(d)) {
    p[x.type] = x.value;
  }
  return `${p.year}-${p.month}-${p.day}`;
}

/** A time on the board: one today (in the zone) as its time alone, one on another day with its date, and one in
 *  another year with its year too; each in the language's own order, clock and words. */
export function localTime(t: When, choice: TimeChoice, now: Date = new Date()): string {
  const d = toDate(t);
  if (!d) return "";
  const zone = zoneOf(choice);
  const opts: Intl.DateTimeFormatOptions = { timeZone: zone, hour: "numeric", minute: "2-digit" };
  if (dayIn(d, zone) !== dayIn(now, zone)) {
    opts.month = "short";
    opts.day = "numeric";
    if (dayIn(d, zone).slice(0, 4) !== dayIn(now, zone).slice(0, 4)) opts.year = "numeric";
  }
  return new Intl.DateTimeFormat(localesOf(choice), opts).format(d);
}

/** A board time's tooltip: the whole date and time with its zone, in the language's format, then the same moment in
 *  UTC, "(UTC 2026-10-03 16:20:09)", the same in every language. */
export function localFull(t: When, choice: TimeChoice): string {
  const d = toDate(t);
  if (!d) return "";
  const local = new Intl.DateTimeFormat(localesOf(choice), { timeZone: zoneOf(choice), dateStyle: "full", timeStyle: "long" }).format(d);
  return `${local} (UTC ${d.toISOString().slice(0, 19).replace("T", " ")})`;
}

/** How long until a time ahead, as the board says it beside a reset: "in 12 min" (a part of a minute counted as one),
 *  "in 3 h 5 min", "in 2 days" from two days on, and "in under a minute"; nothing for a time that has come. */
export function untilWords(t: When, now: Date = new Date()): string {
  const d = toDate(t);
  if (!d) return "";
  const seconds = (d.getTime() - now.getTime()) / 1000;
  if (seconds <= 0) return "";
  if (seconds < 60) return "in under a minute";
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) return `in ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours >= 48) return `in ${Math.floor(hours / 24)} days`;
  return `in ${hours} h${minutes % 60 ? ` ${minutes % 60} min` : ""}`;
}
