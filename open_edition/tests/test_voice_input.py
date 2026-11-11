"""Voice input (voice.js; a microphone, as the Claude app has one):
run through node against a stand-in DOM, a stand-in SpeechRecognition and a clock the test moves. A tap listens, the
words go into the field as they come, a second tap or a pause stops, and the text stays there for the owner to send;
the line beside it says where the audio goes. It was also driven in a headless browser with a stand-in recognition in
the console and on the ADE page (the commit that added it says how). The Chinese this test speaks is made from code
points: the repository holds no Chinese text."""

import json
import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")


def read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as fh:
        return fh.read()


STAND_IN = r"""
function node(tag, ns) {
  const n = { tag, ns: ns || null, children: [], own: "", className: "", attrs: {}, listeners: {}, hidden: false,
              title: "", type: "", disabled: false, value: "", scrollTop: 0, scrollHeight: 120,
              appendChild(x) { this.children.push(x); return x; },
              append(...xs) { for (const x of xs) this.children.push(x); },
              setAttribute(k, v) { this.attrs[k] = String(v); },
              getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
              addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
              fire(t) { for (const f of this.listeners[t] || []) f({ type: t }); } };
  Object.defineProperty(n, "textContent", {
    get() { return this.own + this.children.map((c) => c.textContent).join(""); },
    set(v) { this.own = String(v); this.children = []; } });
  return n;
}
globalThis.document = { createElement: (t) => node(t), createElementNS: (ns, t) => node(t, ns) };
let clock = 0, nextId = 1;
const timers = new Map();
globalThis.setTimeout = (f, ms) => { const id = nextId++; timers.set(id, { at: clock + ms, f }); return id; };
globalThis.clearTimeout = (id) => { timers.delete(id); };
function tick(ms) {
  const until = clock + ms;
  for (;;) {
    let due = null;
    for (const [id, t] of timers) if (t.at <= until && (!due || t.at < due[1].at)) due = [id, t];
    if (!due) break;
    timers.delete(due[0]);
    clock = due[1].at;
    due[1].f();
  }
  clock = until;
}
const store = {};
const storage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
Object.defineProperty(globalThis, "localStorage", { value: storage, configurable: true, writable: true });
Object.defineProperty(globalThis, "navigator", { value: { language: LANGUAGE }, configurable: true, writable: true });
const recs = [];
const failing = { make: false, start: false };
class Recognition {
  constructor() { if (failing.make) throw new Error("not here"); this.calls = []; recs.push(this); }
  start() { this.calls.push("start"); if (failing.start) throw new Error("InvalidStateError"); }
  stop() { this.calls.push("stop"); }
  abort() { this.calls.push("abort"); }
}
globalThis.window = WITH_API ? { webkitSpeechRecognition: Recognition } : {};
const said = (...words) => words.map((t) => [{ transcript: t }]);
const wide = (...points) => String.fromCodePoint(...points);
"""

SNAP = r"""
const snap = (v, field) => {
  const shown = !v.line.hidden;
  return { cls: v.button.className, pressed: v.button.getAttribute("aria-pressed"),
           label: v.button.getAttribute("aria-label"), title: v.button.title, disabled: v.button.disabled,
           icon: v.button.children.length === 1 ? v.button.children[0].children.map((c) => c.tag +
                   (c.getAttribute("fill") ? ":" + c.getAttribute("fill") : "")) : null,
           line: shown ? v.line.children[1].textContent : null,
           langs: shown ? v.line.children[0].children.map((b) => b.textContent + ":" + b.getAttribute("aria-pressed"))
                        : null,
           value: field.value };
};
const out = {};
const T = VOICE.times;
"""


def run(tmp_path, body, language="en-NZ", with_api=True):
    probe = tmp_path / "voice-probe.js"
    probe.write_text(STAND_IN.replace("LANGUAGE", json.dumps(language)).replace("WITH_API", json.dumps(with_api))
                     + read("web", "voice.js") + SNAP + body + "\nprocess.stdout.write(JSON.stringify(out));\n")
    got = subprocess.run([NODE, str(probe)], capture_output=True, text=True)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def test_a_browser_without_the_api_gets_no_microphone(tmp_path):
    got = run(tmp_path, """
out.available = VOICE.available;
out.attached = VOICE.attach(node("textarea"));
""", with_api=False)
    assert got == {"available": False, "attached": None}


