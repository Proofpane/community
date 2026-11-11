// The layout cases' measure (tests/test_slash_menu_layout.py): builds layout.html with Vite into OUT, then, in a
// headless Chromium the caller names, draws each chat box at 360 and 390 pixels wide and prints, as one line of JSON,
// each row's height, its tallest control's, how far right its controls reach, its edge and its narrowest
// control's width, keyed "<box>.<row>.<width>". Usage, from ui/:
//   node scripts/measure-layout.mjs <the playwright package's folder> <a Chromium executable> <OUT>
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, extname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const [playwright, executable, out] = process.argv.slice(2);
const ui = join(dirname(fileURLToPath(import.meta.url)), "..");
await build({
  configFile: false, root: ui, base: "./", logLevel: "silent", plugins: [react(), tailwindcss()],
  build: { outDir: out, emptyOutDir: true, rollupOptions: { input: join(ui, "layout.html") } },
});
const { chromium } = createRequire(import.meta.url)(playwright);
const browser = await chromium.launch({ headless: true, executablePath: executable, args: ["--no-sandbox"] });
// a screen that takes taps, as a phone's does
const page = await (await browser.newContext({ hasTouch: true })).newPage();
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" };
// the built page served from OUT at localhost, a secure context as the app's own address is, and never asked of
// the network: nothing leaves the machine. What the page throws is said, for a run that fails
page.on("pageerror", (e) => process.stderr.write(`page error: ${e.message}\n`));
await page.route("**/*", (route) => route.abort());     // anything else: refused, nothing leaves the machine
await page.route("http://localhost/**", (route) => {
  const path = new URL(route.request().url()).pathname;
  let body;
  try { body = readFileSync(join(out, path)); } catch { return route.fulfill({ status: 404, body: "" }); }
  return route.fulfill({ body, contentType: TYPES[extname(path)] ?? "application/octet-stream" });
});
// each row found by a control it holds today: the chat's settings by its model picker, its box by the message box,
// a linked session's box by its Review button
const SETTINGS = 'select[aria-label="Model"]';
const INPUT = 'textarea[aria-label="Message to Claude"]';
const REVIEW = 'button:has-text("Review")';
const ROWS = {
  chat: { settings: SETTINGS, input: INPUT }, chatbusy: { input: INPUT }, link: { controls: REVIEW },
  card: { controls: REVIEW },
};
// what is tapped first: a card's Instruct opens its box
const BEFORE = { card: 'button:has-text("Instruct")' };
const result = {};
const measure = async (key, selector) => {
  const control = await page.waitForSelector(selector, { timeout: 15000 });
  result[key] = await control.evaluate((node) => {
    const line = node.parentElement;
    const box = line.getBoundingClientRect();
    const shown = [...line.children].filter((c) => c.getBoundingClientRect().height > 0);
    // a control: a button, a picker or a box, or what holds one (the slash button's own wrapper)
    const CONTROLS = "button, select, textarea, input";
    const control = (c) => c.matches(CONTROLS) || !!c.querySelector(CONTROLS);
    const controls = shown.filter(control).map((c) => c.getBoundingClientRect());
    // the row's height and its tallest control's (one line); how much in it is no control (a note goes under
    // the row, not in it); how far right it reaches and the edge it must stay within (the row's and the
    // screen's); and its narrowest control's width
    return { height: box.height, tallest: Math.max(...controls.map((r) => r.height)),
      others: shown.length - controls.length, right: Math.max(...shown.map((c) => c.getBoundingClientRect().right)),
      edge: Math.min(box.right, window.innerWidth), narrowest: Math.min(...controls.map((r) => r.width)) };
  });
};
for (const width of [360, 390]) {
  await page.setViewportSize({ width, height: 800 });
  for (const [box, rows] of Object.entries(ROWS)) {
    await page.goto(`http://localhost/layout.html?which=${box}`);
    if (BEFORE[box]) await (await page.waitForSelector(BEFORE[box], { timeout: 15000 })).tap();
    for (const [row, selector] of Object.entries(rows)) await measure(`${box}.${row}.${width}`, selector);
    // a busy chat's row holds Stop and the microphone too, so it is measured with every button it has
    if (box === "chatbusy") {
      result[`chatbusy.buttons.${width}`] = { stop: await page.getByRole("button", { name: /Stop/ }).count(),
        mic: await page.getByRole("button", { name: "Speak instead of typing" }).count() };
    }
    // the settings' two buttons keep their names where only their icons show
    if (box === "chat") {
      result[`chat.named.${width}`] = { agents: await page.getByRole("button", { name: "Agents", exact: true }).count(),
        connectors: await page.getByRole("button", { name: /^Connectors/ }).count() };
    }
  }
}
// at a desktop's width, the chat in a pane as narrow as a split leaves it: its settings row wraps rather than running
// past the pane's edge
await page.setViewportSize({ width: 1280, height: 800 });
await page.goto("http://localhost/layout.html?which=pane");
await measure("pane.settings.1280", SETTINGS);
// the slash menu at a phone's width: its button a tap, an entry a tap, the entry's modal within the screen with its
// words wrapped in it (nothing to scroll sideways), and a tap on the dimmed page beside it closing it; what is not
// there is said as not found, for the case to fail on
const TITLES = { account: "Account", lanes: "Lanes", map: "Agent map" };
const absent = () => null;
for (const width of [360, 390]) {
  await page.setViewportSize({ width, height: 800 });
  for (const box of ["chat", "link"]) {
    for (const [entry, title] of Object.entries(TITLES)) {
      const key = `${box}.menu.${entry}.${width}`;
      await page.goto(`http://localhost/layout.html?which=${box}`);
      const button = await page.waitForSelector('button[aria-label="Open the slash menu"]', { timeout: 5000 })
        .catch(absent);
      if (!button) { result[key] = { button: false }; continue; }
      await button.tap();
      const option = await page.waitForSelector(`[role="option"]:has-text("/${entry}")`, { timeout: 5000 })
        .catch(absent);
      if (!option) { result[key] = { button: true, option: false }; continue; }
      await option.tap();
      const named = `[role="dialog"][aria-label="${title}"]`;
      const dialog = await page.waitForSelector(named, { timeout: 5000 }).catch(absent);
      if (!dialog) { result[key] = { button: true, option: true, dialog: false }; continue; }
      await page.waitForTimeout(500);                   // its reads land
      const fit = await dialog.evaluate((d) => {
        const r = d.getBoundingClientRect();
        const body = d.children[1];
        return { left: r.left, right: r.right, screen: window.innerWidth,
          sideways: body.scrollWidth - body.clientWidth };
      });
      await page.touchscreen.tap(8, 8);                 // the dimmed page, beside the modal
      await page.waitForTimeout(200);
      result[key] = { button: true, option: true, dialog: true, ...fit, closed: (await page.$(named)) === null };
    }
  }
}
await browser.close();
console.log(JSON.stringify(result));
