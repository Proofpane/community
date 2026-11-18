// The fit cases' measure (tests/test_phone_fit_layout.py): builds layout.html with Vite into OUT, then, in a headless
// Chromium the caller names, draws its pages at 360 and 390 pixels wide and prints, as one line of JSON, the strip that
// says Claude Code is not signed in against what it holds, keyed "banner.<page>.<width>", and on every page each
// element of a fixed height that holds words, keyed "strips.<page>.<width>", with how far anything in it reaches past
// its top or bottom: a class such as h-9, h-[36px] or size-8, with any variant before it, or one of index.css's own
// classes that set a height (.btn, .btn-sm, .icon-btn, .input, .kbd, .pill, .palette-item). And on every page each
// field that takes text, keyed "fields.<page>.<width>", with the font size it computes to; whether the screen's
// pointer is coarse, as a phone's, keyed "pointer.<width>"; the New session form's fields as it opens, keyed
// "fields.newsessionfrom.<width>"; and on a screen without touch at a desktop's width the fields of six pages,
// keyed "fields.fine.<page>", with "pointer.fine". Usage, from ui/:
//   node scripts/measure-fit.mjs <the playwright package's folder> <a Chromium executable> <OUT>
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
// the built page served from OUT at localhost and never asked of the network: nothing leaves the machine. What the
// page throws is said, for a run that fails
const serve = async (p) => {
  p.on("pageerror", (e) => process.stderr.write(`page error: ${e.message}\n`));
  await p.route("**/*", (route) => route.abort());
  await p.route("http://localhost/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    let body;
    try { body = readFileSync(join(out, path)); } catch { return route.fulfill({ status: 404, body: "" }); }
    return route.fulfill({ body, contentType: TYPES[extname(path)] ?? "application/octet-stream" });
  });
};
await serve(page);
// each page found by something it draws; a card's menu opened by its More button, so its items are measured too (a
// linked session's conversation has no such menu)
const SHOWN = { banner: "text=Sign in to Claude", bannerwho: "text=Sign in to Claude", diff: "text=Unified",
  chat: 'textarea[aria-label="Message to Claude"]', chatbusy: 'textarea[aria-label="Message to Claude"]',
  link: 'button:has-text("Review")', linknarrow: 'button:has-text("Review")', card: 'button:has-text("Instruct")',
  agents: 'aside[aria-label="Agents"]', modal: "button[data-modal-close]", modalword: "button[data-modal-close]",
  palette: "text=Rename this session", newsession: 'button:has-text("From a pull request")',
  history: "text=Make sign-in work", signin: "input#email", editor: ".cm-content", linkform: "input#pair-name",
  deskkeys: 'button[aria-label="Stop"]', deskkeyspane: 'button[aria-label="Stop"]' };
