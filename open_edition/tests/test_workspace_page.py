"""The hosted workspace's page and its terminal page.

The terminal page is the one page that needs inline style, because xterm.js writes its theme, cell sizes and scrollbar
into `<style>` elements it creates; these tests hold that the relaxation is on that page alone, that the library is the
one the vendor README names, and that the account's token never travels in a URL. The pages themselves were driven in
a browser through Caddy with this Caddyfile; what can be read from the files is
held here.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
WEB = os.path.join(HERE, "web")
VENDOR = os.path.join(WEB, "vendor", "xterm")
NODE = shutil.which("node")


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


def csp_lines():
    """{matcher: {directive: [values]}} for each Content-Security-Policy the Caddyfile sets."""
    out = {}
    for line in read(HERE, "Caddyfile").splitlines():
        m = re.match(r'\s*header (@\w+ )?Content-Security-Policy "([^"]+)"\s*$', line)
        if m:
            out[(m.group(1) or "").strip()] = {d.split()[0]: d.split()[1:] for d in (p.strip() for p in
                                                                                     m.group(2).split(";")) if d}
    return out


def test_the_desktop_and_phone_apps_hold_the_hosted_app_s_policy_for_scripts_images_and_frames():
    # the same pages ship three ways (the hosted app behind the Caddyfile, the desktop app, the phone app): what may run
    # or be shown in them must not drift apart, whichever one a change to the policy remembers
    hosted = csp_lines()["@terminal"]
    for app in ("desktop", "phone"):
        with open(os.path.join(HERE, app, "src-tauri", "tauri.conf.json")) as fh:
            policy = json.load(fh)["app"]["security"]["csp"]
        got = {d.split()[0]: d.split()[1:] for d in (p.strip() for p in policy.split(";")) if d}
        for directive in ("default-src", "script-src", "img-src", "frame-src", "object-src", "base-uri"):
            assert got.get(directive) == hosted.get(directive), (app, directive, got.get(directive), hosted.get(directive))


def test_every_vendored_file_is_the_one_its_readme_names():
    rows = re.findall(r"^\| `([^`]+)` \| [^|]+ \| `([0-9a-f]{64})` \|$", read(VENDOR, "README.md"), re.M)
    assert {name for name, _ in rows} == {"xterm.js", "xterm.css", "addon-fit.js", "LICENSE", "LICENSE-addon-fit"}
    assert set(os.listdir(VENDOR)) == {name for name, _ in rows} | {"README.md"}
    for name, digest in rows:
        with open(os.path.join(VENDOR, name), "rb") as fh:
            assert hashlib.sha256(fh.read()).hexdigest() == digest, name


def test_only_the_terminal_page_and_the_ade_app_allow_inline_style():
    csp = csp_lines()
    assert set(csp) == {"@pages", "@terminal"}
    pages, terminal = csp["@pages"], csp["@terminal"]
    assert pages["style-src"] == ["'self'"] and terminal["style-src"] == ["'self'", "'unsafe-inline'"]
    # the app shows a worktree's images and PDFs from memory (blob:, which only its own scripts make); nothing else
    # differs
    assert pages["img-src"] == ["'self'"] and terminal["img-src"] == ["'self'", "blob:"]
    assert "frame-src" not in pages and terminal["frame-src"] == ["blob:"]
    same = lambda c: {k: v for k, v in c.items() if k not in ("style-src", "img-src", "frame-src")}
    assert same(terminal) == same(pages)
    assert pages["script-src"] == ["'self'"] and pages["default-src"] == ["'none'"]
    caddy = read(HERE, "Caddyfile")
    # xterm.js and the ADE app (/app/, built from ui/) set style; nothing else may, and scripts stay 'self' everywhere
    assert "\t@pages not path /terminal.html /app/*\n" in caddy and "\t@terminal path /terminal.html /app/*\n" in caddy
    first = [line for line in caddy.splitlines() if "Content-Security-Policy" in line][0]
    assert "style-src 'self';" in first     # the other tests read the first CSP line as the site's


def test_the_terminal_page_loads_only_this_site_s_scripts():
    page = read(WEB, "terminal.html")
    assert re.findall(r'<script src="([^"]+)"></script>', page) == [
        "theme.js", "vendor/xterm/xterm.js", "vendor/xterm/addon-fit.js", "terminal.js"]
    assert not re.search(r"<script(?![^>]*\bsrc=)", page) and "style=" not in page and "://" not in page


def test_no_other_page_loads_the_terminal():
    for name in os.listdir(WEB):
        if name.endswith((".html", ".js")) and name not in ("terminal.html", "terminal.js"):
            assert "vendor/xterm" not in read(WEB, name) and "new Terminal(" not in read(WEB, name), name


def test_the_token_goes_in_the_first_message_never_in_a_url():
    script = read(WEB, "terminal.js")
    (url,) = re.findall(r'new WebSocket\(([^;]+)\);', script)
    url = " ".join(url.split())
    assert "token" not in url and "?" not in url.replace('=== "https:" ?', "") and url.endswith('+ "/api/ade/workspace/terminal"')
    assert 'ws.onopen = () => ws.send(JSON.stringify({ token, session, cols: term.cols, rows: term.rows }));' in script
    # the only text the page sends: the first message, which signs in, and a resize
    texts = re.findall(r"send\(JSON\.stringify\((\{[^}]*\})\)\)", script)
    assert texts == ["{ token, session, cols: term.cols, rows: term.rows }", '{ type: "resize", cols, rows }']
    assert 'session = (location.hash || "").slice(1)' in script
    app = read(WEB, "app.js")
    # a new session's page, a chat's in the console and any other in its terminal, by its id in the fragment
    assert app.count('(x.program === "chat" ? "console.html#" : "terminal.html#") + x.id') == 1
    assert "terminal.html?" not in app and "console.html?" not in app
    assert app.count('(x.chat ? "console.html#" : "terminal.html#") + x.id') == 1
    # the console's one socket, for every chat, signs in the same way
    console = read(WEB, "console.js")
    (url,) = re.findall(r'new WebSocket\(([^;]+)\);', console)
    assert "token" not in url and " ".join(url.split()).endswith('+ WS + "/chat"')
    assert re.findall(r"send\(JSON\.stringify\((\{[^}]*\})\)\)", console) == ["{ token }"]


def test_the_app_serves_the_terminal_page_and_the_library(client):
    for path in ("/terminal.html", "/terminal.js", "/vendor/xterm/xterm.js", "/vendor/xterm/xterm.css",
                 "/vendor/xterm/addon-fit.js"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", path


def test_the_app_s_hashed_files_are_kept_and_its_page_is_revalidated(client):
    # Vite names each built file by a hash of what it holds: a browser keeps those; the page naming them it asks again
    assets = os.path.join(WEB, "app", "assets")
    names = sorted(n for n in os.listdir(assets) if n.endswith((".js", ".css"))) if os.path.isdir(assets) else []
    if not names:
        pytest.skip("the app is not built here")
    r = client.get("/app/assets/" + names[0])
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=31536000, immutable"
    for path in ("/app/", "/app/index.html", "/app.js"):
        assert client.get(path).headers["cache-control"] == "no-cache", path
    missing = client.get("/app/assets/not-built-0000.js")
    assert missing.status_code == 404 and missing.headers.get("cache-control") != "public, max-age=31536000, immutable"


def test_the_page_watches_a_start_and_names_a_dropped_connection():
    # a phone showed only "Load failed" under Workspace while a start went on
    script = read(HERE, "web", "app.js")
    body = script[script.index("async function loadWorkspace()"):]
    body = body[:body.index("\n}\n")]
    assert "st.starting" in body and "setInterval(" in body and "clearInterval(wsStartWatch)" in body
    assert "st.start_error" in body
    assert "The connection to the ADE dropped" in script


def test_the_terminal_page_says_which_close_it_got():
    # a report of "Disconnected" carries the close code, so the next one says which close it was
    script = read(HERE, "web", "terminal.js")
    assert 'say("Disconnected (code " + ev.code + ").")' in script and "ws.onclose = (ev) =>" in script


def claude_gate(script):
    """The page's Claude gate as app.js declares it: its constants and state, and its functions."""
    names = ("NEEDS_CLAUDE", "CLAUDE_FIRST", "claudeSignedIn", "claudeHintEls")
    lines = [re.search(rf"^(?:const|let) {name} = .*$", script, re.M).group(0) for name in names]
    fns = [re.search(rf"function {name}\(.*?\n\}}\n", script, re.S).group(0)
           for name in ("claudeFirst", "claudeHints", "gateNewSession", "claudeRefused")]
    return "\n".join(lines) + "\n" + "".join(fns)