LISTEN = r"""
const field = node("textarea");
field.value = "Fix the bug";
out.none = VOICE.attach(null);
const v = VOICE.attach(field);
out.idle = snap(v, field);
out.svg = v.button.children[0].attrs;
v.button.fire("click");
const r = recs[0];
out.made = { lang: r.lang, interim: r.interimResults, continuous: r.continuous, alternatives: r.maxAlternatives,
             calls: r.calls.slice() };
out.starting = snap(v, field);
r.onstart();
out.listening = snap(v, field);
r.onresult({ results: said("in the") });
out.first = field.value;
r.onresult({ results: said("in the parser") });
out.more = field.value;
out.scrolled = field.scrollTop;
v.button.fire("click");
out.tapped = { calls: r.calls.slice(), snap: snap(v, field) };
r.onresult({ results: said("in the parser now") });
out.lastWords = field.value;
r.onend();
out.ended = snap(v, field);
out.stillThere = field.value;
"""


def test_a_tap_listens_the_words_go_into_the_field_and_a_second_tap_stops(tmp_path):
    got = run(tmp_path, LISTEN + "out.note = VOICE.NOTE; out.keyboard = VOICE.KEYBOARD;")
    note, keyboard = got["note"], got["keyboard"]
    assert note == ("The browser's speech service (Apple's in Safari, Google's in Chrome) hears what you say, not "
                    "Proofpane.")
    assert keyboard == "Your keyboard's own dictation works here too."
    mic = ["rect", "path", "path", "path"]
    assert got["none"] is None
    assert got["svg"] == {"viewBox": "0 0 24 24", "width": "18", "height": "18", "fill": "none", "stroke": "currentColor",
                          "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round",
                          "aria-hidden": "true", "focusable": "false"}
    assert got["idle"] == {"cls": "voice-mic", "pressed": "false", "label": "Speak instead of typing",
                           "title": "Speak instead of typing. " + note, "disabled": False, "icon": mic,
                           "line": "Speak instead of typing. " + note + " " + keyboard,
                           "langs": ["English:true", "Chinese:false"], "value": "Fix the bug"}
    # one recognition, the browser's own language unless chosen, words as they come, never continuous
    assert got["made"] == {"lang": "en-US", "interim": True, "continuous": False, "alternatives": 1, "calls": ["start"]}
    assert got["starting"]["cls"] == "voice-mic listening" and got["starting"]["pressed"] == "true"
    assert got["starting"]["label"] == "Stop listening" and got["starting"]["title"] == "Listening: tap to stop"
    assert got["starting"]["icon"] == ["rect:currentColor"]          # a square: a tap stops it
    assert got["starting"]["line"] == "Starting..."
    assert got["listening"]["line"] == "Listening: tap the microphone again, or pause, to stop. " + note
    assert got["first"] == "Fix the bug in the" and got["more"] == "Fix the bug in the parser"
    assert got["scrolled"] == 120                                     # the field shows its end
    # the second tap: it stops, and the last words may still come; the text stays for the owner to send
    assert got["tapped"]["calls"] == ["start", "stop"] and got["tapped"]["snap"]["cls"] == "voice-mic"
    assert got["lastWords"] == "Fix the bug in the parser now"
    assert got["ended"]["line"] == "Speak instead of typing. " + note + " " + keyboard
    assert got["stillThere"] == "Fix the bug in the parser now"


