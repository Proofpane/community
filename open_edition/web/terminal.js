"use strict";
// Proofpane Community: one workspace session's terminal. The session is named in the address's fragment (#<id>),
// which a browser never sends to a server; the account's token goes in the WebSocket's first message, never in a URL,
// which an access log would keep. Keystrokes go to the workspace as bytes, from the terminal or from the bar under it;
// the only text this page sends is a resize. Everything it shows of its own is written with textContent.

// The bytes a key of the bar sends, as a terminal's own key would: the arrows in the form the program asked for
// (application cursor keys, which a full-screen program turns on, or the normal ones).
function keyBytes(name, applicationCursor) {
  const arrows = { up: "A", down: "B", right: "C", left: "D" };
  if (Object.prototype.hasOwnProperty.call(arrows, name)) return (applicationCursor ? "\x1bO" : "\x1b[") + arrows[name];
  return { enter: "\r", esc: "\x1b", tab: "\t", ctrl_c: "\x03", ctrl_d: "\x04" }[name] || null;
}

// What Copy takes when nothing is selected: the last lines of the terminal's buffer, a wrapped line joined back to the
// one it continues, and no empty lines at the end.
function lastLines(buffer, n) {
  const out = [];
  for (let i = Math.max(0, buffer.length - n); i < buffer.length; i++) {
    const line = buffer.getLine(i);
    if (!line) continue;
    const next = buffer.getLine(i + 1);
    const text = line.translateToString(!(next && next.isWrapped));
    if (line.isWrapped && out.length) out[out.length - 1] += text; else out.push(text);
  }
  while (out.length && !out[out.length - 1].trim()) out.pop();
  return out.join("\n");
}

(function () {
  const $ = (id) => document.getElementById(id);
  const session = (location.hash || "").slice(1);
  const say = (text, cls) => { $("term-state").textContent = text; $("term-state").className = cls || "note"; };
  let token = null;
  try { token = localStorage.getItem("pp-open-token"); } catch (e) { token = null; }
  if (!/^[0-9a-f]{12}$/.test(session)) { say("No session is named in this page's address.", "error"); return; }
  if (!token) { say("Sign in on the main page first, then open the terminal again.", "error"); return; }
  document.title = "Terminal " + session + " · Proofpane Community";
  $("term-title").textContent = "Session " + session;

  const touch = !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
  const term = new Terminal({ cursorBlink: true, scrollback: 5000, fontSize: 14,
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open($("terminal"));

  // On a phone the keyboard shrinks only the visual viewport, and a terminal as tall as the page kept the rows where
  // a program has its input line under the keyboard (seen on an iPhone). So the box ends where
  // the visible part of the page ends, whenever that changes, and a change of size shows the last row.
  const box = $("terminal");
  const bar = $("term-bar");
  const view = window.visualViewport;
  function fitToView() {
    if (view) {
      const top = box.getBoundingClientRect().top - view.offsetTop;   // the box's top within what is visible
      // and the bar under it stays in view too, just above the keyboard
      box.style.height = Math.max(120, Math.floor(view.height - top - bar.offsetHeight - 8)) + "px";
    }
    const rows = term.rows;
    fit.fit();
    if (term.rows !== rows) term.scrollToBottom();
  }
  let fitting = false;
  function refit() {
    if (fitting) return;
    fitting = true;
    requestAnimationFrame(() => { fitting = false; fitToView(); });
  }
  fitToView();

  const encoder = new TextEncoder();
  let ws = null;
  let refused = false;
  let ended = false;

  function send(data) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(data);
  }

  function connect() {
    $("term-reconnect").hidden = true;
    refused = false;
    ended = false;
    say("Connecting...");
    ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host +
                       "/api/ade/workspace/terminal");
    ws.binaryType = "arraybuffer";
    ws.onopen = () => ws.send(JSON.stringify({ token, session, cols: term.cols, rows: term.rows }));
    ws.onmessage = (ev) => {
      if (typeof ev.data !== "string") { term.write(new Uint8Array(ev.data)); return; }
      let msg = null;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type === "ready") {
        say("Connected. The program runs in the workspace: closing this page leaves it running.", "small");
        if (!touch) term.focus();          // on a phone, the bar's field is the way in: the keyboard waits for a tap
      } else if (msg.type === "exit") {
        ended = true;
        say("The program ended" + (typeof msg.code === "number" ? " with status " + msg.code : "") +
            ". Start it again from the workspace.");
      } else if (msg.type === "dropped") {
        say("Output was dropped while this page could not keep up; what follows is live.");
      } else if (msg.type === "error") {
        refused = true;
        say(typeof msg.detail === "string" ? msg.detail : "The workspace refused the terminal.", "error");
      }
    };
    ws.onclose = (ev) => {
      $("term-reconnect").hidden = false;
      // the close code goes with the line, so a report of it says which close it was
      if (!refused && !ended) say("Disconnected (code " + ev.code + ").");
    };
  }

  // On a phone the bar under the terminal is the way in (in a terminal a phone can neither copy nor paste, and a "2"
  // typed into Claude Code's first-run wizard showed nothing): a field where the phone's
  // keyboard, its suggestions and its own Paste work and the text shows; Send, the text and Enter; Type, the text
  // alone; the keys a program needs that a phone lacks; Paste and Copy through the clipboard. Typing into the
  // terminal itself works as before.
  const input = $("term-input");
  const note = (text) => { $("term-note").textContent = text || ""; $("term-note").hidden = !text; refit(); };
  const typeText = (text) => send(encoder.encode(text));
  $("term-line").addEventListener("submit", (ev) => {
    ev.preventDefault();
    typeText(input.value + "\r");
    input.value = "";
  });
  $("term-type").addEventListener("click", () => {
    if (input.value) typeText(input.value);
    input.value = "";
  });
  for (const b of $("term-keys").querySelectorAll("button[data-key]")) {
    b.addEventListener("click", () => typeText(keyBytes(b.dataset.key, term.modes.applicationCursorKeysMode)));
  }
  // a tap on the bar's buttons leaves the field focused, so a phone's keyboard stays open
  bar.addEventListener("mousedown", (ev) => { if (ev.target.closest("button")) ev.preventDefault(); });
  $("term-paste").addEventListener("click", async () => {
    note("");
    let text = null;
    try { text = await navigator.clipboard.readText(); } catch (e) { text = null; }
    if (typeof text !== "string") {
      input.focus();
      note("This browser did not give its clipboard here: paste into the field, then Send.");
      return;
    }
    if (text) term.paste(text);      // as a paste into the terminal: bracketed when the program asked for that
  });
  $("term-copy").addEventListener("click", async () => {
    note("");
    const text = term.hasSelection() ? term.getSelection() : lastLines(term.buffer.active, 200);
    try {
      if (!navigator.clipboard) throw new Error("no clipboard");
      await navigator.clipboard.writeText(text);
      $("term-copy").textContent = "Copied";
      setTimeout(() => { $("term-copy").textContent = "Copy"; }, 2000);
    } catch (e) {
      note("This browser did not allow copying here.");
    }
  });

  term.onData((data) => send(encoder.encode(data)));
  term.onBinary((data) => send(Uint8Array.from(data, (c) => c.charCodeAt(0) & 255)));
  term.onResize(({ cols, rows }) => send(JSON.stringify({ type: "resize", cols, rows })));
  window.addEventListener("resize", refit);
  window.addEventListener("scroll", refit, { passive: true });
  if (view) {
    view.addEventListener("resize", refit);
    view.addEventListener("scroll", refit);
  }
  $("term-reconnect").addEventListener("click", connect);
  connect();
})();