TABLE_SETUP = r"""
function node(tag) {
  const n = { tag, children: [], own: "", className: "", disabled: false, value: "", href: "",
              append(...xs) { for (const x of xs) this.children.push(typeof x === "string" ? { own: x } : x); },
              appendChild(x) { this.children.push(x); return x; }, addEventListener() {},
              insertRow() { return this.appendChild(node("tr")); } };
  Object.defineProperty(n, "textContent", {
    get() { return this.own + this.children.map((c) => c.tag ? c.textContent : c.own).join(""); },
    set(v) { this.own = String(v); this.children = []; } });
  return n;
}
global.document = { createElement: node };
const els = { "ws-repos": node("ul"), "ws-new": { repo: node("select") }, "ws-sessions": { tBodies: [node("tbody")] },
              "ws-gh-code": node("pre"), "ws-gh-code-box": node("div"), "ws-gh-signin": node("button"),
              "ws-claude-link": node("a"), "ws-claude-box": node("div"), "ws-claude-signin": node("button"),
              "ws-claude-sent": node("p") };
function $(id) { return els[id]; }
function cards() {}
function wsRun(f) { return f; }
function fail(e) { throw e; }
const WS = "/api/ade/workspace";
let SESSIONS = JSON.parse(process.argv[2]);
async function api(method, path) {
  if (path === WS + "/repos") return { repos: [] };
  if (path === WS + "/sessions") return { sessions: SESSIONS };
  return { messages: 0 };
}
const timers = [];
global.setInterval = (f, ms) => { timers.push([f, ms]); return timers.length; };
global.clearInterval = (t) => { timers[t - 1] = null; };
let reloaded = 0;
async function loadWorkspace() { reloaded += 1; }
"""

TABLE = TABLE_SETUP + r"""
loadWorkspaceBody().then(() => process.stdout.write(JSON.stringify({
  repos: els["ws-repos"].children.map((li) => li.textContent),
  options: els["ws-new"].repo.children.map((o) => [o.textContent, o.disabled, o.value]),
  rows: els["ws-sessions"].tBodies[0].children.map((tr) => ({
    program: tr.children[1].textContent, state: tr.children[3].textContent,
    links: tr.children[5].children.filter((c) => c.tag === "a").map((a) => [a.textContent, a.href]),
    buttons: tr.children[5].children.filter((c) => c.tag === "button").map((b) => b.textContent) })) })));
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_an_ended_session_reads_as_ended_and_an_empty_list_says_how_to_fill_it(tmp_path):
    # a terminal that "opens and exits" read as broken, and the session form's
    # repository list was empty with no word why
    script = read(HERE, "web", "app.js")
    grab = lambda name: re.search(rf"(?:async )?function {name}\(.*?\n\}}\n", script, re.S).group(0)
    probe = tmp_path / "table.js"
    probe.write_text(claude_gate(script)
                     + "".join(grab(f) for f in ("el", "cell", "unix", "buttonFor", "claudeSignInLink", "showSignIn", "loadWorkspaceBody"))
                     + "let ghWatch = null; let ghWatched = false; let ghLive = false; let claudeWatched = false; "
                     "let claudeLive = false; let claudeSession = null; let claudeCoded = null;\n" + TABLE)
    base = {"branch": None, "repo": None, "task": "Sign in to GitHub", "program": "github"}
    sessions = [dict(base, id="a" * 12, running=False, exit_code=1, ended_at=1790622240),   # 2026-09-28 19:04 UTC
                dict(base, id="b" * 12, running=True, exit_code=None, ended_at=None),
                dict(base, id="c" * 12, running=False, exit_code=None, ended_at=None),
                dict(base, id="d" * 12, running=False, exit_code=-9, ended_at=1790625600),   # Stop, after its grace
                dict(base, id="e" * 12, program="claude-sign-in", running=True, exit_code=None, ended_at=None),
                dict(base, id="f" * 12, program="claude", repo="demo", branch="pp/" + "f" * 12, running=True,
                     exit_code=None, ended_at=None)]
    run = subprocess.run([NODE, str(probe), json.dumps(sessions)], capture_output=True, text=True, check=True)
    got = json.loads(run.stdout)
    assert got["repos"] == ["No repository yet: clone one below. A private one needs the GitHub sign-in first."]
    assert got["options"] == [
        ["No repository yet: clone one above (a private one needs the GitHub sign-in)", True, ""],
        ["No repository (a shell in the workspace)", False, ""]]
    # newest first; a session with no repository has no change to finish
    gh, again, stop = "GitHub sign-in", ["Start again", "Remove"], ["Stop", "Remove"]
    assert got["rows"] == [
        {"program": "claude in demo", "state": "running", "links": [["Terminal", "terminal.html#" + "f" * 12]],
         "buttons": ["Stop", "Finish and verify", "Remove"]},
        {"program": "Claude sign-in", "state": "running", "links": [["Terminal", "terminal.html#" + "e" * 12]],
         "buttons": stop},
        {"program": gh, "state": "ended by signal 9 at 2026-09-28 20:00 UTC",
         "links": [["Show what it said", "terminal.html#" + "d" * 12]], "buttons": again},
        {"program": gh, "state": "stopped", "links": [], "buttons": again},
        {"program": gh, "state": "running", "links": [["Terminal", "terminal.html#" + "b" * 12]], "buttons": stop},
        {"program": gh, "state": "ended with status 1 at 2026-09-28 19:04 UTC",
         "links": [["Show what it said", "terminal.html#" + "a" * 12]], "buttons": again}]


SIGN_IN = TABLE_SETUP + r"""
(async () => {
  const seen = [];
  const look = () => seen.push({ code: els["ws-gh-code"].textContent, shown: !els["ws-gh-code-box"].hidden,
                                 watching: timers.filter(Boolean).length, reloaded, live: ghLive,
                                 button: !els["ws-gh-signin"].hidden });
  const tick = async () => { await timers.filter(Boolean)[0][0](); await new Promise((r) => setTimeout(r, 10)); };
  const github = (over) => Object.assign({ id: "a".repeat(12), program: "github", repo: null, branch: null,
                                           task: "Sign in to GitHub", running: true, exit_code: null }, over);
  SESSIONS = [github({ device_code: null })];                     // gh has not printed its code yet
  await loadWorkspaceBody(); look();
  const interval = timers[0][1];
  SESSIONS = [github({ device_code: "ABCD-1234" })];              // it has, and waits
  await tick(); look();
  SESSIONS = [github({ running: false, exit_code: 0, device_code: null })];   // the code was entered: it ended
  await tick(); look();
  process.stdout.write(JSON.stringify({ seen, interval }));
})();
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_the_sign_in_s_code_is_shown_while_gh_waits_and_the_panel_is_read_again_when_it_ends(tmp_path):
    # the sign-in on one screen, never in a terminal
    script, page = read(HERE, "web", "app.js"), read(HERE, "web", "index.html")
    assert '<pre id="ws-gh-code" class="copyable device-code"></pre>' in page
    assert '<a id="ws-gh-device" target="_blank" rel="noopener">' in page
    assert script.count('$("ws-gh-device").href = "https://github.com/login/device";') == 1   # fixed, never from gh
    grab = lambda name: re.search(rf"(?:async )?function {name}\(.*?\n\}}\n", script, re.S).group(0)
    probe = tmp_path / "signin.js"
    probe.write_text(claude_gate(script)
                     + "".join(grab(f) for f in ("el", "cell", "unix", "buttonFor", "claudeSignInLink", "showSignIn", "loadWorkspaceBody"))
                     + "let ghWatch = null; let ghWatched = false; let ghLive = false; let claudeWatched = false; "
                     "let claudeLive = false; let claudeSession = null; let claudeCoded = null;\n" + SIGN_IN)
    run = subprocess.run([NODE, str(probe), "[]"], capture_output=True, text=True, check=True)
    got = json.loads(run.stdout)
    # one sign-in at a time: its button is hidden while one runs, and loadGitHub puts it back from what gh says
    assert got["seen"] == [{"code": "", "shown": False, "watching": 1, "reloaded": 0, "live": True, "button": False},
                           {"code": "ABCD-1234", "shown": True, "watching": 1, "reloaded": 0, "live": True,
                            "button": False},
                           {"code": "", "shown": False, "watching": 0, "reloaded": 1, "live": False,
                            "button": False}]
    assert got["interval"] == 4000
    # and then loadGitHub says whom it signed in, from what gh answers, on this page that watched it
    body = script[script.index("async function loadGitHub()"):]
    body = body[:body.index("\n}\n")]
    assert '"GitHub signed in as " + gh.user' in body and '$("ws-gh-done").hidden = !(gh.user && ghWatched)' in body
    assert '$("ws-gh-signin").hidden = !gh.installed || !!gh.user || ghLive;' in body