def test_a_pause_stops_it_and_a_recognition_that_never_ends_is_done_anyway(tmp_path):
    got = run(tmp_path, r"""
const field = node("textarea");
const v = VOICE.attach(field, { quiet: true });
out.quietIdle = snap(v, field);
v.button.fire("click");
const r = recs[0];
r.onstart();
out.quietListening = snap(v, field).line !== null;
r.onresult({ results: said("hello") });
tick(T.PAUSE_MS - 1);
out.before = r.calls.slice();
r.onresult({ results: said("hello there") });          // more words: the pause starts again
tick(T.PAUSE_MS - 1);
out.again = r.calls.slice();
tick(1);
out.paused = r.calls.slice();
out.stopping = snap(v, field).cls;
tick(T.END_MS - 1);
r.onresult({ results: said("hello there you") });        // its last words, still taken until it ends
out.lastWords = field.value;
tick(1);
out.done = snap(v, field);
r.onresult({ results: said("hello there you and more") });   // after its end: nothing
out.after = field.value;
v.button.fire("click");
const s = recs[1];
s.onstart();
tick(T.FIRST_MS - 1000);
v.button.fire("click");                                  // stopped on purpose, before any words
tick(1000);
s.onend();
out.onPurpose = { line: snap(v, field).line, calls: s.calls.slice() };
v.button.fire("click");
const e = recs[2];
v.button.fire("click");
e.onerror({ error: "no-speech" });                       // an error while it stops
out.errorWhileStopping = e.calls.slice();
""")
    assert got["quietIdle"]["line"] is None                            # a chat's bar: no line while nothing happens
    assert got["quietListening"] is True
    assert got["before"] == ["start"] and got["again"] == ["start"]
    assert got["paused"] == ["start", "stop"] and got["stopping"] == "voice-mic"
    assert got["lastWords"] == "hello there you"
    assert got["done"]["line"] is None and got["done"]["value"] == "hello there you"
    assert got["after"] == "hello there you"
    assert got["onPurpose"] == {"line": None, "calls": ["start", "stop"]}
    assert got["errorWhileStopping"] == ["start", "stop"]


def test_what_it_says_when_it_waits_or_fails(tmp_path):
    body = r"""
function tried(f) {
  const field = node("textarea");
  const v = VOICE.attach(field, { quiet: true });
  v.button.fire("click");
  const r = recs[recs.length - 1];
  const seen = f(r, v, field) || {};
  seen.line = snap(v, field).line;
  seen.calls = r.calls.slice();
  seen.cls = snap(v, field).cls;
  return seen;
}
out.waiting = tried((r, v, field) => { tick(T.WAIT_MS - 1); const early = snap(v, field).line; tick(1);
                                       return { early, late: snap(v, field).line }; });
out.neverStarted = tried((r) => { tick(T.FIRST_MS); r.onend(); });
out.nothingHeard = tried((r) => { r.onstart(); tick(T.FIRST_MS); r.onend(); });
for (const code of ["not-allowed", "service-not-allowed", "no-speech", "audio-capture", "network",
                    "language-not-supported", "bad-grammar", "aborted"]) {
  out["error:" + code] = tried((r) => { r.onstart(); r.onerror({ error: code }); r.onend(); });
}
out.errorNoEnd = tried((r) => { r.onerror({ error: "network" }); tick(T.END_MS); });
out.fresh = tried((r, v, field) => {
  tick(T.WAIT_MS);                                       // its hint shown, then it starts, then fails
  r.onstart();
  r.onerror({ error: "network" });
  r.onend();
  v.button.fire("click");
  const again = recs[recs.length - 1];
  const starting = snap(v, field).line;
  again.onstart();
  again.onend();
  return { starting, after: snap(v, field).line };
});
out.ERRORS = VOICE.ERRORS;
out.NOT_STARTED = VOICE.NOT_STARTED;
out.HOME_SCREEN = VOICE.HOME_SCREEN;
"""
    got = run(tmp_path, body)
    errors = got["ERRORS"]
    wait = "Waiting for the browser's speech service: allow the microphone if it asks."
    assert got["waiting"]["early"] == "Starting..." and got["waiting"]["late"] == wait
    assert got["neverStarted"]["line"] == got["NOT_STARTED"] and got["neverStarted"]["calls"] == ["start", "stop"]
    assert got["nothingHeard"]["line"] == errors["no-speech"]
    for code, text in errors.items():
        seen = got["error:" + code]
        assert (seen["line"], seen["calls"], seen["cls"]) == (text, ["start", "stop"], "voice-mic"), code
    assert got["error:bad-grammar"]["line"] == "Voice input stopped (bad-grammar)."
    assert got["error:aborted"]["line"] is None and got["error:aborted"]["calls"] == ["start"]   # its own doing
    assert got["errorNoEnd"]["line"] == errors["network"] and got["errorNoEnd"]["cls"] == "voice-mic"
    assert (got["fresh"]["starting"], got["fresh"]["after"]) == ("Starting...", None)
    assert errors["service-not-allowed"] == ("The browser's speech service is off here (on an iPhone or a Mac, Siri or "
                                             "Dictation must be on); your keyboard's own dictation, or typing, still "
                                             "works.")
    # in an app added to an iPhone's Home Screen, where Apple has not given the API (WebKit bug 225298)
    home = run(tmp_path, body.replace("function tried", "navigator.standalone = true;\nfunction tried"))
    assert home["waiting"]["late"] == wait + " " + home["HOME_SCREEN"]
    assert home["neverStarted"]["line"] == home["HOME_SCREEN"]
    assert home["error:service-not-allowed"]["line"] == home["HOME_SCREEN"]
    assert home["error:not-allowed"]["line"] == errors["not-allowed"]
    assert home["HOME_SCREEN"] == ("On an iPhone, Apple gives speech recognition to Safari, not to an app added to the "
                                   "Home Screen: open this page in Safari for the microphone, or use the keyboard's own "
                                   "dictation here.")


