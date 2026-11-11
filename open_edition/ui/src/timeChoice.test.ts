import { afterEach, describe, expect, it } from "vitest";
import { KEY, LOCALES, localeName, readTimeChoice, useTimeChoice, validLocale, validZone, zones } from "./timeChoice";

afterEach(() => {
  localStorage.clear();
  useTimeChoice.setState({ zone: null, locale: null });
});

describe("the board's time setting", () => {
  it("is the device's zone and the browser's languages until one is chosen", () => {
    expect(readTimeChoice()).toEqual({ zone: null, locale: null });
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it("is kept on this device once chosen, and read back as a page loaded again reads it", () => {
    useTimeChoice.getState().set({ zone: "America/New_York" });
    useTimeChoice.getState().set({ locale: "de-DE" });
    expect(useTimeChoice.getState()).toMatchObject({ zone: "America/New_York", locale: "de-DE" });
    expect(JSON.parse(localStorage.getItem(KEY)!)).toEqual({ zone: "America/New_York", locale: "de-DE" });
    expect(readTimeChoice()).toEqual({ zone: "America/New_York", locale: "de-DE" });
    useTimeChoice.getState().set({ zone: null });                       // back to the device's zone, the format kept
    expect(readTimeChoice()).toEqual({ zone: null, locale: "de-DE" });
  });

  it("refuses a zone or a language Intl does not take, keeping what it had", () => {
    useTimeChoice.getState().set({ zone: "Pacific/Auckland", locale: "en-GB" });
    useTimeChoice.getState().set({ zone: "Mars/Olympus", locale: "not a language" });
    expect(readTimeChoice()).toEqual({ zone: "Pacific/Auckland", locale: "en-GB" });
  });

  it("reads one stored that it cannot use as the default", () => {
    for (const stored of ["{not json", "null", "[]", JSON.stringify({ zone: "Mars/Olympus", locale: 7 }), JSON.stringify({ zone: 5 })]) {
      localStorage.setItem(KEY, stored);
      expect(readTimeChoice()).toEqual({ zone: null, locale: null });
    }
    localStorage.setItem(KEY, JSON.stringify({ zone: "Europe/Berlin", locale: "x-not-a-tag-that-is-far-too-long-for-a-tag" }));
    expect(readTimeChoice()).toEqual({ zone: "Europe/Berlin", locale: null });
  });

  it("offers every zone Intl knows, the device's among them, and languages Intl takes, named in English", () => {
    expect(validZone("UTC") && validZone("Pacific/Auckland") && !validZone("") && !validZone(null)).toBe(true);
    expect(validLocale("en-NZ") && !validLocale("") && !validLocale(undefined)).toBe(true);
    const all = zones("Pacific/Auckland");
    expect(all).toContain("Pacific/Auckland");
    expect(all).toContain("America/New_York");
    expect(zones("Made/Up")[0]).toBe("Made/Up");
    // UTC is offered whatever the device's zone, though V8's list of zones names none of UTC, Etc/UTC or GMT
    expect(zones("Pacific/Auckland")).toContain("UTC");
    expect(validZone("UTC")).toBe(true);
    expect(new Set(zones("UTC")).size).toBe(zones("UTC").length);           // each zone once
    expect(LOCALES.every(validLocale)).toBe(true);
    expect(new Set(LOCALES).size).toBe(LOCALES.length);
    expect(localeName("de-DE")).toBe(new Intl.DisplayNames(["en"], { type: "language" }).of("de-DE"));
  });
});