FIT = r"""
let top = 0, scrolled = 0;
const box = { style: {}, getBoundingClientRect: () => ({ top }) };
const bar = { offsetHeight: 0 };
const view = { height: 0, offsetTop: 0 };
const term = { rows: 0, scrollToBottom() { scrolled += 1; } };
const fit = { fit() { term.rows = Math.floor((parseInt(box.style.height, 10) - 8) / 16); } };
const out = [];
for (const [t, h, o, b] of JSON.parse(process.argv[2])) {
  top = t; view.height = h; view.offsetTop = o; bar.offsetHeight = b;
  const before = scrolled;
  fitToView();
  out.push([box.style.height, term.rows, scrolled > before]);
}
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's function")
def test_the_terminal_ends_where_the_visible_page_ends_and_a_new_size_shows_the_last_row(tmp_path):
    # on an iPhone the keyboard covered the rows where claude keeps its input line; iOS
    # shrinks only the visual viewport, so the box is sized to that (checked in headless Chrome with a stand-in)
    script = read(HERE, "web", "terminal.js")
    fn = re.search(r"\n  function fitToView\(\) \{\n.*?\n  \}\n", script, re.S).group(0)
    probe = tmp_path / "fit.js"
    probe.write_text(fn + FIT)
    # (the box's top in the layout viewport, the visible height, the visible part's offset in the layout viewport,
    # the height of the bar under the box, which stays in view with it)
    cases = [(120, 844, 0, 0),       # no keyboard: to the bottom of the screen, less a margin
             (120, 508, 0, 0),       # the keyboard open: it ends above it, and the last row is shown
             (120, 508, 0, 0),       # the same again: nothing moves, and a reader's scrollback is left alone
             (120, 508, 336, 0),     # iOS panned what is visible down to the focused row: the bottom edge stays at
             (-216, 508, 0, 0),      # the keyboard; so it does when the page itself scrolled instead
             (120, 150, 0, 0),       # a very short view: never under 120 px
             (120, 508, 0, 96)]      # the bar: the box ends above it, and the bar above the keyboard
    got = json.loads(subprocess.run([NODE, str(probe), json.dumps(cases)], capture_output=True, text=True,
                                    check=True).stdout)
    assert [g[0] for g in got] == ["716px", "380px", "380px", "716px", "716px", "120px", "284px"]
    assert [g[2] for g in got] == [True, True, False, True, False, True, True]
    for listened in ('window.addEventListener("resize", refit);', 'window.addEventListener("scroll", refit',
                     'view.addEventListener("resize", refit);', 'view.addEventListener("scroll", refit);'):
        assert listened in script, listened
    assert "overflow: hidden" in re.search(r"#terminal \{[^}]*\}", read(HERE, "web", "style.css")).group(0)


CLAUDE = TABLE_SETUP + r"""
(async () => {
  const seen = [];
  const look = () => seen.push({ link: els["ws-claude-link"].href, box: !els["ws-claude-box"].hidden,
                                 button: !els["ws-claude-signin"].hidden, session: claudeSession,
                                 watching: timers.filter(Boolean).length, reloaded });
  const tick = async () => { await timers.filter(Boolean)[0][0](); await new Promise((r) => setTimeout(r, 10)); };
  const signIn = (over) => Object.assign({ id: "c".repeat(12), program: "claude-sign-in", repo: null, branch: null,
                                           task: "Sign in to Claude", running: true, exit_code: null,
                                           device_code: null, sign_in_url: null }, over);
  SESSIONS = [signIn({})];                                              // started; its page not printed yet
  await loadWorkspaceBody(); look();
  SESSIONS = [signIn({ sign_in_url: "https://claude.com.example.test/cai/oauth/authorize?x=1" })];
  await tick(); look();                                                 // not Claude's own address: no link
  SESSIONS = [signIn({ sign_in_url: process.argv[3] })];
  await tick(); look();
  SESSIONS = [signIn({ running: false, exit_code: 0 })];                // the code signed it in: it ended
  await tick(); look();
  process.stdout.write(JSON.stringify(seen));
})();
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_claude_s_sign_in_page_is_a_link_only_at_claude_s_own_address(tmp_path):
    # Claude's sign-in as GitHub's, on one screen, for a person new to it on a phone
    script, page = read(HERE, "web", "app.js"), read(HERE, "web", "index.html")
    for element in ("ws-claude-state", "ws-claude-signin", "ws-claude-box", "ws-claude-link", "ws-claude-code",
                    "ws-claude-sent", "ws-claude-done"):
        assert page.count(f'id="{element}"') == 1, element
    assert '<a id="ws-claude-link" target="_blank" rel="noopener">' in page
    grab = lambda name: re.search(rf"(?:async )?function {name}\(.*?\n\}}\n", script, re.S).group(0)
    probe = tmp_path / "claude.js"
    probe.write_text(claude_gate(script) + "".join(grab(f) for f in ("el", "cell", "unix", "buttonFor", "claudeSignInLink", "showSignIn",
                                               "loadWorkspaceBody"))
                     + "let ghWatch = null; let ghWatched = false; let ghLive = false; let claudeWatched = false; "
                       "let claudeLive = false; let claudeSession = null; let claudeCoded = null;\n" + CLAUDE)
    url = "https://claude.com/cai/oauth/authorize?code=true&client_id=x&state=y"
    run = subprocess.run([NODE, str(probe), "[]", url], capture_output=True, text=True, check=True)
    sid = "c" * 12
    assert json.loads(run.stdout) == [
        {"link": "", "box": False, "button": False, "session": sid, "watching": 1, "reloaded": 0},
        {"link": "", "box": False, "button": False, "session": sid, "watching": 1, "reloaded": 0},
        {"link": url, "box": True, "button": False, "session": sid, "watching": 1, "reloaded": 0},
        {"link": url, "box": False, "button": False, "session": None, "watching": 0, "reloaded": 1}]
    # the code goes to the running sign-in's own route, and the panel then says whom Claude signed in
    assert '"/sessions/" + encodeURIComponent(claudeSession) + "/sign-in-code"' in script
    body = script[script.index("async function loadClaude()"):]
    body = body[:body.index("\n}\n")]
    assert '"Claude signed in" + who' in body and '$("ws-claude-signin").hidden = !c.installed || !!c.signed_in' \
        ' || claudeLive;' in body