def test_sending_typing_or_another_field_stops_it_and_leaves_the_rest_aside(tmp_path):
    got = run(tmp_path, r"""
const a = node("textarea"), b = node("textarea");
const va = VOICE.attach(a, { quiet: true }), vb = VOICE.attach(b, { quiet: true });
// sent while it listens: nothing more goes into the field
va.button.fire("click");
recs[0].onresult({ results: said("abc") });
va.cancel();
recs[0].onresult({ results: said("abc def") });
out.cancelled = { calls: recs[0].calls.slice(), value: a.value, listening: va.listening() };
// typing takes over, and what was typed stays
a.value = "";
va.button.fire("click");
recs[1].onresult({ results: said("hello") });
a.value = "hello there";
a.fire("input");
recs[1].onresult({ results: said("hello world") });
out.typed = { calls: recs[1].calls.slice(), value: a.value, cls: snap(va, a).cls };
// one field at a time
va.button.fire("click");
vb.button.fire("click");
out.one = { a: recs[2].calls.slice(), aListening: va.listening(), b: recs[3].calls.slice(), bListening: vb.listening() };
// a pane busy with Claude's turn: no listening, the button off
vb.enable(false);
out.busy = { calls: recs[3].calls.slice(), disabled: vb.button.disabled, listening: vb.listening() };
vb.enable(true);
out.free = vb.button.disabled;
// a tap while it is still stopping starts again at once
va.button.fire("click");
recs[4].onstart();
va.button.fire("click");
va.button.fire("click");
out.restart = { old: recs[4].calls.slice(), fresh: recs[5].calls.slice(), listening: va.listening() };
// a pane closed: it stops, and a language chosen later does not reach it
const c = node("textarea"), d = node("textarea");
const vc = VOICE.attach(c), vd = VOICE.attach(d);
vc.button.fire("click");
vc.close();
out.closed = { calls: recs[6].calls.slice(), listening: vc.listening() };
vd.line.children[0].children[1].fire("click");
out.closedLangs = snap(vc, c).langs;
out.openLangs = snap(vd, d).langs;
""")
    assert got["cancelled"] == {"calls": ["start", "abort"], "value": "abc", "listening": False}
    assert got["typed"] == {"calls": ["start", "abort"], "value": "hello there", "cls": "voice-mic"}
    assert got["one"] == {"a": ["start", "abort"], "aListening": False, "b": ["start"], "bListening": True}
    assert got["busy"] == {"calls": ["start", "abort"], "disabled": True, "listening": False}
    assert got["free"] is False
    assert got["restart"] == {"old": ["start", "stop", "abort"], "fresh": ["start"], "listening": True}
    assert got["closed"] == {"calls": ["start", "abort"], "listening": False}
    assert got["closedLangs"] == ["English:true", "Chinese:false"]
    assert got["openLangs"] == ["English:false", "Chinese:true"]