// what is tapped before the strips are measured: the New session form's pull request, so its Task row names it
const GIVEN = {
  newsession: ['button:has-text("From a pull request")', 'button:has-text("Make the strips fit a phone")'],
};
// what a page opens before it is measured: the code editor's search panel, by the editor's own keys, from its text
const OPEN = {
  editor: async (p) => {
    await p.click(".cm-content");
    await p.keyboard.press("ControlOrMeta+f");
    await p.waitForSelector(".cm-panels input", { timeout: 15000 });
  },
};
const MORE = 'button[aria-label="More for MacBook Pro"]';
const result = {};
// every element of a fixed height that holds words: how far its words, or anything laid out in it, reach past its top
// or bottom (what is positioned on its own, a tab's underline, is left out); inputs hold one line by their nature
const strips = () => page.evaluate(() => {
  const FIXED = /^(?:[^\s:]+:)*!?(?:h-(?:\d+(?:\.\d+)?|px|\[[^\]]+\])|size-\S+)$/;
  const CLASSES = new Set(["btn", "btn-sm", "icon-btn", "input", "kbd", "pill", "palette-item"]);
  const own = (node, top) => {
    for (let n = node; n && n !== top; n = n.parentElement) {
      if (n.nodeType === 1 && ["absolute", "fixed"].includes(getComputedStyle(n).position)) return false;
    }
    return true;
  };
  return [...document.querySelectorAll("[class]")]
    .filter((el) => [...el.classList].some((c) => FIXED.test(c) || CLASSES.has(c))
    && !el.matches("input, select, textarea") && el.textContent.trim() && el.getBoundingClientRect().height > 0)
    .map((el) => {
    const box = el.getBoundingClientRect();
    let past = 0;
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let t = walk.nextNode(); t; t = walk.nextNode()) {
      if (!t.textContent.trim() || !own(t.parentElement, el)) continue;
      const range = document.createRange();
      range.selectNodeContents(t);
      for (const r of range.getClientRects()) past = Math.max(past, box.top - r.top, r.bottom - box.bottom);
    }
    for (const d of el.querySelectorAll("*")) {
      const r = d.getBoundingClientRect();
      if (r.height > 0 && own(d, el)) past = Math.max(past, box.top - r.top, r.bottom - box.bottom);
    }
    // and how far it reaches past the right of the box it sits in, its words run sideways out of their column
    const wide = box.right - el.parentElement.getBoundingClientRect().right;
    return { cls: String(el.className).slice(0, 90), words: el.textContent.trim().slice(0, 60),
      height: Math.round(box.height), past: Math.round(past * 10) / 10, wide: Math.round(wide * 10) / 10 };
  });
});
// every field that takes text (an input of a kind that takes words, a text box, a select, and an area that is edited in
// place, a code editor's text, counted once at its outermost editable element): what it is and the font size it
// computes to, which iOS zooms the page in for when it is under 16 pixels and the field takes focus
const fields = (p = page) => p.evaluate(() => {
  const NOT_TEXT = ["checkbox", "radio", "range", "file", "color", "button", "submit", "reset", "image", "hidden"];
  return [...document.querySelectorAll("input, textarea, select, [contenteditable]")]
    .filter((el) => !(el.tagName === "INPUT" && NOT_TEXT.includes(el.type)))
    .filter((el) => !el.hasAttribute("contenteditable") || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)
      || (el.isContentEditable && !el.parentElement?.isContentEditable))
    .map((el) => ({
      what: `${el.tagName.toLowerCase()}${el.tagName === "INPUT" ? `[${el.type}]` : ""}${
        el.isContentEditable ? "[contenteditable]" : ""} ${el.id || el.getAttribute("aria-label")
        || el.getAttribute("placeholder") || el.name || el.className || ""}`.trim().slice(0, 80),
      size: parseFloat(getComputedStyle(el).fontSize) }));
});
for (const width of [360, 390]) {
  await page.setViewportSize({ width, height: 800 });
  // a phone's pointer, a finger: what (pointer: coarse) asks
  result[`pointer.${width}`] = await page.evaluate(() => matchMedia("(pointer: coarse)").matches);
  for (const [name, selector] of Object.entries(SHOWN)) {
    await page.goto(`http://localhost/layout.html?which=${name}`);
    await page.waitForSelector(selector, { timeout: 15000 });
    // the New session form as it opens, before a pull request is tapped: its From row's Start from
    if (name === "newsession") {
      await page.waitForTimeout(300);
      result[`fields.newsessionfrom.${width}`] = await fields();
    }
    for (const tap of GIVEN[name] ?? []) await (await page.waitForSelector(tap, { timeout: 15000 })).tap();
    if (OPEN[name]) await OPEN[name](page);
    await page.waitForTimeout(300);                    // its reads land
    if (name.startsWith("banner")) {
      // the strip, its words and its button: where each sits against the strip, how many lines the words take, and
      // whether the button's name is whole
      result[`banner.${name}.${width}`] = await page.evaluate(() => {
        const button = [...document.querySelectorAll("button")].find((b) => b.textContent === "Sign in to Claude");
        const strip = button.parentElement;
        const box = strip.getBoundingClientRect();
        const range = document.createRange();
        range.selectNodeContents(strip.children[1]);
        const lines = new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size;
        const parts = [...strip.children].map((c) => c.getBoundingClientRect()).concat([range.getBoundingClientRect()]);
        // its right edge is the furthest any part of it reaches, the button's most often, not the strip's own box
        return { top: box.top, bottom: box.bottom, height: box.height, right: Math.max(...parts.map((r) => r.right)),
          screen: window.innerWidth, highest: Math.min(...parts.map((r) => r.top)),
          lowest: Math.max(...parts.map((r) => r.bottom)), lines, cut: button.scrollWidth - button.clientWidth };
      });
    }
    result[`strips.${name}.${width}`] = await strips();
    result[`fields.${name}.${width}`] = await fields();
    // the New session form's Task row: how far right its pill and buttons reach against it and the screen, and on
    // how many lines the pill's words stand
    if (name === "newsession") result[`newsession.taskrow.${width}`] = await taskRow();
    if (name === "newsession") result[`newsession.fromrow.${width}`] = await fromRow();
    if (name === "history") result[`history.row.${width}`] = await historyRow();
    if (name.startsWith("deskkeys")) result[`${name}.row.${width}`] = await deskRow();
    const more = await page.$(MORE);
    if (more) {
      await more.tap();
      await page.waitForTimeout(200);
      result[`strips.${name}menu.${width}`] = await strips();
    }
  }
}
// the New session form's Task row: its pill's words' lines, how far right its parts reach against the row and the
// screen, and on how many rows they stand (a part that starts below the pill's bottom); a function, so that the
// loop above it can call it
function taskRow() { return page.evaluate(() => {
  const pill = [...document.querySelectorAll(".pill")].find((p) => p.textContent.includes("From pull request"));
  const row = pill.parentElement;
  const box = row.getBoundingClientRect();
  const parts = [...row.children].map((c) => c.getBoundingClientRect());
  // its words' own lines: the rects of its text alone, not of its icon or its button
  const tops = [];
  const walk = document.createTreeWalker(pill, NodeFilter.SHOW_TEXT);
  for (let t = walk.nextNode(); t; t = walk.nextNode()) {
    if (!t.textContent.trim()) continue;
    const range = document.createRange();
    range.selectNodeContents(t);
    for (const r of range.getClientRects()) if (r.width > 0) tops.push(Math.round(r.top));
  }
  const lines = new Set(tops).size;
  const p = pill.getBoundingClientRect();
  return { right: Math.max(...parts.map((r) => r.right)), edge: Math.min(box.right, window.innerWidth), lines,
    rows: parts.some((r) => r.top >= p.bottom - 0.5) ? 2 : 1 };
}); }
// a Mac's session's row in the History list: on how many rows its three columns stand (a column that starts below
// the bottom of the rows above it begins a row), each column's width, and how far right they reach
function historyRow() { return page.evaluate(() => {
  const row = document.querySelector("li > button");
  const box = row.getBoundingClientRect();
  const parts = [...row.children].map((c) => c.getBoundingClientRect());
  let rows = 0, bottom = -Infinity;
  for (const r of [...parts].sort((a, b) => a.top - b.top)) {
    if (r.top >= bottom - 0.5) rows++;
    bottom = Math.max(bottom, r.bottom);
  }
  return { rows, height: box.height, widths: parts.map((r) => Math.round(r.width * 10) / 10),
    right: Math.max(...parts.map((r) => r.right)), edge: Math.min(box.right, window.innerWidth) };
}); }
// the keys under a terminal on a desktop: how far right its keys, its line and Stop reach against the box it sits in
// (the page, or the 220-pixel pane), on how many rows they stand, and how much of any button's name is cut
function deskRow() { return page.evaluate(() => {
  const bar = document.querySelector('[role="toolbar"][aria-label="Keys"]');
  const edge = Math.min(bar.parentElement.getBoundingClientRect().right, window.innerWidth);
  const parts = [...bar.children].map((c) => c.getBoundingClientRect()).filter((r) => r.width > 0);
  let rows = 0, bottom = -Infinity;
  for (const r of [...parts].sort((a, b) => a.top - b.top)) {
    if (r.top >= bottom - 0.5) rows++;
    bottom = Math.max(bottom, r.bottom);
  }
  const stop = bar.querySelector('button[aria-label="Stop"]');
  const cut = Math.max(...[...bar.querySelectorAll("button")].map((b) => b.scrollWidth - b.clientWidth));
  return { edge, right: Math.max(...parts.map((r) => r.right)), rows, parts: parts.length,
    stopRight: stop.getBoundingClientRect().right, cut };
}); }
// the New session form's From row: on how many rows its field, its note and its button stand
function fromRow() { return page.evaluate(() => {
  const field = [...document.querySelectorAll("label")].find((l) => l.textContent === "From").parentElement;
  const parts = [...field.parentElement.children].map((c) => c.getBoundingClientRect()).filter((r) => r.height > 0);
  let rows = 0, bottom = -Infinity;
  for (const r of [...parts].sort((a, b) => a.top - b.top)) {
    if (r.top >= bottom - 0.5) rows++;
    bottom = Math.max(bottom, r.bottom);
  }
  return { rows, parts: parts.length };
}); }
// the Agents panel's header at each width: where its parts sit against it, how far right they reach, and on how many
// rows they stand
const header = () => page.evaluate(() => {
  const strip = document.querySelector('aside[aria-label="Agents"]').firstElementChild;
  const box = strip.getBoundingClientRect();
  const parts = [...strip.children].map((c) => c.getBoundingClientRect());
  const title = strip.children[1].getBoundingClientRect();    // its name, "Agents"
  // its words' own boxes, however they wrap
  const words = [...strip.querySelectorAll("span")].flatMap((s) => {
    const range = document.createRange();
    range.selectNodeContents(s);
    return [...range.getClientRects()];
  });
  const all = parts.concat(words);
  // the counts ("0 agents · 2 waiting for you"): on how many lines they stand
  const counts = document.createRange();
  counts.selectNodeContents(strip.children[2]);
  return { top: box.top, bottom: box.bottom, height: box.height, screen: window.innerWidth,
    right: Math.max(...all.map((r) => r.right)), highest: Math.min(...all.map((r) => r.top)),
    lowest: Math.max(...all.map((r) => r.bottom)),
    // a second row: a part that starts below the name's bottom
    rows: parts.some((r) => r.top >= title.bottom - 0.5) ? 2 : 1,
    countLines: new Set([...counts.getClientRects()].map((r) => Math.round(r.top))).size };
});
// a dialog's title row at each width: its title and its close button against it, and how many lines the title takes
const titleRow = () => page.evaluate(() => {
  const strip = document.querySelector("button[data-modal-close]").parentElement;
  const box = strip.getBoundingClientRect();
  const range = document.createRange();
  range.selectNodeContents(strip.children[0]);
  const all = [...strip.children].map((c) => c.getBoundingClientRect()).concat([...range.getClientRects()]);
  return { top: box.top, bottom: box.bottom, height: box.height, screen: window.innerWidth,
    right: Math.max(...all.map((r) => r.right)), highest: Math.min(...all.map((r) => r.top)),
    lowest: Math.max(...all.map((r) => r.bottom)),
    lines: new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size };
});
for (const width of [360, 390, 1280]) {
  await page.setViewportSize({ width, height: 800 });
  await page.goto("http://localhost/layout.html?which=agents");
  await page.waitForSelector(SHOWN.agents, { timeout: 15000 });
  await page.waitForTimeout(300);
  result[`agents.header.${width}`] = await header();
  for (const name of ["modal", "modalword"]) {
    await page.goto(`http://localhost/layout.html?which=${name}`);
    await page.waitForSelector(SHOWN[name], { timeout: 15000 });
    result[`${name}.title.${width}`] = await titleRow();
  }
}
// the New session form's Task row and a History row at a desktop's width: one row each, as before
await page.setViewportSize({ width: 1280, height: 800 });
await page.goto("http://localhost/layout.html?which=newsession");
await page.waitForSelector(SHOWN.newsession, { timeout: 15000 });
for (const tap of GIVEN.newsession) await (await page.waitForSelector(tap, { timeout: 15000 })).tap();
await page.waitForTimeout(300);
result["newsession.taskrow.1280"] = await taskRow();
result["newsession.fromrow.1280"] = await fromRow();
for (const name of ["history", "historypanel"]) {
  await page.goto(`http://localhost/layout.html?which=${name}`);
  await page.waitForSelector(SHOWN.history, { timeout: 15000 });
  result[`${name}.row.1280`] = await historyRow();
}
// the keys under a terminal on a desktop, across the page and in the 220-pixel pane
for (const name of ["deskkeys", "deskkeyspane"]) {
  await page.goto(`http://localhost/layout.html?which=${name}`);
  await page.waitForSelector(SHOWN[name], { timeout: 15000 });
  result[`${name}.row.1280`] = await deskRow();
}
// the command palette's actions at a desktop's width: each row, its label's box and its lines
await page.goto("http://localhost/layout.html?which=palette");
await page.waitForSelector(SHOWN.palette, { timeout: 15000 });
result["palette.items.1280"] = await page.evaluate(() => [...document.querySelectorAll(".palette-item")].map((el) => {
  const range = document.createRange();
  range.selectNodeContents(el.children[1]);           // its label, beside its icon
  const box = el.getBoundingClientRect();
  const words = range.getBoundingClientRect();
  return { words: el.textContent.trim().slice(0, 40), height: box.height, top: box.top, bottom: box.bottom,
    wordsTop: words.top, wordsBottom: words.bottom,
    lines: new Set([...range.getClientRects()].filter((r) => r.height > 0).map((r) => Math.round(r.top))).size };
}));
// at a desktop's width the strip is as it was: one line, its height the old fixed one
await page.setViewportSize({ width: 1280, height: 800 });
for (const name of ["banner", "bannerwho"]) {
  await page.goto(`http://localhost/layout.html?which=${name}`);
  await page.waitForSelector(SHOWN[name], { timeout: 15000 });
  result[`banner.${name}.1280`] = await page.evaluate(() => {
    const button = [...document.querySelectorAll("button")].find((b) => b.textContent === "Sign in to Claude");
    const range = document.createRange();
    range.selectNodeContents(button.parentElement.children[1]);
    return { height: button.parentElement.getBoundingClientRect().height,
      lines: new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size };
  });
}
// a screen without touch, a desktop's: the fields there, each its own size, a touch screen's mend not reaching them
const fine = await (await browser.newContext()).newPage();
await serve(fine);
await fine.setViewportSize({ width: 1280, height: 800 });
for (const name of ["signin", "chat", "newsession", "palette", "editor", "linkform"]) {
  await fine.goto(`http://localhost/layout.html?which=${name}`);
  await fine.waitForSelector(SHOWN[name], { timeout: 15000 });
  if (OPEN[name]) await OPEN[name](fine);
  await fine.waitForTimeout(300);
  result[`fields.fine.${name}`] = await fields(fine);
}
result["pointer.fine"] = await fine.evaluate(() => matchMedia("(pointer: coarse)").matches);
await browser.close();
console.log(JSON.stringify(result));
