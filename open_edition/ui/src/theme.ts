// The app's theme: dark (the default: an ADE is open all day next to a terminal), light, or the system's, remembered on
// this device. The resolved one is set on <html> as data-theme: "dark", or "bright" for light, the value Proofpane's own
// frontend sets, which the stylesheet's light palette keys on.
import { create } from "zustand";

export type ThemePref = "dark" | "light" | "system";
const KEY = "pp-theme";

function stored(): ThemePref {
  try { const v = localStorage.getItem(KEY); if (v === "dark" || v === "light" || v === "system") return v; } catch { /* default */ }
  return "dark";
}
const systemLight = () => typeof matchMedia !== "undefined" && matchMedia("(prefers-color-scheme: light)").matches;
const resolve = (pref: ThemePref): "dark" | "light" => (pref === "system" ? (systemLight() ? "light" : "dark") : pref);

function apply(theme: "dark" | "light") {
  if (typeof document !== "undefined") document.documentElement.dataset.theme = theme === "light" ? "bright" : "dark";
}

export const useTheme = create<{ pref: ThemePref; theme: "dark" | "light"; set: (p: ThemePref) => void }>((set) => {
  const pref = stored();
  const theme = resolve(pref);
  apply(theme);
  if (typeof matchMedia !== "undefined") {
    matchMedia("(prefers-color-scheme: light)").addEventListener?.("change", () => {
      const p = useTheme.getState().pref;
      if (p === "system") { const t = resolve(p); apply(t); set({ theme: t }); }
    });
  }
  return {
    pref, theme,
    set: (p) => {
      try { localStorage.setItem(KEY, p); } catch { /* this page only */ }
      const t = resolve(p);
      apply(t);
      set({ pref: p, theme: t });
    },
  };
});