def test_english_or_chinese_is_chosen_remembered_and_switched_while_listening(tmp_path):
    got = run(tmp_path, r"""
const field = node("textarea");
const v = VOICE.attach(field, { quiet: true });
v.button.fire("click");
recs[0].onstart();
recs[0].onresult({ results: said("hello") });
v.line.children[0].children[1].fire("click");            // Chinese, while it listens
out.switched = { old: recs[0].calls.slice(), fresh: recs[1] && recs[1].calls.slice(), lang: recs[1] && recs[1].lang,
                 stored: localStorage.getItem("pp-voice-language"), langs: snap(v, field).langs };
recs[1].onstart();
recs[1].onresult({ results: said(wide(0x4f60, 0x597d)) });
out.mixed = field.value;
field.value = wide(0x4f60, 0x597d);
v.cancel();
v.button.fire("click");
recs[2].onresult({ results: said(wide(0x4e16, 0x754c)) });
out.wide = field.value;
v.line.children[0].children[1].fire("click");            // the one already chosen: nothing starts again
out.same = recs.length;
out.language = VOICE.language();
""")
    hello = chr(0x4F60) + chr(0x597D)
    assert got["switched"] == {"old": ["start", "abort"], "fresh": ["start"], "lang": "zh-CN", "stored": "zh-CN",
                               "langs": ["English:false", "Chinese:true"]}
    assert got["mixed"] == "hello " + hello                          # the words so far stay, before the new ones
    assert got["wide"] == hello + chr(0x4E16) + chr(0x754C)          # no space between two CJK characters
    assert got["same"] == 3 and got["language"] == "zh-CN"
    # a device's first language is the browser's own when it is Chinese, else English, and a stored one wins
    first = run(tmp_path, "out.language = VOICE.language();", language="zh-TW")
    assert first["language"] == "zh-CN"
    for language in ("en-NZ", "fr-FR", ""):
        assert run(tmp_path, "out.language = VOICE.language();", language=language)["language"] == "en-US"
    stored = run(tmp_path, 'localStorage.setItem("pp-voice-language", "fr-FR"); out.a = VOICE.language();'
                           'localStorage.setItem("pp-voice-language", "en-US"); out.b = VOICE.language();',
                 language="zh-CN")
    assert stored == {"a": "zh-CN", "b": "en-US"}
    # a browser that keeps no storage (a private window): the browser's own language, and a choice holds on the page
    private = run(tmp_path, r"""
Object.defineProperty(globalThis, "localStorage", { get() { throw new Error("denied"); }, configurable: true });
out.language = VOICE.language();
const f = node("textarea");
const v = VOICE.attach(f);
v.line.children[0].children[0].fire("click");
out.after = VOICE.language();
""", language="zh-CN")
    assert private == {"language": "zh-CN", "after": "en-US"}
    held = run(tmp_path, r"""
const f = node("textarea");
const v = VOICE.attach(f);
v.line.children[0].children[1].fire("click");
localStorage.setItem("pp-voice-language", "en-US");     // another tab's choice, later
out.here = VOICE.language();
""")
    assert held == {"here": "zh-CN"}
    lost = run(tmp_path, r"""
localStorage.setItem = () => { throw new Error("quota"); };   // reads work, nothing is kept
const f = node("textarea");
const v = VOICE.attach(f);
v.line.children[0].children[1].fire("click");
out.here = VOICE.language();
out.langs = snap(v, f).langs;
""")
    assert lost == {"here": "zh-CN", "langs": ["English:false", "Chinese:true"]}


def test_the_words_are_joined_as_they_are_written(tmp_path):
    got = run(tmp_path, r"""
const a = wide(0x4f60), b = wide(0x597d), comma = wide(0xff0c);
out.joined = [VOICE.joined("", "hi"), VOICE.joined("Fix", "it"), VOICE.joined("Fix ", "it"),
              VOICE.joined("Fix\n", "it"), VOICE.joined("Fix", ""), VOICE.joined(a, b), VOICE.joined("Fix", a),
              VOICE.joined(a, "it"), VOICE.joined(a + comma, b)];
out.heard = [VOICE.heardIn(said("hello", " world")), VOICE.heardIn([[{ transcript: " spaced " }], [], [null]]),
             VOICE.heardIn(undefined), VOICE.heardIn([[{ transcript: 7 }]])];
""")
    one, two, comma = chr(0x4F60), chr(0x597D), chr(0xFF0C)
    assert got["joined"] == ["hi", "Fix it", "Fix it", "Fix\nit", "Fix", one + two, "Fix " + one, one + " it",
                             one + comma + two]
    assert got["heard"] == ["hello world", "spaced", "", ""]