PICKER = r"""
function node(tag) {
  const n = { tag, children: [], own: "", disabled: false, hidden: false, value: "",
              appendChild(x) { this.children.push(x); return x; }, addEventListener() {} };
  Object.defineProperty(n, "textContent", {
    get() { return this.own + this.children.map((c) => c.textContent).join(""); },
    set(v) { this.own = String(v); this.children = []; this.picked = null; } });
  return n;
}
function selectNode() {
  const n = node("select");
  Object.defineProperty(n, "value", {      // as a browser's: the chosen option, else the first that can be chosen
    get() { const o = this.picked || this.children.find((c) => !c.disabled); return o ? o.value : ""; },
    set(v) { this.picked = this.children.find((c) => c.value === v) || null; } });
  return n;
}
global.document = { createElement: node };
const els = { "ws-gh-filter": node("input"), "ws-gh-select": selectNode(), "ws-gh-clone": node("button"),
              "ws-gh-count": node("p"), "ws-gh-pick": node("div"), "ws-gh-state": node("span"),
              "ws-gh-done": node("p"), "ws-gh-signin": node("button") };
function $(id) { return els[id]; }
const WS = "/api/ade/workspace";
let ghLive = false, ghWatched = false, ghRepos = [];
const ANSWER = JSON.parse(process.argv[2]);
async function api() { return ANSWER; }
(async () => {
  const seen = (label) => ({ label, count: els["ws-gh-count"].textContent, hidden: els["ws-gh-pick"].hidden,
    options: els["ws-gh-select"].children.map((o) => [o.textContent, o.value, o.disabled]),
    chosen: els["ws-gh-select"].value, clone: [els["ws-gh-clone"].textContent, els["ws-gh-clone"].disabled] });
  const out = [];
  await loadGitHub(); out.push(seen("all"));
  els["ws-gh-filter"].value = " ABC ";  showRepos(); out.push(seen("filtered"));
  els["ws-gh-select"].value = "https://github.com/acme/abc-cloned.git"; pickedRepo(); out.push(seen("a cloned one"));
  els["ws-gh-filter"].value = "ab";  showRepos(); out.push(seen("still chosen"));
  els["ws-gh-filter"].value = "zzz";  showRepos(); out.push(seen("none"));
  process.stdout.write(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_the_repositories_are_one_dropdown_narrowed_by_a_filter(tmp_path):
    # a repository picked from a dropdown, never a list printed all at once
    script, page = read(HERE, "web", "app.js"), read(HERE, "web", "index.html")
    assert 'id="ws-gh-repos"' not in page and '<select id="ws-gh-select"></select>' in page
    grab = lambda name: re.search(rf"(?:async )?function {name}\(.*?\n\}}\n", script, re.S).group(0)
    probe = tmp_path / "picker.js"
    probe.write_text("".join(grab(f) for f in ("el", "loadGitHub", "repoOption", "showRepos", "pickedRepo")) + PICKER)
    repo = lambda name, **kw: dict({"full_name": "acme/" + name, "private": False, "url":
                                    f"https://github.com/acme/{name}.git", "description": "", "cloned": False,
                                    "pushed": "2026-09-20"}, **kw)
    repos = [repo("zeta"), repo("abc-one", private=True), repo("abc-cloned", cloned=True, pushed=None),
             repo("other-ABC-x")]
    answer = {"installed": True, "user": "octocat", "repos": repos, "capped": True}
    got = json.loads(subprocess.run([NODE, str(probe), json.dumps(answer)], capture_output=True, text=True,
                                    check=True).stdout)
    url = lambda name: f"https://github.com/acme/{name}.git"
    assert got[0]["count"] == ("4 repositories, most recently pushed first (the 4 most recent: clone any other by its "
                               "address below).") and got[0]["hidden"] is False
    assert [o[0] for o in got[0]["options"]] == ["acme/zeta · public · pushed 2026-09-20",
                                                 "acme/abc-one · private · pushed 2026-09-20",
                                                 "acme/abc-cloned · public · cloned",
                                                 "acme/other-ABC-x · public · pushed 2026-09-20"]
    assert (got[0]["chosen"], got[0]["clone"]) == (url("zeta"), ["Clone", False])
    assert [o[1] for o in got[1]["options"]] == [url("abc-one"), url("abc-cloned"), url("other-ABC-x")]
    assert got[2]["clone"] == ["Cloned", True]
    assert (got[3]["chosen"], got[3]["clone"]) == (url("abc-cloned"), ["Cloned", True])   # narrowing keeps the choice
    assert got[4]["options"] == [['No repository\'s name has "zzz"', "", True]] and got[4]["clone"] == ["Clone", True]


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's function")
def test_a_button_wired_straight_to_wsrun_gets_itself_not_the_click(tmp_path):
    # as Sign out of Claude was wired: armed() was handed the click event and failed, and the Start and
    # sign-in buttons' `b.disabled = true` disabled nothing
    script = read(HERE, "web", "app.js")
    probe = tmp_path / "wsrun.js"
    probe.write_text(re.search(r"function wsRun\(fn\) \{.*?\n\}\n", script, re.S).group(0) + r"""
const notes = [];
function wsNote(t) { if (t) notes.push(t); }
const button = new EventTarget();
const got = [];
button.addEventListener("click", wsRun(async (b) => { await null; got.push(b === button); }));
button.dispatchEvent(new Event("click"));
wsRun(async (b) => { got.push(b === "given"); })("given");
setTimeout(() => process.stdout.write(JSON.stringify({ got, notes })), 20);
""")
    run = subprocess.run([NODE, str(probe)], capture_output=True, text=True, check=True)
    assert json.loads(run.stdout) == {"got": [True, True], "notes": []}


CLAUDE_STATE = r"""
function node() { return { textContent: "", hidden: false, append() {}, addEventListener() {} }; }
global.document = { createElement: node };
const els = {};
for (const id of ["ws-claude-state", "ws-claude-done", "ws-claude-signout", "ws-claude-signin", "ws-new-submit",
                  "ws-new-note"]) els[id] = node();
els["ws-new"] = { program: { value: "claude" } };
function $(id) { return els[id]; }
const WS = "/api/ade/workspace";
const [answer, live, watched, signedOut] = JSON.parse(process.argv[2]);
let claudeLive = live, claudeWatched = watched, claudeSignedOut = signedOut;
async function api() { return answer; }
function loadCapabilities() {}
loadClaude().then(() => process.stdout.write(JSON.stringify({ state: els["ws-claude-state"].textContent,
  done: els["ws-claude-done"].hidden ? null : els["ws-claude-done"].textContent,
  signin: !els["ws-claude-signin"].hidden, signout: !els["ws-claude-signout"].hidden,
  claude_session_blocked: els["ws-new-submit"].disabled })));
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's function")
@pytest.mark.parametrize("case, want", [
    (["in", False, False, False], {"state": "Signed in to Claude as owner@example.org (max).", "done": None,
                                   "signin": False, "signout": True}),
    (["in", False, True, False], {"state": "Signed in to Claude as owner@example.org (max).",
                                  "done": "Claude signed in as owner@example.org.", "signin": False, "signout": True}),
    (["out", False, False, False], {"state": "Not signed in to Claude.", "done": None, "signin": True,
                                    "signout": False}),
    (["out", True, True, False], {"state": "Signing in: open the page below, then paste the code it shows.",
                                  "done": None, "signin": False, "signout": False}),
    (["out", False, False, True], {"state": "Signed out of Claude: Claude Code's sign-in is deleted from this "
                                            "workspace.", "done": None, "signin": True, "signout": False}),
    (["none", False, False, False], {"state": "claude is not installed in this workspace's image.", "done": None,
                                     "signin": False, "signout": False})])
def test_the_claude_section_says_where_the_sign_in_stands(tmp_path, case, want):
    answers = {"in": {"installed": True, "signed_in": True, "email": "owner@example.org", "method": "claude.ai",
                      "subscription": "max"},
               "out": {"installed": True, "signed_in": False, "email": None, "method": "none", "subscription": None},
               "none": {"installed": False, "signed_in": False, "email": None, "method": None, "subscription": None}}
    script = read(HERE, "web", "app.js")
    probe = tmp_path / "state.js"
    el = re.search(r"function el\(.*?\n\}\n", script, re.S).group(0)
    probe.write_text(claude_gate(script) + el + re.search(r"async function loadClaude\(\) \{.*?\n\}\n", script,
                                                          re.S).group(0) + CLAUDE_STATE)
    run = subprocess.run([NODE, str(probe), json.dumps([answers[case[0]], *case[1:]])], capture_output=True, text=True,
                         check=True)
    # and a claude session waits for the sign-in: the form's button is off until Claude is signed in
    assert json.loads(run.stdout) == dict(want, claude_session_blocked=case[0] != "in")


