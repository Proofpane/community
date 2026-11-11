// Voice input (a microphone, as the Claude app has one): a tap starts
// listening, the words go into the field as they are recognised, a second tap or a pause stops, and the text stays to
// read and send, never sent by itself. The recognition is the browser's own (the Web Speech API), so the audio goes to
// the browser's speech service (Apple's in Safari, Google's in Chrome), not to Proofpane. Where the browser has none
// (Firefox) there is no microphone, and the keyboard's own dictation works as in any field. Apple gives the API to
// Safari, not to a page added to an iPhone's Home Screen (WebKit bug 225298). One recognition at a time, never
// continuous (Chrome on Android repeats the words in continuous mode). Ported from the older pages' voice.js.
import { useCallback, useEffect, useRef, useState } from "react";

type Recognizer = {
  lang: string; interimResults: boolean; continuous: boolean; maxAlternatives: number;
  onstart: (() => void) | null; onresult: ((ev: { results?: ArrayLike<ArrayLike<{ transcript?: string }>> }) => void) | null;
  onerror: ((ev: { error?: string }) => void) | null; onend: (() => void) | null;
  start(): void; stop(): void; abort(): void;
};
type RecognizerClass = new () => Recognizer;

const Recognition: RecognizerClass | null = typeof window === "undefined" ? null
  : ((window as unknown as Record<string, unknown>).SpeechRecognition || (window as unknown as Record<string, unknown>).webkitSpeechRecognition || null) as RecognizerClass | null;

export const voiceHere = !!Recognition;
export const LANGS: [string, string][] = [["en-US", "English"], ["zh-CN", "Chinese"]];
const KEY = "pp-voice-language";
const WAIT_MS = 4000;       // not started by then: say what it waits for
const FIRST_MS = 10000;     // no words by then: stop
const PAUSE_MS = 3000;      // this long after the last words: stop
const END_MS = 2000;        // asked to stop and not ended by then: done anyway
export const NOTE = "The browser's speech service (Apple's in Safari, Google's in Chrome) hears what you say, not Proofpane.";
const HOME_SCREEN = "On an iPhone, Apple gives speech recognition to Safari, not to an app added to the Home Screen: open this page in Safari for the microphone, or use the keyboard's own dictation here.";
const NOT_STARTED = "The browser's speech service did not start: try again, or use your keyboard's own dictation.";
const COULD_NOT = "Voice input could not start here. Your keyboard's own dictation works here too.";
const ERRORS: Record<string, string> = {
  "not-allowed": "The microphone is not allowed for this page: allow it in the browser's settings, or use your keyboard's own dictation.",
  "service-not-allowed": "The browser's speech service is off here (on an iPhone or a Mac, Siri or Dictation must be on); your keyboard's own dictation, or typing, still works.",
  "no-speech": "Nothing was heard: tap the microphone and speak.",
  "audio-capture": "No microphone was found.",
  network: "The browser's speech service could not be reached: try again, or type.",
  "language-not-supported": "The browser's speech service does not take this language here: try the other one.",
};

const homeScreen = () => (navigator as Navigator & { standalone?: boolean }).standalone === true;
function wide(ch: string) {
  const c = ch ? ch.codePointAt(0) || 0 : 0;
  return (c >= 0x2e80 && c <= 0x9fff) || (c >= 0xf900 && c <= 0xfaff) || (c >= 0xff00 && c <= 0xffef);
}

/** What was there before, and what is heard, as one text: a space between words, none between wide characters. */
export function joined(base: string, heard: string): string {
  if (!heard) return base;
  if (!base || /\s$/.test(base) || (wide(base.slice(-1)) && wide(heard.charAt(0)))) return base + heard;
  return `${base} ${heard}`;
}

/** The best transcript of each result, in order. */
export function heardIn(results?: ArrayLike<ArrayLike<{ transcript?: string }>>): string {
  let text = "";
  for (let i = 0; i < (results ? results.length : 0); i++) {
    const best = results![i] && results![i][0];
    if (best && typeof best.transcript === "string") text += best.transcript;
  }
  return text.trim();
}

export function language(): string {
  let stored: string | null = null;
  try { stored = localStorage.getItem(KEY); } catch { stored = null; }
  if (LANGS.some(([code]) => code === stored)) return stored as string;
  return (navigator.language || "").toLowerCase().startsWith("zh") ? "zh-CN" : "en-US";
}

/** A field's microphone: listening or not, what it says, and the language. `get` reads the field now; `set` writes it. */
export function useVoice(get: () => string, set: (text: string) => void) {
  const rec = useRef<Recognizer | null>(null);
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const [listening, setListening] = useState(false);
  const [started, setStarted] = useState(false);
  const [line, setLine] = useState("");
  const [lang, setLang] = useState(language);

  const clear = (key?: string) => {
    for (const k of key ? [key] : Object.keys(timers.current)) { clearTimeout(timers.current[k]); delete timers.current[k]; }
  };
  const later = (key: string, ms: number, f: () => void) => { clear(key); timers.current[key] = setTimeout(() => { delete timers.current[key]; f(); }, ms); };
  const finish = useCallback(() => { clear(); rec.current = null; setListening(false); }, []);
  const drop = useCallback(() => {
    const r = rec.current;
    if (!r) return;
    finish();
    try { r.abort(); } catch { /* going away either way */ }
  }, [finish]);
  const stop = useCallback(() => {
    const r = rec.current;
    if (!r) return;
    clear("quiet");
    try { r.stop(); } catch { /* ended below */ }
    later("end", END_MS, finish);
    setListening(false);
  }, [finish]);

  const start = useCallback((code = lang) => {
    drop();
    setLine(""); setStarted(false);
    if (!Recognition) return;
    let r: Recognizer;
    try {
      r = new Recognition();
      r.lang = code; r.interimResults = true; r.continuous = false; r.maxAlternatives = 1;
    } catch { setLine(COULD_NOT); return; }
    rec.current = r;
    const base = get();
    const mine = <A extends unknown[]>(f: (...a: A) => void) => (...a: A) => { if (rec.current === r) f(...a); };
    r.onstart = mine(() => setStarted(true));
    r.onresult = mine((ev) => {
      setStarted(true);
      set(joined(base, heardIn(ev.results)));
      later("quiet", PAUSE_MS, stop);
      setLine("");
    });
    r.onerror = mine((ev) => {
      if (ev.error === "aborted") return;
      setLine(ev.error === "service-not-allowed" && homeScreen() ? HOME_SCREEN : ERRORS[ev.error || ""] || `Voice input stopped (${ev.error || "no reason given"}).`);
      stop();
    });
    r.onend = mine(finish);
    later("wait", WAIT_MS, () => setLine("Waiting for the browser's speech service: allow the microphone if it asks." + (homeScreen() ? " " + HOME_SCREEN : "")));
    later("quiet", FIRST_MS, () => { setLine(homeScreen() ? HOME_SCREEN : NOT_STARTED); stop(); });
    try { r.start(); } catch { setLine(COULD_NOT); finish(); return; }
    setListening(true);
  }, [drop, finish, get, lang, set, stop]);

  const choose = (code: string) => {
    setLang(code);
    try { localStorage.setItem(KEY, code); } catch { /* this page only */ }
    if (rec.current) start(code);       // again, in the other language, after the words so far
  };
  useEffect(() => () => drop(), [drop]);
  return { available: voiceHere, listening, started, line, lang, choose, toggle: () => (rec.current ? stop() : start()), cancel: drop };
}
