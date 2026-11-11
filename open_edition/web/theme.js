"use strict";
// The light or dark palette the person chose in the app (ui/src/theme.ts keeps the choice under "pp-theme" on this
// site), put on the page before it shows, as the app puts it: data-theme="bright" for light (style.css).
(function () {
  let pref = null;
  try { pref = localStorage.getItem("pp-theme"); } catch (e) { /* no storage here: the default */ }
  const light = pref === "light" ||
    (pref === "system" && typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: light)").matches);
  document.documentElement.dataset.theme = light ? "bright" : "dark";
})();