GATE = TABLE_SETUP + r"""
const plain = document.createElement;
global.document = { createElement: (tag) => {
  const n = plain(tag);
  n.handlers = {};
  n.addEventListener = (type, f) => { n.handlers[type] = f; };
  return n;
} };
let scrolled = 0, focused = 0;
els["ws-new"].program = node("select");
els["ws-new-submit"] = node("button");
els["ws-new-note"] = Object.assign(node("p"), { hidden: true });
els["ws-note"] = Object.assign(node("p"), { hidden: true });
els["ws-claude"] = Object.assign(node("div"), { scrollIntoView() { scrolled += 1; } });
els["ws-claude-signin"] = Object.assign(node("button"), { hidden: false, focus() { focused += 1; } });
const gate = [];
for (const signed of [null, false, true]) {
  for (const program of ["claude", "chat", "codex", "shell"]) {
    claudeSignedIn = signed;
    els["ws-new"].program.value = program;
    gateNewSession();
    gate.push([signed, program, els["ws-new-submit"].disabled, els["ws-new-note"].hidden,
               els["ws-new-note"].textContent]);
  }
}
const hints = [node("span"), node("span")];
claudeHintEls = hints;
claudeSignedIn = false;
claudeHints();
const off = hints.map((h) => h.textContent);
claudeSignedIn = true;
claudeHints();
const on = hints.map((h) => h.textContent);
// the app's refusal, from any button: the note says it with a way to the sign-in, and the page goes there
els["ws-new"].program.value = "claude";
claudeRefused();
const refused = { signed: claudeSignedIn, submit: els["ws-new-submit"].disabled, hidden: els["ws-note"].hidden,
                  note: els["ws-note"].textContent, scrolled, hint: hints[0].textContent };
els["ws-note"].children.find((c) => c.tag === "button").handlers.click();
process.stdout.write(JSON.stringify({ gate, off, on, refused, after: { scrolled, focused } }));
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_a_claude_session_or_a_chat_waits_for_claude_s_sign_in_on_the_page(tmp_path):
    # a claude session started before Claude's sign-in brought Claude Code's own sign-in
    # into its terminal, where a phone cannot copy. The form does not send one, and says why with a way to the
    # sign-in; such a session's row says it; the app's refusal, from any button, takes the page to the sign-in.
    from proofpane_open.ade import workspace_routes
    script, page = read(HERE, "web", "app.js"), read(HERE, "web", "index.html")
    assert f'const CLAUDE_FIRST = "{workspace_routes.CLAUDE_FIRST}";' in script
    # the Claude block is the first thing in the Workspace section: above the workspace's Start, outside its body
    ws = page[page.index('<div id="ade-workspace"'):page.index('<div id="ade-board"')]
    assert ws.index('<div id="ws-claude" hidden>') < ws.index('id="ws-state"') < ws.index('<div id="ws-body" hidden>')
    assert '<p id="ws-new-note" class="error" hidden></p>' in ws and 'id="ws-new-submit"' in ws
    assert "After you approve, that page shows a code: copy it there, then paste it here" in ws
    # a chat opens in the console, never in a terminal
    assert '(x.program === "chat" ? "console.html#" : "terminal.html#") + x.id' in script
    probe = tmp_path / "gate.js"
    grab = lambda name: re.search(rf"(?:async )?function {name}\(.*?\n\}}\n", script, re.S).group(0)
    probe.write_text(claude_gate(script) + grab("el") + GATE)
    got = json.loads(subprocess.run([NODE, str(probe), "[]"], capture_output=True, text=True, check=True).stdout)
    first = workspace_routes.CLAUDE_FIRST + " Go to Sign in to Claude"
    assert got["gate"] == [
        [None, "claude", False, True, ""], [None, "chat", False, True, ""],
        [None, "codex", False, True, ""], [None, "shell", False, True, ""],
        [False, "claude", True, False, first], [False, "chat", True, False, first],
        [False, "codex", False, True, ""], [False, "shell", False, True, ""],
        [True, "claude", False, True, ""], [True, "chat", False, True, ""],
        [True, "codex", False, True, ""], [True, "shell", False, True, ""]]
    assert got["off"] == [" · Claude is not signed in: sign in above first"] * 2 and got["on"] == ["", ""]
    assert got["refused"] == {"signed": False, "submit": True, "hidden": False, "note": first, "scrolled": 1,
                              "hint": " · Claude is not signed in: sign in above first"}
    assert got["after"] == {"scrolled": 2, "focused": 1}


REFUSED = r"""
function node(tag) {
  const n = { tag, children: [], own: "", hidden: true, disabled: false, handlers: {},
              append(...xs) { for (const x of xs) this.children.push(typeof x === "string" ? { own: x } : x); },
              addEventListener(type, f) { this.handlers[type] = f; } };
  Object.defineProperty(n, "textContent", {
    get() { return this.own + this.children.map((c) => c.tag ? c.textContent : c.own).join(""); },
    set(v) { this.own = String(v); this.children = []; } });
  return n;
}
global.document = { createElement: node };
let scrolled = 0;
const els = { "ws-note": node("p"), "ws-new": { program: { value: "claude" } }, "ws-new-submit": node("button"),
              "ws-new-note": node("p"), "ws-claude": Object.assign(node("div"), { scrollIntoView() { scrolled += 1; } }),
              "ws-claude-signin": Object.assign(node("button"), { focus() {} }) };
function $(id) { return els[id]; }
const notes = [];
function wsNote(t) { if (t) notes.push(t); }
(async () => {
  await wsRun(async () => { throw new Error(CLAUDE_FIRST); })("given");
  const first = { note: els["ws-note"].textContent, hidden: els["ws-note"].hidden, scrolled, signed: claudeSignedIn,
                  submit: els["ws-new-submit"].disabled };
  await wsRun(async () => { throw new Error("The workspace is not answering."); })("given");
  process.stdout.write(JSON.stringify({ first, notes, scrolled }));
})();
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_the_app_s_refusal_before_claude_s_sign_in_takes_the_page_to_the_sign_in(tmp_path):
    # from any button (Start again on a claude session's row, the form): the words the app refuses with are known by
    # the page, which says them with a way to the sign-in and goes there; any other refusal is a plain note, as before
    from proofpane_open.ade import workspace_routes
    script = read(HERE, "web", "app.js")
    grab = lambda name: re.search(rf"(?:async )?function {name}\(.*?\n\}}\n", script, re.S).group(0)
    probe = tmp_path / "refused.js"
    probe.write_text(claude_gate(script) + grab("el") + grab("wsRun") + REFUSED)
    got = json.loads(subprocess.run([NODE, str(probe)], capture_output=True, text=True, check=True).stdout)
    assert got["first"] == {"note": workspace_routes.CLAUDE_FIRST + " Go to Sign in to Claude", "hidden": False,
                            "scrolled": 1, "signed": False, "submit": True}
    assert got["notes"] == ["The workspace is not answering."] and got["scrolled"] == 1


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_a_claude_session_s_row_says_when_claude_is_not_signed_in(tmp_path):
    script = read(HERE, "web", "app.js")
    grab = lambda name: re.search(rf"(?:async )?function {name}\(.*?\n\}}\n", script, re.S).group(0)
    base = {"branch": None, "repo": "demo", "task": "t", "running": True, "exit_code": None, "ended_at": None}
    sessions = [dict(base, id="a" * 12, program="claude"), dict(base, id="b" * 12, program="shell"),
                dict(base, id="c" * 12, program="chat", chat={"state": "idle"})]
    rows = {}
    for signed in ("null", "false", "true"):
        probe = tmp_path / f"rows-{signed}.js"
        probe.write_text(claude_gate(script) + "".join(grab(f) for f in (
            "el", "cell", "unix", "buttonFor", "claudeSignInLink", "showSignIn", "loadWorkspaceBody"))
            + "let ghWatch = null; let ghWatched = false; let ghLive = false; let claudeWatched = false; "
              f"let claudeLive = false; let claudeSession = null; let claudeCoded = null;\nclaudeSignedIn = {signed};\n" + TABLE)
        run = subprocess.run([NODE, str(probe), json.dumps(sessions)], capture_output=True, text=True, check=True)
        rows[signed] = [(r["program"], r["state"]) for r in json.loads(run.stdout)["rows"]]
    hint = " · Claude is not signed in: sign in above first"
    assert rows["false"] == [("a chat in demo", "waiting for you" + hint), ("shell in demo", "running"),
                             ("claude in demo", "running" + hint)]
    assert rows["null"] == rows["true"] == [("a chat in demo", "waiting for you"), ("shell in demo", "running"),
                                            ("claude in demo", "running")]


