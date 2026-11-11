"use strict";
// Voice input (a microphone, as the Claude app has one): one
// microphone button beside a field, which types what is said into it. A tap starts listening and the button shows it;
// the words go into the field as they are recognised; a second tap, or a pause, stops; the text stays in the field to
// read and send, never sent by itself. The recognition is the browser's own (the Web Speech API: SpeechRecognition,
// webkitSpeechRecognition in Safari and Chrome), so the audio goes to the browser's speech service (Apple's in Safari,
// Google's in Chrome), not to Proofpane, and the line beside the button says so. Where the browser has no such API
// (Firefox) there is no button, and the keyboard's own dictation types into the field as into any other. Apple has not
// given the API to apps added to an iPhone's Home Screen (WebKit bug 225298: "not available in ... web apps added to
// Home Screen for now", said in 2021, the bug set aside as "later", people still asking in 2025): there the button
// stays, in case that has changed, and when it fails, the line says to open the page in Safari. English or Chinese,
// remembered on this device.
//
// One recognition at a time, never continuous (Chrome on Android repeats the words in continuous mode): a pause ends
// it, as the browser decides, and a pause of PAUSE_MS after the last words ends it here too, for a browser that goes
// on. Typing into the field while it listens stops it and keeps what was typed. Everything is written as text.

