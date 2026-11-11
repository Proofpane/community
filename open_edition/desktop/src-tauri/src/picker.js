// Design mode for the desktop app's preview window (lib.rs, open_preview): the app being built, served on this Mac,
// with a button to pick one of its elements. A pick is what an agent needs to find and change the element: where the
// page is, a CSS path to it, its HTML (cut short), its text, its box and the styles that place and dress it. It goes to
// the app's own command `design_pick`, which only this window may call; the main window then shows it, and the person
// writes what to change and sends it to the session's agent. Nothing here sends anything to an agent by itself.
//
// Run at the start of every page in the window, in its top frame only. It touches the page as little as it can: one
// element of its own, holding a shadow root, and listeners that act only while picking.
(() => {
  "use strict";
  if (window.top !== window || window.__ppDesignMode) return;
  window.__ppDesignMode = true;

  const HTML_MOST = 6000;
  const TEXT_MOST = 500;
  const STYLES = ["display", "position", "top", "left", "width", "height", "margin", "padding", "box-sizing", "color",
    "background-color", "background-image", "font-family", "font-size", "font-weight", "line-height", "letter-spacing",
    "text-align", "border", "border-radius", "box-shadow", "opacity", "gap", "flex-direction", "justify-content",
    "align-items", "flex", "grid-template-columns", "grid-template-rows", "overflow", "z-index", "transform"];
  const EMPTY = new Set(["none", "normal", "auto", "0px", "rgba(0, 0, 0, 0)", "0px none rgb(0, 0, 0)", "static",
    "visible", "start", "0", "1", "0px 0px 0px 0px", "content-box", "stretch", "flex-start", "row", "0 1 auto"]);

  const cut = (s, most) => (s.length > most ? s.slice(0, most) + "…" : s);
  const esc = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : s.replace(/[^\w-]/g, "\\$&"));

  /** A CSS path to the element: tag, id where there is one (and no further up), two classes at most, and the
   *  element's place among its kind where a sibling shares its tag. Six steps at most. */
  function pathTo(el) {
    const steps = [];
    for (let node = el; node && node.nodeType === 1 && steps.length < 6; node = node.parentElement) {
      let step = node.localName;
      if (node.id) {
        steps.unshift(`${step}#${esc(node.id)}`);
        break;
      }
      const classes = Array.from(node.classList).filter((c) => c && c.length < 40).slice(0, 2);
      if (classes.length) step += classes.map((c) => "." + esc(c)).join("");
      const parent = node.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((c) => c.localName === node.localName);
        if (same.length > 1) step += `:nth-of-type(${same.indexOf(node) + 1})`;
      }
      steps.unshift(step);
      if (node.localName === "body") break;
    }
    return steps.join(" > ");
  }

  function stylesOf(el) {
    const cs = getComputedStyle(el);
    const out = {};
    for (const k of STYLES) {
      const v = cs.getPropertyValue(k).trim();
      if (v && !EMPTY.has(v) && !/^0px none\b/.test(v)) out[k] = cut(v, 200);
    }
    return out;
  }

  /** What the agent is told of the element. */
  function pickOf(el) {
    const r = el.getBoundingClientRect();
    return {
      url: location.href,
      title: cut(document.title || "", 200),
      selector: pathTo(el),
      html: cut(el.outerHTML || "", HTML_MOST),
      text: cut((el.innerText || el.textContent || "").replace(/\s+/g, " ").trim(), TEXT_MOST),
      rect: { x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) },
      viewport: { width: window.innerWidth, height: window.innerHeight },
      styles: stylesOf(el),
    };
  }

  let host, root, button, box, label, toast;
  let picking = false;
  let eating = false;      // the rest of the click that picked: the page never sees it

  function build() {
    host = document.createElement("proofpane-design-mode");
    host.style.cssText = "all: initial; position: fixed; z-index: 2147483647; inset: auto 16px 16px auto;";
    root = host.attachShadow({ mode: "closed" });
    root.innerHTML = `
      <style>
        .bar { font: 12px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; display: flex; gap: 6px;
               align-items: center; background: #111827; color: #f3f4f6; border: 1px solid #374151; border-radius: 8px;
               padding: 6px 8px; box-shadow: 0 8px 24px rgba(0,0,0,.35); }
        button { font: inherit; color: inherit; background: #1f2937; border: 1px solid #374151; border-radius: 4px;
                 padding: 4px 9px; cursor: pointer; }
        button.on { background: #0e7490; color: #ffffff; border-color: #0e7490; }
        .toast { font: 12px -apple-system, sans-serif; position: fixed; right: 16px; bottom: 60px; background: #111827;
                 color: #f3f4f6; border: 1px solid #374151; border-radius: 8px; padding: 6px 10px; display: none; }
      </style>
      <div class="bar"><button type="button" title="Pick an element to send to the session's agent (Esc stops)">Pick an element</button></div>
      <div class="toast"></div>`;
    button = root.querySelector("button");
    toast = root.querySelector(".toast");
    button.addEventListener("click", (e) => { e.stopPropagation(); setPicking(!picking); });
    box = document.createElement("proofpane-design-highlight");
    box.style.cssText = "all: initial; position: fixed; z-index: 2147483646; pointer-events: none; display: none;" +
      "outline: 2px solid #22d3ee; background: rgba(34, 211, 238, .12); border-radius: 2px;";
    label = document.createElement("proofpane-design-label");
    label.style.cssText = "all: initial; position: fixed; z-index: 2147483647; pointer-events: none; display: none;" +
      "font: 11px ui-monospace, Menlo, monospace; background: #22d3ee; color: #030712; padding: 1px 5px; border-radius: 3px;";
    document.documentElement.append(host, box, label);
  }

  function say(text) {
    toast.textContent = text;
    toast.style.display = "block";
    clearTimeout(say.t);
    say.t = setTimeout(() => { toast.style.display = "none"; }, 3500);
  }

  function ours(el) {
    return el === host || el === box || el === label;
  }

  function show(el) {
    if (!el || ours(el)) { box.style.display = label.style.display = "none"; return; }
    const r = el.getBoundingClientRect();
    Object.assign(box.style, { display: "block", left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` });
    label.textContent = `${el.localName}${el.id ? "#" + el.id : ""} ${Math.round(r.width)}×${Math.round(r.height)}`;
    Object.assign(label.style, { display: "block", left: `${Math.max(0, r.left)}px`, top: `${Math.max(0, r.top - 18)}px` });
  }

  function setPicking(on) {
    picking = on;
    button.classList.toggle("on", on);
    button.textContent = on ? "Click an element (Esc stops)" : "Pick an element";
    document.documentElement.style.cursor = on ? "crosshair" : "";
    if (!on) box.style.display = label.style.display = "none";
  }

  function swallow(e) {
    if ((!picking && !eating) || ours(e.target)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    if (eating && !picking && e.type === "click") eating = false;      // the picking click is over
  }

  async function picked(e) {
    if (!picking || ours(e.target)) return;
    swallow(e);
    const el = e.target;
    setPicking(false);
    eating = true;
    setTimeout(() => { eating = false; }, 1500);      // a press that ends in no click (a drag) does not keep it
    const ipc = window.__TAURI_INTERNALS__;
    if (!ipc || typeof ipc.invoke !== "function") { say("This window cannot reach Proofpane Community."); return; }
    try {
      await ipc.invoke("design_pick", { pick: pickOf(el) });
      say("Sent to Proofpane Community: write what to change there.");
    } catch (err) {
      say("Not sent: " + String(err).slice(0, 120));
    }
  }

  function start() {
    if (host) return;
    build();
    window.addEventListener("mousemove", (e) => { if (picking) show(e.target); }, true);
    window.addEventListener("pointerdown", picked, true);
    for (const type of ["mousedown", "mouseup", "click", "dblclick", "pointerup", "contextmenu"]) {
      window.addEventListener(type, swallow, true);
    }
    window.addEventListener("keydown", (e) => { if (picking && e.key === "Escape") { e.preventDefault(); setPicking(false); } }, true);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