WATCH_SETUP = r"""
function node(tag) {
  const n = { tag, children: [], own: "", className: "", disabled: false, hidden: true, value: "", href: "", handlers: {},
              append(...xs) { for (const x of xs) this.children.push(typeof x === "string" ? { own: x } : x); },
              appendChild(x) { this.children.push(x); return x; }, addEventListener(type, f) { this.handlers[type] = f; },
              insertRow() { return this.appendChild(node("tr")); } };
  Object.defineProperty(n, "textContent", {
    get() { return this.own + this.children.map((c) => c.tag ? c.textContent : c.own).join(""); },
    set(v) { this.own = String(v); this.children = []; } });
  return n;
}
global.document = { createElement: node };
const made = { "ws-new": Object.assign(node("form"), { repo: node("select"), program: { value: "chat" } }),
               "ws-sessions": { tBodies: [node("tbody")] } };
function $(id) { return made[id] || (made[id] = node(id)); }
function cards() {}
function wsRun(f) { return f; }
const failed = [];
function fail(e) { failed.push(e.message); }
function loadCapabilities() {}
let claudeSignedOut = false;
let me = { id: "u1", email: "one@example.org" };            // the page's account, signed in as it is for the workspace
const WS = "/api/ade/workspace";
const timers = [];
global.setInterval = (f, ms) => { timers.push([f, ms]); return timers.length; };
global.clearInterval = (t) => { timers[t - 1] = null; };
let reloaded = 0;
async function loadWorkspace() { reloaded += 1; }
// the workspace as the test has it, every request kept in order: the path it names failing, and a read of an
// account's Claude sign-in (GET /claude?account=) held while HOLD is on, as one that does not come back, until the test
// lets it go, answered then as Claude Code's status was when it was asked; a code's POST held so while HOLD_CODE is on
const asked = [];
let SESSIONS = [], REPOS = [], FAILING = null, HOLD = false, HOLD_CODE = false;
let STATUS = { installed: true, signed_in: false, email: null, method: "none", subscription: null };
const held = [];
const letGo = () => { for (const go of held.splice(0)) go(); };
async function api(method, path) {
  asked.push(method + " " + path);
  if (path === FAILING) throw new Error("The workspace is not answering.");
  if (path === WS + "/repos") return { repos: REPOS };
  if (path === WS + "/sessions") return { sessions: SESSIONS };
  if (path.endsWith("/sign-in-code")) {
    const record = SESSIONS.find((x) => path.includes(x.id));
    return HOLD_CODE ? new Promise((r) => held.push(() => r(record))) : record;
  }
  if (path === WS + "/claude" || path.startsWith(WS + "/claude?")) {
    const now = STATUS;
    return HOLD && path.includes("?account=") ? new Promise((r) => held.push(() => r(now))) : now;
  }
  return { messages: 0 };
}
const settle = () => new Promise((r) => setTimeout(r, 10));
const tick = async () => { await timers.filter(Boolean)[0][0](); await settle(); };
const signIn = (over) => Object.assign({ id: "c".repeat(12), program: "claude-sign-in", repo: null, branch: null,
                                         task: "Sign in to Claude", running: true, exit_code: null, ended_at: null,
                                         device_code: null, account: null,
                                         sign_in_url: "https://claude.com/cai/oauth/authorize?code=true&state=y" }, over);
const sendCode = async () => {
  $("ws-claude-code").handlers.submit({ preventDefault() {}, target: { code: { value: " abc#def " }, reset() {} } });
  await settle();
};
const seen = [];
const look = (label) => seen.push({ label, reads: asked.filter((a) => a.startsWith("GET " + WS + "/claude?")).length,
  // the line saying the code was sent is in the box with Claude's page and the code's field (index.html)
  sent: $("ws-claude-box").hidden || $("ws-claude-sent").hidden ? null : $("ws-claude-sent").textContent,
  box: !$("ws-claude-box").hidden, done: $("ws-claude-done").hidden ? null : $("ws-claude-done").textContent,
  line: $("ws-claude-state").textContent, running: SESSIONS.filter((x) => x.running).length,
  watching: timers.filter(Boolean).length, reloaded });
"""


def watch_probe(script):
    """A probe of the page's watch over its sign-ins, as app.js declares it: the setup above, the Claude gate, the
    functions the watch uses, the watch's own state and functions (from `let ghWatch` up to loadWorkspaceBody), and
    the submit of Claude's code form, which sends the code."""
    grab = lambda name: re.search(rf"(?:async )?function {name}\(.*?\n\}}\n", script, re.S).group(0)
    watch = script[script.index("let ghWatch = null;"):script.index("async function loadWorkspaceBody() {")]
    submit = re.search(r'\$\("ws-claude-code"\)\.addEventListener\("submit", .*?\n\}\);\n', script, re.S).group(0)
    return (WATCH_SETUP + claude_gate(script) + "".join(grab(f) for f in (
        "el", "cell", "unix", "buttonFor", "claudeSignInLink", "loadClaude", "loadWorkspaceBody")) + watch + submit)


LISTS = r"""
(async () => {
  SESSIONS = [signIn({}), { id: "f".repeat(12), program: "claude", repo: "demo", branch: "pp/" + "f".repeat(12),
                            task: "Fix it", running: true, exit_code: null, ended_at: null }];
  REPOS = [{ name: "demo", label: "github.com/acme/demo", branch: "main", head: "abcdef0123" }];
  FAILING = WS + "/" + process.argv[2];
  let thrown = null;
  try { await loadWorkspaceBody(); } catch (e) { thrown = e.message; }
  await settle();
  process.stdout.write(JSON.stringify({ thrown, repos: $("ws-repos").children.map((li) => li.textContent),
    rows: $("ws-sessions").tBodies[0].children.map((tr) => tr.children[1].textContent), box: !$("ws-claude-box").hidden,
    watching: timers.filter(Boolean).length, session: claudeSession }));
})();
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
@pytest.mark.parametrize("failing, want", [
    ("repos", {"repos": [], "rows": ["claude in demo", "Claude sign-in"], "box": True, "watching": 1,
               "session": "c" * 12}),
    ("sessions", {"repos": ["demo · github.com/acme/demo · main at abcdef0"], "rows": [], "box": False,
                  "watching": 0, "session": None})])
def test_the_sessions_show_whether_or_not_the_repositories_answer_and_the_other_way_round(tmp_path, failing, want):
    # on the canary the workspace answered GET /sessions while GET /repos failed, and the page read the two together,
    # so a Claude sign-in stayed shown as it was: each is read and shown on its own, the failure said as before
    script = read(HERE, "web", "app.js")
    probe = tmp_path / "lists.js"
    probe.write_text(watch_probe(script) + LISTS)
    got = json.loads(subprocess.run([NODE, str(probe), failing], capture_output=True, text=True, check=True).stdout)
    assert got == dict(want, thrown="The workspace is not answering.")


SIGNED_IN = r"""
(async () => {
  SESSIONS = [signIn({})];
  await loadWorkspaceBody(); look("its page and its code shown");
  await sendCode(); look("its code sent");
  HOLD = true;
  await tick(); look("a look");
  await tick(); look("a look while that read is under way");
  letGo();
  await settle(); look("that read answered: not signed in yet");
  HOLD = false;
  STATUS = { installed: true, signed_in: true, email: "me@example.org", method: "claude.ai", subscription: "max" };
  await tick(); look("signed in, the sign-in's program still running");
  await tick(); look("a look after");
  process.stdout.write(JSON.stringify({ seen, failed, claude: asked.filter((a) => a.startsWith("GET " + WS + "/claude")) }));
})();
"""

WS_CLAUDE = "GET /api/ade/workspace/claude"

ENDED = r"""
(async () => {
  SESSIONS = [signIn({})];
  await loadWorkspaceBody();
  await sendCode();
  HOLD = true;
  await tick(); look("a look, its read not answered");
  // Claude Code signs it in and its program ends
  STATUS = { installed: true, signed_in: true, email: "me@example.org", method: "claude.ai", subscription: "max" };
  SESSIONS = [signIn({ running: false, exit_code: 0, ended_at: 1790622240 })];
  HOLD = false;
  await tick(); look("the look that sees it ended");
  letGo();                               // the read from before its end, answered as Claude Code's status was then
  await settle(); look("the read from before its end answered");
  process.stdout.write(JSON.stringify({ seen, failed, claude: asked.filter((a) => a.startsWith("GET " + WS + "/claude")) }));
})();
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_after_its_code_the_claude_sign_in_says_signed_in_as_soon_as_its_account_s_status_does(tmp_path):
    # the canary's page said "Code sent" until it was reloaded: it said "signed in" only once the sign-in's program had
    # ended and a whole reload had gone through. Now each look after the code reads that sign-in's account's status,
    # one read at a time, and the page says it is signed in as soon as one says so, the program still running, with
    # no reload; the Claude line is read again then
    script = read(HERE, "web", "app.js")
    probe = tmp_path / "signed_in.js"
    probe.write_text(watch_probe(script) + SIGNED_IN)
    got = json.loads(subprocess.run([NODE, str(probe)], capture_output=True, text=True, check=True).stdout)
    sent = "Code sent: Claude Code is checking it. This page says when it is signed in."
    waiting = {"sent": sent, "box": True, "done": None, "line": "", "running": 1, "watching": 1, "reloaded": 0}
    signed = dict(waiting, sent=None, box=False, done="Claude signed in as me@example.org.",
                  line="Signed in to Claude as me@example.org (max).")
    assert got["seen"] == [dict(waiting, label="its page and its code shown", reads=0, sent=None),
                           dict(waiting, label="its code sent", reads=0),
                           dict(waiting, label="a look", reads=1),
                           dict(waiting, label="a look while that read is under way", reads=1),
                           dict(waiting, label="that read answered: not signed in yet", reads=1),
                           dict(signed, label="signed in, the sign-in's program still running", reads=2),
                           dict(signed, label="a look after", reads=2)]
    assert got["failed"] == []
    assert got["claude"] == [WS_CLAUDE + "?account=default"] * 2 + [WS_CLAUDE]


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_a_claude_sign_in_that_ends_after_its_code_says_signed_in_from_its_own_read(tmp_path):
    # its program ending once it signed in (as the stand-in's does, and the workspace's agent ends it), with a read of
    # its status from before still under way: the look that sees it ended reads its account's status once more, and
    # says signed in from that, not from the workspace's reload, which a GET /repos that fails would stop before
    # Claude's line is read
    script = read(HERE, "web", "app.js")
    probe = tmp_path / "ended.js"
    probe.write_text(watch_probe(script) + ENDED)
    got = json.loads(subprocess.run([NODE, str(probe)], capture_output=True, text=True, check=True).stdout)
    signed = {"sent": None, "box": False, "done": "Claude signed in as me@example.org.",
              "line": "Signed in to Claude as me@example.org (max).", "running": 0, "watching": 0, "reloaded": 1,
              "reads": 2}
    assert got["seen"] == [
        {"label": "a look, its read not answered", "reads": 1, "box": True, "done": None, "line": "", "running": 1,
         "watching": 1, "reloaded": 0,
         "sent": "Code sent: Claude Code is checking it. This page says when it is signed in."},
        dict(signed, label="the look that sees it ended"),
        dict(signed, label="the read from before its end answered")]
    assert got["failed"] == []
    assert got["claude"] == [WS_CLAUDE + "?account=default"] * 2 + [WS_CLAUDE]