const VOICE = (function () {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition || null;
  const LANGS = [["en-US", "English"], ["zh-CN", "Chinese"]];
  const KEY = "pp-voice-language";
  const WAIT_MS = 4000;       // not started by then: say what it waits for
  const FIRST_MS = 10000;     // no words by then: stop
  const PAUSE_MS = 3000;      // this long after the last words: stop
  const END_MS = 2000;        // asked to stop and not ended by then: done anyway
  const NOTE = "The browser's speech service (Apple's in Safari, Google's in Chrome) hears what you say, not " +
               "Proofpane.";
  const KEYBOARD = "Your keyboard's own dictation works here too.";
  const HOME_SCREEN = "On an iPhone, Apple gives speech recognition to Safari, not to an app added to the Home " +
                      "Screen: open this page in Safari for the microphone, or use the keyboard's own dictation here.";
  const ERRORS = {
    "not-allowed": "The microphone is not allowed for this page: allow it in the browser's settings, or use your " +
                   "keyboard's own dictation.",
    "service-not-allowed": "The browser's speech service is off here (on an iPhone or a Mac, Siri or Dictation must " +
                           "be on); your keyboard's own dictation, or typing, still works.",
    "no-speech": "Nothing was heard: tap the microphone and speak.",
    "audio-capture": "No microphone was found.",
    "network": "The browser's speech service could not be reached: try again, or type.",
    "language-not-supported": "The browser's speech service does not take this language here: try the other one.",
  };
  const NOT_STARTED = "The browser's speech service did not start: try again, or use your keyboard's own dictation.";
  const COULD_NOT = "Voice input could not start here. " + KEYBOARD;
  const SVG = "http://www.w3.org/2000/svg";   // the namespace of the icon's elements, not an address anything loads
  const made = [];
  let live = null;            // the field that listened last on this page: one at a time
  let here = null;            // the language chosen on this page, which a browser that stores nothing still keeps

  function make(tag, text, cls) {
    const e = document.createElement(tag);
    if (text !== undefined && text !== null) e.textContent = String(text);
    if (cls) e.className = cls;
    return e;
  }

  // an app added to an iPhone's Home Screen (Safari's own property)
  function homeScreen() { return navigator.standalone === true; }

  // the language to listen for: the one chosen on this page, else on this device, else the browser's own language when
  // it is Chinese, else English
  function language() {
    let stored = null;
    try { stored = localStorage.getItem(KEY); } catch (e) { stored = null; }
    for (const chosen of [here, stored]) if (LANGS.some(([code]) => code === chosen)) return chosen;
    const own = typeof navigator.language === "string" ? navigator.language : "";
    return own.toLowerCase().startsWith("zh") ? "zh-CN" : "en-US";
  }

  function setLanguage(code) {
    here = code;
    try { localStorage.setItem(KEY, code); } catch (e) { /* this page only */ }
    for (const w of made) drawLine(w);
  }

  // a character of the CJK scripts or the full-width forms, by its code point: words in them take no space between
  function wide(ch) {
    const c = ch ? ch.codePointAt(0) : 0;
    return (c >= 0x2e80 && c <= 0x9fff) || (c >= 0xf900 && c <= 0xfaff) || (c >= 0xff00 && c <= 0xffef);
  }

  // the field's text with what was heard after it: a space between, but none after white space or between two CJK
  // characters
  function joined(base, heard) {
    if (!heard) return base;
    if (!base || /\s$/.test(base) || (wide(base.slice(-1)) && wide(heard.charAt(0)))) return base + heard;
    return base + " " + heard;
  }

  // what one recognition has heard so far: each result's best reading, the final ones and the one still coming
  function heardIn(results) {
    let text = "";
    for (let i = 0; i < (results ? results.length : 0); i++) {
      const best = results[i] && results[i][0];
      if (best && typeof best.transcript === "string") text += best.transcript;
    }
    return text.trim();
  }

  // a microphone, or a square while it listens (a tap stops it); drawn with strokes in the button's colour
  function icon(listening) {
    const svg = document.createElementNS(SVG, "svg");
    const set = (e, a) => { for (const k of Object.keys(a)) e.setAttribute(k, String(a[k])); return e; };
    set(svg, { viewBox: "0 0 24 24", width: 18, height: 18, fill: "none", stroke: "currentColor", "stroke-width": 2,
               "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false" });
    const shapes = listening ? [["rect", { x: 7, y: 7, width: 10, height: 10, rx: 1.5, fill: "currentColor" }]]
      : [["rect", { x: 9, y: 2, width: 6, height: 12, rx: 3 }], ["path", { d: "M5 10v1a7 7 0 0 0 14 0v-1" }],
         ["path", { d: "M12 18v4" }], ["path", { d: "M8 22h8" }]];
    for (const [tag, a] of shapes) svg.appendChild(set(document.createElementNS(SVG, tag), a));
    return svg;
  }

  function later(w, key, ms, f) {
    clear(w, key);
    w.timers[key] = setTimeout(() => { delete w.timers[key]; f(); }, ms);
  }

  function clear(w, key) {
    if (w.timers[key] !== undefined) {
      clearTimeout(w.timers[key]);
      delete w.timers[key];
    }
  }

  function listening(w) { return !!w.rec && !w.stopping; }

  function langs(w) {
    const group = make("span", null, "voice-langs");
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", "The language you speak");
    const now = language();
    for (const [code, name] of LANGS) {
      const b = make("button", name);
      b.type = "button";
      b.setAttribute("aria-pressed", code === now ? "true" : "false");
      b.addEventListener("click", () => {
        if (code === language()) return;
        setLanguage(code);
        if (listening(w)) start(w);  // again, in the other language, after the words so far
      });
      group.appendChild(b);
    }
    return group;
  }

  function drawLine(w) {
    let text;
    if (listening(w)) {
      text = w.started ? "Listening: tap the microphone again, or pause, to stop. " + NOTE : w.hint || "Starting...";
    } else if (w.error) {
      text = w.error;
    } else {
      text = w.quiet ? "" : "Speak instead of typing. " + NOTE + " " + KEYBOARD;
    }
    w.line.textContent = "";
    w.line.hidden = !text;
    if (text) {
      w.line.appendChild(langs(w));
      w.line.appendChild(make("span", text));
    }
  }

  function draw(w) {
    const on = listening(w);
    w.button.className = "voice-mic" + (on ? " listening" : "");
    w.button.setAttribute("aria-pressed", on ? "true" : "false");
    w.button.setAttribute("aria-label", on ? "Stop listening" : "Speak instead of typing");
    w.button.title = on ? "Listening: tap to stop" : "Speak instead of typing. " + NOTE;
    w.button.textContent = "";
    w.button.appendChild(icon(on));
    drawLine(w);
  }

  function finish(w) {
    for (const key of Object.keys(w.timers)) clear(w, key);
    w.rec = null;
    w.stopping = false;
    draw(w);
  }

  // stop now, and leave aside whatever the recognition still says
  function drop(w) {
    const rec = w.rec;
    if (!rec) return;
    finish(w);
    try { rec.abort(); } catch (e) { /* it is going away either way */ }
  }

  // stop listening; the last words may still come, then its end
  function stop(w) {
    const rec = w.rec;
    if (!rec || w.stopping) return;
    w.stopping = true;
    clear(w, "quiet");               // a stop asked for is not "nothing was heard"
    try { rec.stop(); } catch (e) { /* ended below */ }
    later(w, "end", END_MS, () => finish(w));
    draw(w);
  }

  function start(w) {
    if (live && live !== w) drop(live);
    drop(w);
    w.error = "";
    w.hint = "";
    w.started = false;
    let rec;
    try {
      rec = new Recognition();
      rec.lang = language();
      rec.interimResults = true;
      rec.continuous = false;
      rec.maxAlternatives = 1;
    } catch (e) {
      w.error = COULD_NOT;
      draw(w);
      return;
    }
    w.rec = rec;
    w.base = w.field.value;
    live = w;
    const mine = (f) => (ev) => { if (w.rec === rec) f(ev || {}); };
    rec.onstart = mine(() => {
      w.started = true;              // (the line no longer shows what it waited for)
      drawLine(w);
    });
    rec.onresult = mine((ev) => {
      w.started = true;
      w.field.value = joined(w.base, heardIn(ev.results));      // a value set here fires no input event
      w.field.scrollTop = w.field.scrollHeight;
      later(w, "quiet", PAUSE_MS, () => stop(w));
      drawLine(w);
    });
    rec.onerror = mine((ev) => {
      if (ev.error === "aborted") return;
      w.error = ev.error === "service-not-allowed" && homeScreen() ? HOME_SCREEN
        : ERRORS[ev.error] || "Voice input stopped (" + String(ev.error || "no reason given") + ").";
      stop(w);                       // its end follows; if it does not, END_MS
    });
    rec.onend = mine(() => finish(w));
    later(w, "wait", WAIT_MS, () => {
      w.hint = "Waiting for the browser's speech service: allow the microphone if it asks." +
               (homeScreen() ? " " + HOME_SCREEN : "");
      drawLine(w);
    });
    later(w, "quiet", FIRST_MS, () => {
      w.error = w.started ? ERRORS["no-speech"] : homeScreen() ? HOME_SCREEN : NOT_STARTED;   // (an error stops first)
      stop(w);
    });
    try {
      rec.start();
    } catch (e) {
      w.error = COULD_NOT;
      finish(w);
      return;
    }
    draw(w);
  }

  // the microphone for a field: { button, line, cancel, enable, close }, or null where the browser cannot listen. The
  // line says the language, what it is doing and where the audio goes; `quiet` hides it while nothing happens (a
  // chat's bar), else it stays (a form)
  function attach(field, opts) {
    if (!Recognition || !field) return null;
    const w = { field, quiet: !!(opts && opts.quiet), rec: null, stopping: false, started: false, base: "", error: "",
                hint: "", timers: {} };
    w.button = make("button", null, "voice-mic");
    w.button.type = "button";
    w.line = make("p", null, "small voice-line");
    w.line.setAttribute("aria-live", "polite");
    w.button.addEventListener("click", () => { if (listening(w)) stop(w); else start(w); });
    // typing takes over: it stops, and what was typed stays
    field.addEventListener("input", () => drop(w));
    made.push(w);
    draw(w);
    return {
      button: w.button, line: w.line,
      cancel() { drop(w); },
      enable(on) { w.button.disabled = !on; if (!on) drop(w); },
      close() { drop(w); const i = made.indexOf(w); if (i >= 0) made.splice(i, 1); },
      listening() { return listening(w); },
    };
  }

  return { available: !!Recognition, attach, joined, heardIn, language, LANGS, NOTE, KEYBOARD, HOME_SCREEN, ERRORS,
           NOT_STARTED, COULD_NOT, times: { WAIT_MS, FIRST_MS, PAUSE_MS, END_MS } };
})();
