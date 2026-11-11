// A checkpoint's fields as the page shows them: a list's items, text as one, anything else as its JSON,
// and blank text as nothing. A linked session's card and the slash menu's Lanes both read them so; each shows what it
// reads through visible(), so every character shows itself.
export const fieldText = (v: unknown): string => (typeof v === "string" ? v : JSON.stringify(v));
export const fieldBlank = (v: unknown): boolean => v == null || (typeof v === "string" && !v.trim());
export const fieldItems = (v: unknown): string[] =>
  (Array.isArray(v) ? v.map(fieldText) : fieldBlank(v) ? [] : [fieldText(v)]);
