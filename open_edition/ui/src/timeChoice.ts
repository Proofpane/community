// The progress board's times: the device's zone and the browser's languages'
// format, unless the person chose another zone or another language's format here, remembered on this device as the
// theme is (theme.ts). A value this page cannot use (a zone or a language Intl does not take) is never kept, and one
// stored that it cannot use reads as the default.
import { create } from "zustand";
import type { TimeChoice } from "./when";

export const KEY = "pp-board-times";

/** Whether Intl takes `zone` as a time zone. */
export function validZone(zone: unknown): zone is string {
  if (typeof zone !== "string" || !zone || zone.length > 64) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}

/** Whether Intl takes `locale` as a language whose format it writes times in. */
export function validLocale(locale: unknown): locale is string {
  if (typeof locale !== "string" || !locale || locale.length > 35) return false;
  try {
    return Intl.DateTimeFormat.supportedLocalesOf([locale]).length > 0;
  } catch {
    return false;
  }
}

/** The choice this device keeps; the default (null, null) where it keeps none it can use. */
export function readTimeChoice(): TimeChoice {
  try {
    const kept = JSON.parse(localStorage.getItem(KEY) || "null") as Partial<TimeChoice> | null;
    if (kept && typeof kept === "object") {
      return { zone: validZone(kept.zone) ? kept.zone : null, locale: validLocale(kept.locale) ? kept.locale : null };
    }
  } catch { /* the default */ }
  return { zone: null, locale: null };
}

/** The board's time choice, and setting it: a part given as null goes back to the default, one Intl does not take is
 *  refused (left as it was), and what is set is kept on this device. */
export const useTimeChoice = create<TimeChoice & { set: (change: Partial<TimeChoice>) => void }>((set, get) => ({
  ...readTimeChoice(),
  set: (change) => {
    const zone = change.zone === undefined ? get().zone : change.zone === null ? null : validZone(change.zone) ? change.zone : get().zone;
    const locale = change.locale === undefined ? get().locale
      : change.locale === null ? null : validLocale(change.locale) ? change.locale : get().locale;
    try { localStorage.setItem(KEY, JSON.stringify({ zone, locale })); } catch { /* this page only */ }
    set({ zone, locale });
  },
}));

/** Languages offered to choose a format from, beside the browser's own: a tag each, named in English by Intl. */
export const LOCALES = ["en-US", "en-GB", "en-AU", "en-NZ", "en-CA", "en-IN", "de-DE", "fr-FR", "es-ES", "es-MX", "it-IT",
  "nl-NL", "pt-BR", "pt-PT", "sv-SE", "da-DK", "nb-NO", "fi-FI", "pl-PL", "cs-CZ", "tr-TR", "ru-RU", "uk-UA", "el-GR",
  "he-IL", "ar-EG", "hi-IN", "th-TH", "id-ID", "vi-VN", "ja-JP", "ko-KR", "zh-CN", "zh-TW"];

/** A language's name in English ("German (Germany)"), or its tag where Intl cannot name it. */
export function localeName(tag: string): string {
  try {
    return new Intl.DisplayNames(["en"], { type: "language" }).of(tag) || tag;
  } catch {
    return tag;
  }
}

/** The time zones to choose from: the device's, UTC, and every zone Intl lists, each once; the device's zone and UTC
 *  where it cannot list them. UTC is added: the list V8 gives (418 zones on Node 22.22.0; Chrome and Edge run V8,
 *  not measured here) names none of UTC, Etc/UTC or GMT, though Intl takes UTC as a zone. The device's zone is here as
 *  well as in the setting's first entry, This device's (zone), on purpose: that entry follows the device wherever it
 *  is, and the zone chosen from this list stays that zone. */
export function zones(device: string): string[] {
  let all: string[] = [];
  try {
    all = Intl.supportedValuesOf("timeZone");
  } catch { /* the device's and UTC alone */ }
  return [...new Set([device, "UTC", ...all])];
}
