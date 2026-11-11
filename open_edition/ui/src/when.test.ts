import { describe, expect, it, vi } from "vitest";
import { browserLocales, clock, day, dayTime, deviceZone, full, localFull, localTime, near, untilWords, weekdayTime } from "./when";

// 2026-10-03T16:20:09Z: Sunday 4 October, 05:20:09 in New Zealand (daylight time, UTC+13)
const T = Date.UTC(2026, 9, 3, 16, 20, 9) / 1000;
const NOW = new Date(Date.UTC(2026, 9, 3, 18));
const NZ = "Pacific/Auckland";

describe("times as the person reads them", () => {
  it("are in the device's zone, never UTC: New Zealand's for a device there", () => {
    expect(clock(T, NZ)).toBe("05:20");
    expect(dayTime(T, NZ, NOW)).toBe("4 Oct, 05:20");
    expect(weekdayTime(T, NZ)).toBe("Sun 05:20");
    expect(full(T, NZ)).toBe("Sun 4 Oct 2026, 05:20:09 NZDT");
  });

  it("read day first on a 24-hour clock in any zone, whatever the browser's language", () => {
    expect(dayTime(T, "America/New_York", NOW)).toBe("3 Oct, 12:20");
    expect(clock(Date.UTC(2026, 9, 3, 22, 5) / 1000, "America/New_York")).toBe("18:05");
    expect(clock(Date.UTC(2026, 9, 3, 11, 0) / 1000, NZ)).toBe("00:00");
  });

  it("take seconds, ISO strings and dates alike, and name the year only when it is not this one", () => {
    expect(dayTime("2026-10-03T16:20:09Z", NZ, NOW)).toBe("4 Oct, 05:20");
    expect(clock(new Date(T * 1000), NZ)).toBe("05:20");
    expect(day(Date.UTC(2025, 0, 2, 3) / 1000, NZ, NOW)).toBe("2 Jan 2025");
    expect(day(T, NZ, NOW)).toBe("4 Oct");
  });

  it("say nothing of no time or one that cannot be read", () => {
    for (const t of [null, undefined, "", "not a time"]) {
      expect([clock(t, NZ), day(t, NZ), dayTime(t, NZ), weekdayTime(t, NZ), full(t, NZ), near(t, NZ)]).toEqual(["", "", "", "", "", ""]);
    }
  });

  it("give a time near now by its clock and one further off with its day, so a reset days away is not read as today's", () => {
    const now = new Date(T * 1000);
    const H = 3600;
    expect(near(T + 3 * H, NZ, now)).toBe("08:20");                    // within 12 hours: the clock alone
    expect(near(T - 3 * H, NZ, now)).toBe("02:20");                    // before now as well
    expect(near(T + 12 * H - 1, NZ, now)).toBe("17:20");
    expect(near(T + 12 * H, NZ, now)).toBe("4 Oct, 17:20");            // 12 hours off: with its day
    expect(near(T + 3 * 24 * H, NZ, now)).toBe("7 Oct, 05:20");        // a seven-day window's reset three days away
    expect(near(T - 2 * 24 * H, NZ, now)).toBe("2 Oct, 05:20");
    expect(near(Date.UTC(2027, 0, 2, 3) / 1000, NZ, now)).toBe("2 Jan 2027, 16:00");   // another year: its year too
  });
});

// The progress board's own times: each expected value is computed here with Intl, for
// two languages and two zones, never written out, so that the tests hold on any machine and no other script's words are
// in this file.
describe("the progress board's times", () => {
  const LOCALES = ["en-US", "de-DE"];
  const ZONES = [NZ, "America/New_York"];
  const at = (s: number) => new Date(s * 1000);
  const fmt = (locale: string, zone: string, opts: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat([locale], { timeZone: zone, ...opts }).format(at(T));
  const CLOCK: Intl.DateTimeFormatOptions = { hour: "numeric", minute: "2-digit" };

  it("write a time today as its time alone, in the chosen zone and the chosen language's own format", () => {
    const now = at(T + 600);                 // ten minutes on: the same day in both zones
    const seen = new Set<string>();
    for (const zone of ZONES) for (const locale of LOCALES) {
      const got = localTime(T, { zone, locale }, now);
      expect(got).toBe(fmt(locale, zone, CLOCK));
      seen.add(got);
    }
    expect(seen.size).toBe(4);                // two zones and two formats: four ways of writing it
  });

  it("write a time on another day with its date, and one in another year with its year", () => {
    for (const zone of ZONES) for (const locale of LOCALES) {
      expect(localTime(T, { zone, locale }, at(T + 2 * 86400))).toBe(fmt(locale, zone, { month: "short", day: "numeric", ...CLOCK }));
      expect(localTime(T, { zone, locale }, at(T + 400 * 86400)))
        .toBe(fmt(locale, zone, { year: "numeric", month: "short", day: "numeric", ...CLOCK }));
    }
  });

  it("read today as the chosen zone's day: the same moment is today in one zone and yesterday in another", () => {
    const now = at(Date.UTC(2026, 9, 4, 10, 0) / 1000);   // 23:00 on the 4th in New Zealand, 06:00 on the 4th in New York
    expect(localTime(T, { zone: NZ, locale: "en-US" }, now)).toBe(fmt("en-US", NZ, CLOCK));
    expect(localTime(T, { zone: "America/New_York", locale: "en-US" }, now))
      .toBe(fmt("en-US", "America/New_York", { month: "short", day: "numeric", ...CLOCK }));
  });

  it("are in the device's zone and the browser's languages until another is chosen", () => {
    const langs = vi.spyOn(navigator, "languages", "get").mockReturnValue(["de-DE", "not a language tag", "en-US"]);
    try {
      expect(browserLocales()).toEqual(["de-DE", "en-US"]);
      expect(localTime(T, { zone: null, locale: null }, at(T + 60)))
        .toBe(new Intl.DateTimeFormat(["de-DE", "en-US"], { timeZone: deviceZone(), ...CLOCK }).format(at(T)));
      expect(localTime(T, { zone: NZ, locale: "en-US" }, at(T + 60))).toBe(fmt("en-US", NZ, CLOCK));
    } finally {
      langs.mockRestore();
    }
  });

  it("give the whole date, the zone and the same moment in UTC in a tooltip", () => {
    for (const zone of ZONES) for (const locale of LOCALES) {
      expect(localFull(T, { zone, locale })).toBe(`${fmt(locale, zone, { dateStyle: "full", timeStyle: "long" })} (UTC 2026-10-03 16:20:09)`);
    }
    expect([localTime(null, { zone: NZ, locale: "en-US" }), localFull("not a time", { zone: NZ, locale: "en-US" })]).toEqual(["", ""]);
  });

  it("say how long until a reset ahead, a part of a minute counted as one, and nothing for one that has come", () => {
    const now = at(T);
    const after = (s: number) => untilWords(T + s, now);
    expect(after(12 * 60)).toBe("in 12 min");
    expect(after(11 * 60 + 1)).toBe("in 12 min");
    expect(after(30)).toBe("in under a minute");
    expect(after(60)).toBe("in 1 min");
    expect(after(59 * 60 + 30)).toBe("in 1 h");
    expect(after(90 * 60)).toBe("in 1 h 30 min");
    expect(after(48 * 3600 - 60)).toBe("in 47 h 59 min");
    expect(after(48 * 3600)).toBe("in 2 days");
    expect(after(3 * 86400 + 7200)).toBe("in 3 days");
    expect([after(0), after(-60), untilWords(null, now)]).toEqual(["", "", ""]);
  });
});