def test_a_recognition_that_cannot_be_made_or_started_says_so(tmp_path):
    got = run(tmp_path, r"""
const field = node("textarea");
const v = VOICE.attach(field, { quiet: true });
failing.make = true;
v.button.fire("click");
out.make = snap(v, field);
failing.make = false;
failing.start = true;
v.button.fire("click");
out.start = snap(v, field);
out.calls = recs[0].calls;
out.COULD_NOT = VOICE.COULD_NOT;
""")
    assert got["COULD_NOT"] == "Voice input could not start here. Your keyboard's own dictation works here too."
    for seen in (got["make"], got["start"]):
        assert (seen["cls"], seen["line"]) == ("voice-mic", got["COULD_NOT"])
    assert got["calls"] == ["start"]


def test_the_microphone_sends_nothing_and_writes_no_html():
    script = read("web", "voice.js")
    # the audio goes to the browser's speech service, never here: this script makes no request and sends no form
    for word in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "EventSource", ".submit(", "requestSubmit",
                 "getUserMedia", "MediaRecorder", "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                 "eval(", "new Function", "setAttribute(\"style\""):
        assert word not in script, word
    assert re.findall(r"\bhttps?://[^\s\"']+", script) == ["http://www.w3.org/2000/svg"]   # the icon's namespace


def test_the_console_and_the_ade_form_carry_the_microphone():
    script, page, app, ade, css = (read("web", "console.js"), read("web", "console.html"), read("web", "app.js"),
                                   read("web", "index.html"), read("web", "style.css"))
    # a chat's bar: beside Attach, before Send; its line only while it listens; off while Claude works
    assert "pane.voice = VOICE.attach(pane.box, { quiet: true });" in script
    assert "if (pane.voice) pane.form.append(pane.voice.button, pane.voice.line);" in script
    assert script.index("pane.form.append(pane.box, pane.attach);") < script.index(
        "if (pane.voice) pane.form.append(pane.voice.button, pane.voice.line);") < script.index(
        "pane.form.append(pane.send);")
    assert "if (pane.voice) pane.voice.enable(!busy);" in script
    # sent: it stops before the message goes, so no late words come back into the emptied field
    sent = script[script.index('pane.form.addEventListener("submit"'):]
    assert sent.index("if (pane.voice) pane.voice.cancel();") < sent.index('api("POST"')
    # a pane closed or replaced stops listening
    assert "function silence(pane) { if (pane && pane.voice) pane.voice.close(); }" in script
    assert "silence(pane);\n  con.panes = con.panes.filter" in script
    assert "silence(con.panes[con.focus]);\n      con.panes[con.focus] = pane;" in script
    # the forms: the console's New chat and the ADE page's new session, each stopping before it sends
    assert 'const newVoice = VOICE.attach($("con-new").task);' in script
    assert '<div id="con-new-voice" class="voice-row"></div>' in page
    new_chat = script[script.index('$("con-new").addEventListener("submit"'):]
    assert new_chat.index("if (newVoice) newVoice.cancel();") < new_chat.index('api("POST"')
    assert 'const wsVoice = VOICE.attach($("ws-new").task);' in app
    assert '<div id="ws-new-voice" class="voice-row"></div>' in ade
    new_session = app[app.index('$("ws-new").addEventListener("submit"'):]
    assert new_session.index("if (wsVoice) wsVoice.cancel();") < new_session.index("wsRun(")
    # voice.js comes before the script that uses it, on both pages
    for html, user in ((page, "console.js"), (ade, "app.js")):
        assert html.index('<script src="voice.js">') < html.index(f'<script src="{user}">')
    # the page says it, before anyone taps: the keyboard's own dictation, and whose service listens
    assert "Your keyboard's own dictation works here too." in page
    assert "Your browser's own speech service does the listening (Apple's\n        in Safari, Google's in Chrome), " \
           "not Proofpane." in page
    # the line in a chat's bar goes above the field, across the bar; an empty row (no API) takes no room
    assert ".con-send .voice-line { flex: 0 0 100%; order: -1; }" in css and ".voice-row:empty { display: none; }" in css
    assert "@media (prefers-reduced-motion: reduce) { .voice-mic.listening { animation: none; } }" in css