SIGNED_OUT = r"""
// what the page's sign-out and its next sign-in touch besides, as stand-ins
function stored() {}
function stopRefresh() {}
function show() {}
function route() {}
const ade = {};
const CASE = process.argv[2];
const ONE = { installed: true, signed_in: true, email: "one@example.org", method: "claude.ai", subscription: "max" };
(async () => {
  SESSIONS = [signIn({})];
  await loadWorkspaceBody();
  if (CASE === "signed out during its code's POST") {
    HOLD_CODE = true;                     // the code's POST not answered before the sign-out
    await sendCode();
  } else {
    await sendCode();
    // a look reads the first account's sign-in: answered before the sign-out, not signed in yet or signed in; or not
    // answered by then, its account's Claude Code having signed in
    if (CASE === "its read in flight" || CASE === "seen signed in before the sign-out") STATUS = ONE;
    HOLD = CASE === "its read in flight";
    await tick();
  }
  $("sign-out").handlers.click();
  signedIn({ id: "u2", email: "two@example.org" });
  // the next account's workspace: its own Claude Code signed in, and a sign-in of its own running, or none
  const own = CASE === "its own sign-in running" || CASE === "signed out during its code's POST";
  SESSIONS = own ? [signIn({ id: "d".repeat(12) })] : [];
  STATUS = { installed: true, signed_in: true, email: "two@example.org", method: "claude.ai", subscription: "max" };
  HOLD = HOLD_CODE = false;
  letGo();
  await settle();
  await tick();                           // the next account's first look
  process.stdout.write(JSON.stringify({ box: !$("ws-claude-box").hidden,
    // the line saying the code was sent is in the box with Claude's page and the code's field (index.html)
    sent: $("ws-claude-box").hidden || $("ws-claude-sent").hidden ? null : $("ws-claude-sent").textContent,
    done: $("ws-claude-done").hidden ? null : $("ws-claude-done").textContent,
    claude: asked.filter((a) => a.startsWith("GET " + WS + "/claude")), failed }));
})();
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
@pytest.mark.parametrize("case, box, claude", [
    ("its read answered", False, [WS_CLAUDE + "?account=default"]),
    ("its read in flight", False, [WS_CLAUDE + "?account=default"]),
    ("its own sign-in running", True, [WS_CLAUDE + "?account=default"]),
    ("signed out during its code's POST", True, []),
    ("seen signed in before the sign-out", False, [WS_CLAUDE + "?account=default", WS_CLAUDE])])
def test_a_claude_sign_in_s_code_is_forgotten_with_the_account_that_sent_it(tmp_path, case, box, claude):
    # one account sends its sign-in's code, then the page signs out and another signs in: the next account's looks
    # never read the first one's sign-in, a read of it still under way says nothing on the next account's page, and the
    # next account's page says nothing of it: no "Code sent" over a sign-in of its own, whether the sign-out came after
    # the code's POST answered or while it was under way, and no "Claude signed in as" of the first account's
    script = read(HERE, "web", "app.js")
    grab = lambda name: re.search(rf"(?:async )?function {name}\(.*?\n\}}\n", script, re.S).group(0)
    sign_out = re.search(r'\$\("sign-out"\)\.addEventListener\("click", .*?\n\}\);\n', script, re.S).group(0)
    probe = tmp_path / "signed_out.js"
    probe.write_text(watch_probe(script) + grab("signedIn") + sign_out + SIGNED_OUT)
    got = json.loads(subprocess.run([NODE, str(probe), case], capture_output=True, text=True, check=True).stdout)
    assert got == {"box": box, "sent": None, "done": None, "claude": claude, "failed": []}


WORKSPACE = r"""
function node(tag) {
  const n = { tag, children: [], own: "", hidden: false, disabled: false, append() {}, addEventListener() {} };
  Object.defineProperty(n, "textContent", { get() { return this.own; }, set(v) { this.own = String(v); } });
  return n;
}
const made = {};
function $(id) { return made[id] || (made[id] = node(id)); }
const WS = "/api/ade/workspace";
const [st] = JSON.parse(process.argv[2]);
async function api() { return st; }
function wsNote() {}
function fail(e) { throw e; }
const called = [];
async function loadWorkspaceBody() { called.push("body"); }
function loadGitHub() { called.push("github"); }
function loadClaude() { called.push("claude"); }
async function loadFinishes() {}
let wsStartWatch = null;
global.setInterval = () => 1;
global.clearInterval = () => {};
for (const id of ["ws-claude-signin", "ws-claude-signout", "ws-claude-box", "ws-claude-done"]) $(id).hidden = false;
loadWorkspace().then(() => process.stdout.write(JSON.stringify({
  block: !$("ws-claude").hidden, line: $("ws-claude-state").textContent, called, signed: claudeSignedIn,
  shown: ["ws-claude-signin", "ws-claude-signout", "ws-claude-box", "ws-claude-done"].filter((id) => !$(id).hidden) })));
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
@pytest.mark.parametrize("st, want", [
    ({"enabled": True, "allowed": True, "running": False},
     {"block": True, "line": "Claude: start the workspace below, then sign in to Claude here.", "called": [],
      "signed": None, "shown": []}),
    ({"enabled": True, "allowed": True, "running": True},
     {"block": True, "line": "", "called": ["body", "github", "claude"], "signed": False,
      "shown": ["ws-claude-signin", "ws-claude-signout", "ws-claude-box", "ws-claude-done"]}),
    ({"enabled": True, "allowed": False, "detail": "Not for this account.", "running": False},
     {"block": False, "line": "Claude: start the workspace below, then sign in to Claude here.", "called": [],
      "signed": None, "shown": []})])
def test_the_claude_block_is_there_before_the_workspace_runs_and_says_to_start_it(tmp_path, st, want):
    # the Claude line and its button are the first thing in the Workspace section, so a phone shows them first; with
    # the workspace stopped they say to start it, and no sign-in control shows (a sign-in runs in the workspace)
    script = read(HERE, "web", "app.js")
    body = re.search(r"async function loadWorkspace\(\) \{.*?\n\}\n", script, re.S).group(0)
    probe = tmp_path / "workspace.js"
    probe.write_text(claude_gate(script) + "claudeSignedIn = false;\n" + body + WORKSPACE)
    got = json.loads(subprocess.run([NODE, str(probe), json.dumps([st])], capture_output=True, text=True,
                                    check=True).stdout)
    assert got == want


def test_the_chat_is_the_form_s_first_program_and_the_console_is_one_tap_away():
    # a person signed in who started the form's first program, claude in a terminal, met Claude Code's first-run
    # wizard there. The chat, which never shows a terminal, comes first and is the one chosen; the
    # terminal programs come last and say so; the console is a button beside the form's own
    page = read(HERE, "web", "index.html")
    form = page[page.index('<form id="ws-new">'):]
    form = form[:form.index("</form>")]
    assert re.findall(r'<option value="(\w+)">([^<]+)</option>', form) == [
        ("chat", "a chat with Claude (in the console)"), ("shell", "a shell"),
        ("claude", "claude in a terminal (advanced)"), ("codex", "codex in a terminal (advanced)")]
    assert "selected" not in form
    assert re.search(r'<button type="submit" id="ws-new-submit">Start a session</button>\s*'
                     r'<a class="button-link" href="console.html">Open the console</a>', form)


TERMINAL_KEYS = r"""
const line = (text, wrapped) => ({ isWrapped: wrapped, translateToString(trim) { return trim ? text.trimEnd() : text; } });
const buffer = (lines) => ({ length: lines.length, getLine: (i) => lines[i] });
const keys = ["enter", "esc", "tab", "up", "down", "left", "right", "ctrl_c", "ctrl_d", "nope"];
process.stdout.write(JSON.stringify({
  normal: keys.map((k) => keyBytes(k, false)), app: keys.map((k) => keyBytes(k, true)),
  copy: lastLines(buffer([line("$ ls   ", false), line("a very long line that the terminal wrapped at its ", false),
                          line("width   ", true), line("b   ", false), line("", false), line("   ", false)]), 200),
  last2: lastLines(buffer([line("one", false), line("two", false), line("three", false)]), 2),
  empty: lastLines(buffer([]), 200) }));
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_the_terminal_s_bar_sends_what_a_terminal_s_keys_send_and_copies_its_last_lines(tmp_path):
    # on a phone, in a terminal: no copy, no paste, and a "2" typed into Claude Code's first-run wizard showed
    # nothing. The bar under the terminal: a field whose text shows, Send (the text and Enter)
    # and Type (the text alone), the keys a phone lacks, Paste and Copy
    script, page = read(WEB, "terminal.js"), read(WEB, "terminal.html")
    for element in ("term-bar", "term-line", "term-input", "term-type", "term-keys", "term-paste", "term-copy",
                    "term-note"):
        assert page.count(f'id="{element}"') == 1, element
    assert re.search(r'<input id="term-input" autocapitalize="off" autocorrect="off" spellcheck="false" '
                     r'autocomplete="off"\s+enterkeyhint="send"', page)
    assert re.findall(r'data-key="(\w+)"', page) == ["enter", "esc", "tab", "up", "down", "left", "right", "ctrl_c",
                                                     "ctrl_d"]
    # Send is the text and Enter, Type the text alone; a paste goes in as a paste (bracketed when asked for); a tap
    # on the bar keeps the phone's keyboard open; on a phone the terminal does not take the focus by itself
    assert 'typeText(input.value + "\\r");' in script and "if (input.value) typeText(input.value);" in script
    assert "if (text) term.paste(text);" in script
    assert 'bar.addEventListener("mousedown", (ev) => { if (ev.target.closest("button")) ev.preventDefault(); });' \
        in script
    assert "if (!touch) term.focus();" in script
    assert "term.hasSelection() ? term.getSelection() : lastLines(term.buffer.active, 200)" in script
    probe = tmp_path / "keys.js"
    fns = "".join(re.search(rf"\nfunction {name}\(.*?\n\}}\n", script, re.S).group(0)
                  for name in ("keyBytes", "lastLines"))
    probe.write_text(fns + TERMINAL_KEYS)
    got = json.loads(subprocess.run([NODE, str(probe)], capture_output=True, text=True, check=True).stdout)
    assert got["normal"] == ["\r", "\x1b", "\t", "\x1b[A", "\x1b[B", "\x1b[D", "\x1b[C", "\x03", "\x04", None]
    assert got["app"] == ["\r", "\x1b", "\t", "\x1bOA", "\x1bOB", "\x1bOD", "\x1bOC", "\x03", "\x04", None]
    assert got["copy"] == "$ ls\na very long line that the terminal wrapped at its width\nb"
    assert got["last2"] == "two\nthree" and got["empty"] == ""


UPDATE = r"""
function node(tag) { return { tag, textContent: "", hidden: true, disabled: false, dataset: {} }; }
const made = {};
function $(id) { return made[id] || (made[id] = node(id)); }
let updateWatch = null;
const timers = [];
global.setInterval = (f) => { timers.push(f); return timers.length; };
global.clearInterval = () => {};
let reread = 0;
function loadCapabilities() { reread += 1; }
function loadClaudeUpdate() {}
const out = [];
for (const u of JSON.parse(process.argv[2])) {
  showClaudeUpdate(u);
  out.push([$("ws-claude-update").hidden, $("ws-claude-update-state").textContent, $("ws-claude-update-mode").textContent,
            $("ws-claude-update-mode").dataset.next || null, $("ws-claude-update-now").disabled, !!updateWatch, reread]);
}
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_the_page_says_how_claude_code_is_kept_current(tmp_path):
    # updates automatic by default or only when the owner says, a check now,
    # and how the last one went, in words; the choices are read again once a new build is in use
    script = read(HERE, "web", "app.js")
    grab = lambda name: re.search(rf"function {name}\(.*?\n\}}\n", script, re.S).group(0)
    probe = tmp_path / "update.js"
    probe.write_text(re.search(r"function unix\([^\n]*\n", script).group(0) + grab("updateWords")
                     + grab("showClaudeUpdate") + UPDATE)
    auto = {"enabled": True, "mode": "automatic", "version": "2.1.274", "updating": False, "last": None}
    cases = [{"enabled": False},
             auto,
             dict(auto, updating=True),
             dict(auto, version="2.1.280", last={"at": 1790669000, "ok": True, "version": "2.1.280", "changed": True}),
             dict(auto, mode="manual", version="2.1.280",
                  last={"at": 1790669000, "ok": True, "version": "2.1.280", "changed": False}),
             dict(auto, last={"at": 1790669000, "ok": False, "version": "2.1.281",
                              "why": "the Claude Agent SDK could not start it (CLIConnectionError)"}),
             dict(auto, last={"at": 1790669000, "ok": False, "version": None,
                              "why": "Claude Code's install did not finish"})]
    got = json.loads(subprocess.run([NODE, str(probe), json.dumps(cases)], capture_output=True, text=True,
                                    check=True).stdout)
    daily = "Updates: automatic, at each start and once a day."
    at = " (2026-09-29 08:03 UTC)"
    assert got == [
        [True, "", "", None, False, False, 0],
        [False, daily, "Only when I say", "manual", False, False, 0],
        [False, "Checking for a newer Claude Code...", "Only when I say", "manual", True, True, 0],
        [False, daily + " Updated to 2.1.280" + at + ".", "Only when I say", "manual", False, False, 1],
        [False, "Updates: only when you say. 2.1.280 is the newest" + at + ".", "Update automatically", "automatic",
         False, False, 1],
        [False, daily + " The update to 2.1.281 did not pass its check" + at + ": the Claude Agent SDK could not "
         "start it (CLIConnectionError); Claude Code 2.1.274 stays in use.", "Only when I say", "manual", False,
         False, 1],
        [False, daily + " The update did not pass its check" + at + ": Claude Code's install did not finish; "
         "Claude Code 2.1.274 stays in use.", "Only when I say", "manual", False, False, 1]]
