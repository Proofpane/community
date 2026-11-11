"""The console page (console.html, console.js): what it is made
of, and its functions run through node against a stand-in DOM. It was also driven in a headless browser with a
stand-in claude behind a real agent (the commit that added it says how)."""

import json
import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
NODE = shutil.which("node")


def read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_every_element_the_console_uses_is_on_its_page_once():
    script, page = read("web", "console.js"), read("web", "console.html")
    used = set(re.findall(r'\$\("([\w-]+)"\)', script))
    assert {"con-list", "con-panes", "con-queue", "con-queue-box", "con-new", "con-note", "con-start", "con-side-grip",
            "con-main", "con-pick"} <= used
    for element in used:
        assert page.count(f'id="{element}"') == 1, element


def test_the_console_loads_only_this_site_s_files_and_writes_no_html():
    script, page = read("web", "console.js"), read("web", "console.html")
    assert re.findall(r'<script[^>]*>', page) == ['<script src="theme.js">', '<script src="chat-settings.js">',
                                                  '<script src="voice.js">', '<script src="console.js">']
    assert "style=" not in page
    assert "://" not in page and "<style" not in page
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        for name in ("console.js", "chat-settings.js", "voice.js", "theme.js"):
            assert sink not in read("web", name), (name, sink)
    # the one link it makes from data: a session's terminal page on this site, by its id, in the fragment
    assert re.findall(r"\.href = ([^;]+);", script) == ['"terminal.html#" + s.id']
    # sizes go through the CSS object model, which the strict policy allows, never a style attribute
    assert "setAttribute(\"style\"" not in script and ".style.setProperty(" in script
    # the Caddyfile gives every page but the terminal's the strict policy: no inline style, no inline script
    assert "@pages not path /terminal.html" in read("Caddyfile")


def test_the_app_serves_the_console(client):
    for path in ("/console.html", "/console.js"):
        assert client.get(path).status_code == 200, path


PURE = r"""
const now = 1790630000;
const S = (over) => Object.assign({ id: "a".repeat(12), program: "chat", repo: "demo", task: "Fix it\nmore",
                                    running: true, exit_code: null, created_at: now - 100, chat: null }, over);
const chat = (state) => ({ state, last_text: null, cost: 0, last_at: null, pending: [] });
const list = [S({ id: "1", chat: chat("idle"), created_at: now - 50 }), S({ id: "2", chat: chat("needs_you") }),
              S({ id: "3", program: "shell", running: false, exit_code: 2 }), S({ id: "4", chat: chat("running") }),
              S({ id: "5", program: "github", repo: null, running: false, exit_code: -9 }),
              S({ id: "6", program: "codex", running: false })];
process.stdout.write(JSON.stringify({
  order: sortedSessions(list).map((s) => s.id),
  words: list.map((s) => stateWord(s)),
  labels: list.map((s) => label(s)),
  since: [since(now - 5, now), since(now - 600, now), since(now - 7200, now), since(now - 3 * 86400, now)],
  money: [money(0.0123), money(1), money(0.00004)],
  blocks: textBlocks("Here:\n```js\nlet a = 1;\n```\nand more\n```\nunclosed"),
  empty: textBlocks("```\n```\n\n"),
  waiting: waitingAsks([S({ id: "x", chat: Object.assign(chat("needs_you"), { asks: [{ id: "b", time: 9 }, { id: "a", time: 3 }] }) }),
                        S({ id: "y", chat: Object.assign(chat("needs_you"), { asks: [{ id: "c", time: 5 }] }) }),
                        S({ id: "z", chat: chat("idle") }), S({ id: "t", program: "shell" })]).map((w) => w.s.id + w.a.id),
  cells: [1, 2, 3, 4].map((n) => cells(n)),
}));
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_the_list_puts_who_needs_you_first_in_plain_words(tmp_path):
    script = read("web", "console.js")
    grab = lambda name: re.search(rf"function {name}\(.*?\n\}}\n", script, re.S).group(0)
    one = lambda name: re.search(rf"function {name}\([^\n]*\}}\n", script).group(0)
    consts = re.search(r"const WORDS = .*?\nconst FIRST = [^\n]*\n", script, re.S).group(0)
    probe = tmp_path / "pure.js"
    probe.write_text(consts + "".join(grab(f) for f in ("stateOf", "stateWord", "sortedSessions", "label", "since",
                                                        "textBlocks", "waitingAsks", "cells")) + one("money") + PURE)
    got = json.loads(subprocess.run([NODE, str(probe)], capture_output=True, text=True, check=True).stdout)
    assert got["order"] == ["2", "4", "1", "3", "5", "6"]         # needs you, running, then the newest first
    assert got["words"] == ["idle", "needs you", "ended with status 2", "running", "ended by signal 9", "stopped"]
    assert got["labels"] == ["Chat in demo", "Chat in demo", "shell in demo", "Chat in demo", "GitHub sign-in",
                             "codex in demo"]
    assert got["since"] == ["just now", "10 min ago", "2 h ago", "3 d ago"]
    assert got["money"] == ["$0.0123", "$1.0000", "$0.0000"]
    assert got["blocks"] == [["p", "Here:"], ["pre", "let a = 1;"], ["p", "and more"], ["pre", "unclosed"]]
    assert got["empty"] == []
    assert got["waiting"] == ["xa", "yc", "xb"]                   # every chat's questions, the oldest first
    assert got["cells"] == [{"panes": ["one"], "lines": []}, {"panes": ["left", "right"], "lines": ["cols"]},
                            {"panes": ["tl", "tr", "bottom"], "lines": ["cols", "rows"]},
                            {"panes": ["tl", "tr", "bl", "br"], "lines": ["cols", "rows"]}]


DRAW = r"""
function node(tag) {
  const n = { tag, children: [], own: "", className: "", type: "", disabled: false,
              append(...xs) { for (const x of xs) this.children.push(typeof x === "string" ? { own: x } : x); },
              appendChild(x) { this.children.push(x); return x; }, addEventListener(t, f) { this.listener = f; } };
  Object.defineProperty(n, "textContent", {
    get() { return this.own + this.children.map((c) => c.tag ? c.textContent : c.own).join(""); },
    set(v) { this.own = String(v); this.children = []; } });
  return n;
}
global.document = { createElement: node };
const box = node("div");
function $(id) { return node("x"); }
const WS = "/api/ade/workspace";
const pane = { sid: "a".repeat(12), log: box, cards: new Map() };
const posted = [];
async function api(method, path, body) { posted.push([method, path, body]); return {}; }
function note(t) { if (t) posted.push(["note", t]); }
const events = JSON.parse(process.argv[2]);
const pending = new Set(["toolu_2", "toolu_3"]);
for (const ev of events) drawEvent(pane, ev, pending);
const ask = box.children.find((c) => c.className === "con-ask");     // the one with no card of its own
const names = (row) => row.children.filter((c) => c.tag === "button").map((b) => b.textContent);
const buttons = names(ask.children[1]);
const inCard = box.children.find((c) => c.className === "con-tool" && c.textContent.startsWith("Edit"));
const cardButtons = names(inCard.children.find((c) => c.className === "con-ask").children[1]);
ask.children[1].children[0].listener({ stopPropagation() {} });
setTimeout(() => {
  drawEvent(pane, { kind: "answered", id: "toolu_2", decision: "allow" }, pending);
  process.stdout.write(JSON.stringify({
    shown: box.children.map((c) => [c.tag, c.className, c.textContent]), buttons, cardButtons, posted }));
}, 20);
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_each_event_is_drawn_as_text_and_a_question_is_answered_from_its_card(tmp_path):
    script = read("web", "console.js")
    grab = lambda name: re.search(rf"(?:async )?function {name}\(.*?\n\}}\n", script, re.S).group(0)
    one = lambda name: re.search(rf"function {name}\([^\n]*\}}\n", script).group(0)
    consts = re.search(r"const DECIDED = \{.*?\};\n", script, re.S).group(0)
    probe = tmp_path / "draw.js"
    probe.write_text(read("web", "chat-settings.js") + consts + "".join(grab(f) for f in (
        "el", "textBlocks", "copyRow", "message", "button", "answerButtons", "drawEvent")) + one("money")
        + one("estimate") + one("size") + DRAW)
    events = [
        {"seq": 1, "kind": "user", "text": '<img src=x onerror="alert(1)">'},
        {"seq": 2, "kind": "session", "model": "stand-in"},
        {"seq": 3, "kind": "text", "text": "Run this:\n```\nnpm test\n```"},
        {"seq": 4, "kind": "tool", "id": "toolu_1", "name": "Bash", "summary": "ls"},
        {"seq": 5, "kind": "tool_end", "id": "toolu_1", "error": False, "summary": "a b"},
        {"seq": 6, "kind": "ask", "id": "toolu_2", "name": "Bash", "summary": "rm -rf build", "for_session": True},
        {"seq": 6.5, "kind": "tool", "id": "toolu_3", "name": "Edit", "summary": "a.py"},
        {"seq": 6.6, "kind": "ask", "id": "toolu_3", "name": "Edit", "summary": "a.py", "for_session": False},
        {"seq": 7, "kind": "ask", "id": "toolu_9", "name": "Read", "summary": "x", "for_session": False},
        {"seq": 8, "kind": "result", "subtype": "success", "error": False, "cost": 0.0123, "duration_ms": 1500},
        {"seq": 9, "kind": "status", "state": "idle"},
        {"seq": 9.4, "kind": "upload", "name": "photo.png", "path": ".proofpane-uploads/photo.png", "size": 2500000},
        {"seq": 9.5, "kind": "settings", "model": "opus", "effort": "high", "permission_mode": "plan"},
        {"seq": 9.6, "kind": "settings", "model": None, "effort": None, "permission_mode": None},
        {"seq": 10, "kind": "error", "text": "Claude Code could not start"}]
    got = json.loads(subprocess.run([NODE, str(probe), json.dumps(events)], capture_output=True, text=True,
                                    check=True).stdout)
    assert got["shown"] == [
        ["div", "con-msg me", 'You<img src=x onerror="alert(1)">'],
        ["div", "small", "Claude Code (stand-in) is working in this session, in your workspace in the cloud (a machine "
                         "of its own on Fly), not on your computer."],
        ["div", "con-msg claude", "ClaudeRun this:Copynpm test"],
        ["div", "con-tool", "Bash · ls donea b"],
        ["div", "con-ask", "Claude asks to use Bash: rm -rf buildallowed once"],
        ["div", "con-tool", "Edit · a.py runningClaude asks to use it.Allow once Deny"],   # in its call's card
        ["div", "con-ask", "Claude asks to use Read: xno longer waiting"],
        ["div", "small con-result", "Turn done · 1.5 s · est. $0.0123"],
        ["div", "small", "You attached photo.png (2.4 MB), at .proofpane-uploads/photo.png in the session's worktree, "
                         "kept out of its commits."],
        ["div", "small", "Settings: Opus · High effort · Plan first."],
        ["div", "small", "Settings: Claude Code's defaults."],
        ["div", "error", "Claude Code could not start"]]
    assert got["buttons"] == ["Allow once", "Allow for this session", "Deny"]
    assert got["cardButtons"] == ["Allow once", "Deny"]       # no session-wide offer where Claude Code made none
    assert got["posted"] == [["POST", "/api/ade/workspace/sessions/aaaaaaaaaaaa/chat/answer",
                              {"id": "toolu_2", "decision": "allow"}]]


QUEUE = r"""
function node(tag) {
  const n = { tag, children: [], own: "", className: "", hidden: false, type: "", disabled: false, tabIndex: 0,
              append(...xs) { for (const x of xs) this.children.push(typeof x === "string" ? { own: x } : x); },
              appendChild(x) { this.children.push(x); return x; }, setAttribute() {},
              addEventListener(t, f) { this[t] = f; } };
  Object.defineProperty(n, "textContent", {
    get() { return this.own + this.children.map((c) => c.tag ? c.textContent : c.own).join(""); },
    set(v) { this.own = String(v); this.children = []; } });
  return n;
}
global.document = { createElement: node, title: "" };
global.window = { matchMedia: () => ({ matches: false }) };
const els = {};
function $(id) { return els[id] || (els[id] = node(id)); }
const WS = "/api/ade/workspace";
const MAX_PANES = 4;
const posted = [];
async function api(method, path, body) { posted.push([method, path, body]); return {}; }
function note(t) { if (t) posted.push(["note", t]); }
const opened = [];
async function openPane(sid, beside) { opened.push([sid, beside]); }
const con = JSON.parse(process.argv[2]);
renderList();
const queue = $("con-queue").children;
queue[0].children[2].children[0].click({ stopPropagation() {} });      // Allow once, from the queue
queue[0].children[0].children[1].click({ stopPropagation() {} });      // Open
const rows = $("con-list").children;
rows[1].children[2].children[1].click({ stopPropagation() {} });       // Beside, on a row not open yet
setTimeout(() => process.stdout.write(JSON.stringify({
  title: document.title, count: $("con-queue-count").textContent, hidden: $("con-queue-box").hidden,
  queue: queue.map((li) => li.textContent), rows: rows.map((li) => li.className + " | " + li.textContent),
  posted, opened })), 20);
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_every_waiting_question_is_answered_from_the_queue_and_a_row_opens_beside(tmp_path):
    # one needs-you queue across sessions, answerable without opening the pane
    script = read("web", "console.js")
    grab = lambda name: re.search(rf"(?:async )?function {name}\(.*?\n\}}\n", script, re.S).group(0)
    one = lambda name: re.search(rf"function {name}\([^\n]*\}}\n", script).group(0)
    consts = re.search(r"const WORDS = .*?\nconst FIRST = [^\n]*\n", script, re.S).group(0)
    probe = tmp_path / "queue.js"
    probe.write_text(consts + "".join(grab(f) for f in ("el", "stateOf", "stateWord", "sortedSessions", "label",
                                                        "since", "waitingAsks", "button", "answerButtons",
                                                        "renderList")) + one("money") + one("estimate") + one("phone") + QUEUE)
    ask = lambda i, t, for_session: {"id": i, "name": "Bash", "summary": "npm test " + i, "for_session": for_session,
                                     "time": t}
    sessions = [
        {"id": "1" * 12, "program": "chat", "repo": "api", "task": "t", "created_at": 1, "running": True,
         "chat": {"state": "needs_you", "asks": [ask("q2", 20, True)], "cost": 0.5, "last_text": "a", "last_at": 0}},
        {"id": "2" * 12, "program": "chat", "repo": "web", "task": "t", "created_at": 2, "running": True,
         "chat": {"state": "needs_you", "asks": [ask("q1", 10, False)], "cost": 0, "last_text": "b", "last_at": 0}},
        {"id": "3" * 12, "program": "shell", "repo": "web", "task": "t", "created_at": 3, "running": True,
         "chat": None}]
    con = {"sessions": sessions, "panes": [{"sid": "2" * 12}], "focus": 0}
    got = json.loads(subprocess.run([NODE, str(probe), json.dumps(con)], capture_output=True, text=True,
                                    check=True).stdout)
    assert got["title"] == "(2) Console · Proofpane Community" and got["count"] == "(2)" and got["hidden"] is False
    assert got["queue"] == ["Chat in webOpenBash: npm test q1Allow once Deny",
                            "Chat in apiOpenBash: npm test q2Allow once Allow for this session Deny"]
    assert got["rows"][0].startswith("con-row st-needs_you open | Chat in web") and "Beside" not in got["rows"][0]
    assert got["rows"][1].endswith("Beside") and got["rows"][2].endswith("Beside")      # not open: it can go beside
    # a chat's amount is marked as the estimate it is
    assert sum("est. $0.5000" in r for r in got["rows"]) == 1 and not any(re.search(r"(?<!est\. )\$0", r)
                                                                         for r in got["rows"])
    assert got["posted"] == [["POST", "/api/ade/workspace/sessions/222222222222/chat/answer",
                              {"id": "q1", "decision": "allow"}]]
    assert got["opened"] == [["222222222222", False], ["111111111111", True]]
    # with four panes open there is no room beside: no row offers it
    full = dict(con, panes=[{"sid": x * 12} for x in "2456"])
    probe.write_text(probe.read_text().replace("rows[1].children[2].children[1].click({ stopPropagation() {} });", ""))
    got = json.loads(subprocess.run([NODE, str(probe), json.dumps(full)], capture_output=True, text=True,
                                    check=True).stdout)
    assert not any("Beside" in r for r in got["rows"])


def test_the_split_and_the_list_s_width_are_remembered_for_the_account():
    script = read("web", "console.js")
    assert 'localStorage.getItem("pp-console:" + con.user + ":" + name)' in script
    assert 'remember("split-" + which' in script and 'remember("side", w)' in script
    assert 'con.user = (await api("GET", "/api/auth/me")).id;' in script


SPLIT = r"""
const props = {};
const box = { style: { setProperty(k, v) { props[k] = v; } } };
function $(id) { return box; }
const kept = {};
function remember(name, value) { kept[name] = value; }
const out = [setSplit("cols", 0.9, true), setSplit("rows", 0.05, false), setSplit("cols", "nonsense", false)];
process.stdout.write(JSON.stringify({ out, props, kept }));
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's function")
def test_a_line_between_panes_stays_between_a_fifth_and_four_fifths(tmp_path):
    script = read("web", "console.js")
    probe = tmp_path / "split.js"
    probe.write_text(re.search(r"function setSplit\(.*?\n\}\n", script, re.S).group(0) + SPLIT)
    got = json.loads(subprocess.run([NODE, str(probe)], capture_output=True, text=True, check=True).stdout)
    assert got["out"] == [0.8, 0.2, 0.5]
    assert got["props"] == {"--col-a": "1.000fr", "--col-b": "1.000fr", "--row-a": "0.400fr", "--row-b": "1.600fr"}
    assert got["kept"] == {"split-cols": 0.8}


CHAT_GATE = r"""
const button = { disabled: false };
const els = { "con-claude": { hidden: true }, "con-new": { querySelector: (q) => q === "button[type=submit]" ? button : null } };
function $(id) { return els[id]; }
const WS = "/api/ade/workspace";
const con = { hasRepos: false, claude: null };
let answer = null;
function loadCapabilities() {}
async function api(method, path) {
  if (path !== WS + "/claude") throw new Error("asked " + path);
  if (answer === "error") throw new Error("The ADE answered 502.");
  return answer;
}
const answers = { in: { installed: true, signed_in: true }, out: { installed: true, signed_in: false },
                  none: { installed: false, signed_in: false }, error: "error" };
(async () => {
  const out = [];
  for (const [repos, which] of JSON.parse(process.argv[2])) {
    con.hasRepos = repos;
    answer = answers[which];
    await loadClaude();
    out.push([repos, which, con.claude, els["con-claude"].hidden, button.disabled]);
  }
  process.stdout.write(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the console's functions")
def test_a_chat_waits_for_claude_s_sign_in_in_the_console(tmp_path):
    # Claude Code's own sign-in came up in a terminal, where a phone cannot copy. The
    # console's chat form waits for Claude's sign-in on the ADE page, says so with a link there, and knows the app's
    # refusal by its words
    from proofpane_open.ade import workspace_routes
    script, page = read("web", "console.js"), read("web", "console.html")
    assert f'const CLAUDE_FIRST = "{workspace_routes.CLAUDE_FIRST}";' in script
    assert re.search(r'<p id="con-claude" class="error" hidden>Sign in to Claude first, on <a href="/#ade">', page)
    body = script[script.index('api("POST", WS + "/sessions"'):]
    assert "if (e.message === CLAUDE_FIRST) {\n        con.claude = false;\n        gateChat();" in body
    assert '$("con-new-box").addEventListener("toggle", () => { if ($("con-new-box").open) loadClaude(); });' in script
    grab = lambda name: re.search(rf"(?:async )?function {name}\(.*?\n\}}\n", script, re.S).group(0)
    probe = tmp_path / "gate.js"
    probe.write_text(grab("loadClaude") + grab("gateChat") + CHAT_GATE)
    cases = [[True, "in"], [True, "out"], [True, "none"], [True, "error"], [False, "in"], [False, "out"]]
    got = json.loads(subprocess.run([NODE, str(probe), json.dumps(cases)], capture_output=True, text=True,
                                    check=True).stdout)
    assert got == [[True, "in", True, True, False], [True, "out", False, False, True],
                   [True, "none", False, False, True], [True, "error", None, True, False],
                   [False, "in", True, True, True], [False, "out", False, False, True]]


def test_the_pages_say_where_a_session_runs_and_that_its_amounts_are_estimates():
    # how the charge at the bottom is worked out, and that a session runs in the workspace, not in the person's own
    # editor
    script, page, ade = read("web", "console.js"), read("web", "console.html"), read("web", "index.html")
    text, ade = " ".join(page.split()), " ".join(ade.split())
    assert "Every session runs in your workspace in the cloud, a machine of its own on Fly" in text
    assert "not on your computer, and not in the Claude app. Closing this page leaves it running." in text
    assert ("are Claude Code's own estimates at API prices, not a bill: signed in with a Claude plan, you pay the plan, "
            "not each token.") in text
    assert "Your own workspace, in the cloud: a machine of its own on Fly, not your computer" in ade
    assert 'pane.meta.title = c && c.cost ? ESTIMATE : "";' in script
    # every amount shown goes through estimate(): money() is used only by it
    assert script.count("money(") == 2 and 'function estimate(x) { return "est. " + money(x); }' in script


SETTINGS = r"""
function node(tag) {
  const n = { tag, children: [], own: "", className: "", value: "", listeners: {},
              appendChild(x) { this.children.push(x); return x; },
              addEventListener(t, f) { this.listeners[t] = f; } };
  Object.defineProperty(n, "textContent", {
    get() { return this.own + this.children.map((c) => c.textContent).join(""); },
    set(v) { this.own = String(v); this.children = []; } });
  Object.defineProperty(n, "options", { get() { return this.children; } });
  return n;
}
global.document = { createElement: node };
const values = () => CHAT_SETTINGS.lists().map(([, , l]) => l.map((x) => x[0]));
const f = CHAT_SETTINGS.fields({});
const note = () => f.box.children[3].textContent;
const out = { first: f.read(), firstNote: note(), lists: values(),
              labels: f.box.children.slice(0, 3).map((l) => l.own.trim()) };
f.show({ model: "opus", effort: "max", permission_mode: "bypassPermissions" });
out.shown = f.read();
out.shownNote = note();
f.selects.permission_mode.value = "plan";
f.selects.permission_mode.listeners.change();
out.changedNote = note();
f.selects.effort.value = "ultracode";
f.selects.effort.listeners.change();
out.ultracodeNote = note();
f.show({ model: "claude-opus-4-5" });
out.odd = [f.read(), f.selects.model.options.length];
out.words = [CHAT_SETTINGS.words({}), CHAT_SETTINGS.words(null),
             CHAT_SETTINGS.words({ model: "fable", effort: "xhigh", permission_mode: "auto" }),
             CHAT_SETTINGS.words({ model: "claude-opus-4-5", effort: "ultracode" })];
out.notes = CHAT_SETTINGS.NOTES;
// a newer claude: one more level, one mode fewer; a chat that had the mode now gone keeps it, shown as itself
f.show({ permission_mode: "auto" });
const base = { model_aliases: ["fable", "opus", "sonnet", "haiku"], efforts: ["low", "ultra"],
               permission_modes: ["default", "plan"] };
out.firstUse = CHAT_SETTINGS.use(Object.assign({ claude_version: "2.1.274" }, base));
out.bad = [CHAT_SETTINGS.use(null), CHAT_SETTINGS.use({ efforts: [] }), CHAT_SETTINGS.use({ efforts: "low" })];
out.update = CHAT_SETTINGS.use(Object.assign({ claude_version: "2.2.0" }, base));
out.after = { lists: values(), kept: f.read(), options: f.selects.permission_mode.options.map((o) => o.value),
              version: CHAT_SETTINGS.version() };
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_a_chat_s_settings_are_claude_code_s_own_and_say_what_the_modes_mean(tmp_path):
    # the model, the effort and the permission mode, as the Claude app has them
    from proofpane_workspace import workspace
    probe = tmp_path / "settings.js"
    probe.write_text(read("web", "chat-settings.js") + SETTINGS)
    got = json.loads(subprocess.run([NODE, str(probe)], capture_output=True, text=True, check=True).stdout)
    models, efforts, modes = got["lists"]
    # the agent's own lists, with the empty choice meaning Claude Code's default ("default" for the mode)
    assert efforts == ["", *workspace.CHAT_EFFORTS] and modes == ["", *(m for m in workspace.CHAT_MODES if m != "default")]
    assert models[0] == "" and all(workspace._MODEL.fullmatch(m) for m in models[1:])
    assert got["labels"] == ["Model", "Effort", "Permissions"]
    assert got["first"] == {"model": "", "effort": "", "permission_mode": ""}
    assert got["firstNote"] == "Claude asks you before each action that needs your permission."
    assert got["shown"] == {"model": "opus", "effort": "max", "permission_mode": "bypassPermissions"}
    assert got["shownNote"] == ("Nothing asks you in this chat: Claude acts on its own. The workspace's gates still "
                                "refuse what they refuse.")
    assert got["changedNote"] == "Claude plans first and changes nothing until you accept its plan."
    assert got["ultracodeNote"] == ("Claude plans first and changes nothing until you accept its plan. Extra high "
                                    "effort, and Claude may fan out sub-agents on its own.")
    assert got["odd"] == [{"model": "claude-opus-4-5", "effort": "", "permission_mode": ""}, len(models) + 1]
    assert got["words"] == ["Claude Code's defaults", "Claude Code's defaults", "Fable · Extra high effort · Auto",
                            "claude-opus-4-5 · Ultracode effort"]
    assert set(got["notes"]) == set(modes)
    # the installed claude's own lists: taken as they are, a value no longer offered
    # kept and shown as itself, the version before said once it changes, anything malformed left aside
    assert got["firstUse"] is None and got["bad"] == [None, None, None] and got["update"] == "2.1.274"
    assert got["after"] == {"lists": [["", "fable", "opus", "sonnet", "haiku"], ["", "low", "ultra"], ["", "plan"]],
                            "kept": {"model": "", "effort": "", "permission_mode": "auto"},
                            "options": ["", "plan", "auto"], "version": "2.2.0"}
    # the console and the ADE page's form carry it, the form only for a chat
    script, page, app, ade = read("web", "console.js"), read("web", "console.html"), read("web", "app.js"), \
        read("web", "index.html")
    assert "chat_settings: newChat.read()" in script and 'id="con-new-settings"' in page
    assert "chat_settings: chat ? wsChatSettings.read() : undefined" in app and '<div id="ws-new-settings"></div>' in ade
    assert 'function showChatSettings() { $("ws-new-settings").hidden = $("ws-new").program.value !== "chat"; }' in app
    assert re.findall(r'<script src="([^"]+)"></script>', ade) == ["theme.js", "chat-settings.js", "voice.js", "app.js"]


@pytest.mark.skipif(NODE is None, reason="needs node to run the console's functions")
def test_an_attached_file_s_path_goes_into_the_message(tmp_path):
    # upload: the file goes into the chat's worktree, and its path into the message, for Claude
    script = read("web", "console.js")
    one = lambda name: re.search(rf"function {name}\([^\n]*\}}\n", script).group(0)
    grab = lambda name: re.search(rf"function {name}\(.*?\n\}}\n", script, re.S).group(0)
    probe = tmp_path / "attach.js"
    probe.write_text(grab("attached") + one("size") + """
process.stdout.write(JSON.stringify([attached("", "p/a.png"), attached("look at this", "p/a.png"),
  attached("line\\n", "p/b.txt"), size(10), size(500 * 1024), size(3 * 1048576)]));
""")
    got = json.loads(subprocess.run([NODE, str(probe)], capture_output=True, text=True, check=True).stdout)
    assert got == ["Attached: p/a.png\n", "look at this\nAttached: p/a.png\n", "line\nAttached: p/b.txt\n", "1 KB",
                   "500 KB", "3.0 MB"]
    # a label around the input opens the phone's own picker; the bytes go to the upload route, signed, with its name
    assert 'pane.attach = el("label", null, "button-link con-attach");' in script
    assert "pane.attach.append(pane.attachWord, pane.file);" in script and 'pane.file.type = "file";' in script
    assert '"/upload?name=" + encodeURIComponent(file.name)' in script
    assert 'Authorization: "Bearer " + token }, body: file' in script
    assert "if (file.size > MAX_UPLOAD) throw new Error(\"A file is at most 10 MB.\");" in script


MCP_PROBE = r"""
function node(tag) {
  const n = { tag, children: [], own: "", className: "", listeners: {}, hidden: false, disabled: false, type: "",
              appendChild(x) { this.children.push(x); return x; },
              append(...xs) { for (const x of xs) this.children.push(typeof x === "string" ? { textContent: x } : x); },
              addEventListener(t, f) { this.listeners[t] = f; } };
  Object.defineProperty(n, "textContent", {
    get() { return this.own + this.children.map((c) => c.textContent).join(""); },
    set(v) { this.own = String(v); this.children = []; } });
  return n;
}
global.document = { createElement: node };
const calls = [];
const notes = [];
function note(t) { notes.push(t); }
async function api(method, path, body) { calls.push([method, path, body]); return { servers: [], live: [], running: true }; }
const flat = (n, out = []) => { out.push(n); for (const c of n.children || []) flat(c, out); return out; };
const buttons = (n) => flat(n).filter((x) => x.tag === "button").map((b) => b.textContent);
const out = {};
out.where = [MCP_WHERE_OF({ files: ["this repository"] }), MCP_WHERE_OF({ files: ["this repository", "/home/ws/.mcp.json"] }),
             MCP_WHERE_OF({ scope: "user" }), MCP_WHERE_OF({ scope: "local" }), MCP_WHERE_OF({ scope: "claudeai" }),
             MCP_WHERE_OF({ scope: "project" }), MCP_WHERE_OF({ scope: "odd" })];
out.runs = [mcpRuns({ kind: "command", line: "python3 s.py", names: ["TOKEN", "KEY"] }),
            mcpRuns({ kind: "command", line: "npx x", names: [] }),
            mcpRuns({ kind: "http", line: "https://e.org/mcp", names: ["Authorization"] }),
            mcpRuns({ kind: "sse", line: "https://e.org/sse" }), mcpRuns({ kind: "sdk", line: "" })];
out.state = [mcpState({ status: "connected", tools: 0 }), mcpState({ status: "connected", tools: 1 }),
             mcpState({ status: "connected", tools: 3 }), mcpState({ status: "failed", error: "spawn ENOENT" }),
             mcpState({ status: "failed" }), mcpState({ status: "needs-auth" }), mcpState({ status: "pending" }),
             mcpState({ status: "disabled" }), mcpState({ status: "odd" })];
out.line = [mcpLine({ strict: "/w/.mcp.json is not JSON", servers: [], held: ["a"] }),
            mcpLine({ servers: [{ name: "web", status: "connected" }, { name: "gh", status: "failed" }], held: ["echo"],
                      stray: ["sneaky"] }),
            mcpLine({ servers: [], held: [], stray: [] })];
out.change = ["approved", "stopped", "added", "removed"].map((c) => mcpChange({ change: c, name: "echo",
                                                                               line: c === "approved" ? "python3 s.py" : "" }));
const pane = { sid: "abc", mcpBody: node("div"), mcpAdd: node("form") };
drawMcp(pane, { strict: null, running: true, servers: [
    { name: "echo", kind: "command", line: "python3 s.py", names: [], files: ["this repository"], approved: false,
      conflict: false, fingerprint: "f1" },
    { name: "on", kind: "command", line: "node on.js", names: [], files: ["this repository"], approved: true,
      conflict: false, fingerprint: "f2" },
    { name: "later", kind: "command", line: "x", names: [], files: ["this repository"], approved: true, conflict: false,
      fingerprint: "f3" },
    { name: "twice", kind: "command", line: "y", names: [], files: ["this repository", "/w/.mcp.json"], approved: false,
      conflict: true, fingerprint: null }],
  live: [{ name: "on", scope: "project", status: "connected", tools: 1, kind: "command", line: "node on.js", names: [] },
         { name: "web", scope: "user", status: "connected", tools: 2, kind: "http", line: "https://e.org", names: [] },
         { name: "Drive", scope: "claudeai", status: "needs-auth", tools: 0, kind: "claudeai-proxy", line: "", names: [] },
         { name: "gone", scope: "local", status: "disabled", tools: 0, kind: "command", line: "x", names: [],
           removed: true }] });
const items = flat(pane.mcpBody).filter((x) => x.tag === "li");
out.items = items.map((li) => [li.className, li.children[0].textContent, buttons(li), li.textContent]);
out.form = pane.mcpBody.children.includes(pane.mcpAdd);
const approve = flat(items[0]).find((x) => x.tag === "button");
approve.listeners.click({ stopPropagation() {} });
setTimeout(() => {
  out.calls = calls;
  const strict = { sid: "s", mcpBody: node("div"), mcpAdd: node("form") };
  drawMcp(strict, { strict: "/w/.mcp.json is not JSON", running: false, servers: [], live: null });
  out.strict = strict.mcpBody.children.map((c) => [c.className || "", c.textContent]);
  process.stdout.write(JSON.stringify(out));
}, 20);
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the console's functions")
def test_a_chat_s_mcp_servers_are_shown_and_a_repository_s_approved_as_shown(tmp_path):
    # the repository's servers wait for the owner's approval, bound to how the page
    # showed them; the workspace's own and a Claude account's connectors show with their state
    script = read("web", "console.js")
    grab = lambda name: re.search(rf"(?:async )?function {name}\(.*?\n\}}\n", script, re.S).group(0)
    one = lambda name: re.search(rf"function {name}\([^\n]*\}}\n", script).group(0)
    consts = re.search(r"const MCP_WHERE = .*?\n(?=\nfunction mcpPath)", script, re.S).group(0)
    probe = tmp_path / "mcp.js"
    probe.write_text("const WS = '/api/ade/workspace';\n" + consts + grab("el") + grab("button") + one("mcpPath") +
                     "".join(grab(f) for f in ("mcpWhere", "mcpRuns", "mcpState", "mcpLine", "mcpChange", "loadMcp",
                                               "mcpAct", "mcpItem", "drawMcp")) +
                     MCP_PROBE.replace("MCP_WHERE_OF", "mcpWhere"))
    got = json.loads(subprocess.run([NODE, str(probe)], capture_output=True, text=True, check=True).stdout)
    assert got["where"] == ["this repository's .mcp.json", "this repository's .mcp.json, /home/ws/.mcp.json",
                            "added for every repository in the workspace", "added for this repository",
                            "a connector of your Claude account (claude.ai)", "a .mcp.json", "odd"]
    assert got["runs"] == ["Runs python3 s.py (with TOKEN, KEY set)", "Runs npx x",
                           "Connects to https://e.org/mcp (with its Authorization header)",
                           "Connects to https://e.org/sse", ""]
    assert got["state"] == ["On", "On, 1 tool", "On, 3 tools", "Failed: spawn ENOENT", "Failed",
                            "Needs its own sign-in, which this page cannot give yet (claude in a terminal can: /mcp).",
                            "Starting...", "Off", "odd"]
    assert got["line"] == ["No MCP server runs in this chat: /w/.mcp.json is not JSON. Fix or remove that file.",
                           "MCP servers: web (on), gh (failed). Held back until you approve them under Connectors: "
                           "echo. Turned off, as no .mcp.json here declares them: sneaky.", ""]
    assert got["change"] == ["You approved echo (python3 s.py): it starts with the chat's next message.",
                             "You stopped using echo.", "You added echo: it starts with the chat's next message.",
                             "You removed echo."]
    # the repository's: one to approve (marked), one on, one approved and not started yet, one defined twice; then the
    # workspace's own (removable here) and the Claude account's connector (set on claude.ai)
    items = got["items"]
    assert [(c, head, b) for c, head, b, _ in items] == [
        ("waiting", "echo this repository's .mcp.json", ["Approve"]),
        ("", "on this repository's .mcp.json", ["Stop using"]),
        ("", "later this repository's .mcp.json", ["Stop using"]),
        ("", "twice this repository's .mcp.json, /w/.mcp.json", []),
        ("", "web added for every repository in the workspace", ["Remove"]),
        ("", "Drive a connector of your Claude account (claude.ai)", []),
        ("", "gone added for this repository", [])]
    assert "Removed: it goes with the chat's next message." in items[6][3]
    assert "only once you approve it" in items[0][3] and "On, 1 tool" in items[1][3]
    assert "Approved: it starts with the chat's next message." in items[2][3]
    assert "define it differently" in items[3][3] and "Needs its own sign-in" in items[5][3]
    assert got["form"] is True
    assert got["calls"] == [["POST", "/api/ade/workspace/sessions/abc/chat/mcp",
                             {"action": "approve", "name": "echo", "fingerprint": "f1"}]]
    assert got["strict"][0] == ["error", "No MCP server runs in this chat: /w/.mcp.json is not JSON. Fix or remove "
                                         "that file."]
    assert [t for _, t in got["strict"][1:3]] == [
        "No MCP server yet; claude says what it has when it starts, with the chat's next message.",
        "Claude is not running in this chat now: the list shows what it had."]
    # the panel: a chat's only, read when it opens and when claude starts; the form keeps nothing once it is sent
    assert 'pane.mcp.hidden = s.program !== "chat";' in script
    assert 'pane.mcp.addEventListener("toggle", () => { if (pane.mcp.open) loadMcp(pane); });' in script
    assert 'if (ev.kind === "mcp" && p.mcp.open) loadMcp(p);' in script


ADD_PROBE = r"""
function node(tag) {
  const n = { tag, children: [], own: "", className: "", listeners: {}, attrs: {}, value: "", resets: 0,
              appendChild(x) { this.children.push(x); x.parentNode = this; return x; },
              append(...xs) { for (const x of xs) this.appendChild(x); },
              setAttribute(k, v) { this.attrs[k] = v; },
              addEventListener(t, f) { this.listeners[t] = f; },
              reset() { this.resets++; for (const x of flat(this)) if ("value" in x && x.tag !== "option") x.value = ""; } };
  Object.defineProperty(n, "textContent", {
    get() { return this.own + this.children.map((c) => c.textContent).join(""); },
    set(v) { this.own = String(v); this.children = []; } });
  // a label's own words: its first child, a text node
  Object.defineProperty(n, "firstChild", { get() { const p = this;
    return { get textContent() { return p.own; }, set textContent(v) { p.own = String(v); } }; } });
  return n;
}
function flat(n, out = []) { out.push(n); for (const c of n.children || []) flat(c, out); return out; }
global.document = { createElement: node };
const calls = [], notes = [];
let answer = async () => ({ servers: [], live: [], running: true });
function note(t) { notes.push(t); }
async function api(method, path, body) { calls.push([method, path, body]); return answer(); }
const pane = { sid: "abc", mcpBody: node("div") };
pane.mcpAdd = mcpAddForm(pane);
const box = pane.mcpAdd;
const f = flat(box).find((x) => x.tag === "form");
const by = (name) => flat(f).find((x) => x.name === name);
const label = (name) => by(name).parentNode.own.trim();
const out = { labels: ["name", "kind", "what", "lines", "scope"].map(label), placeholder: by("what").placeholder,
              folded: [box.tag, box.children[0].textContent] };
box.open = true;
(async () => {
  by("name").value = " gh ";
  by("what").value = "npx -y server-github";
  by("lines").value = "GITHUB_TOKEN=abc";
  by("scope").value = "repository";
  by("kind").value = "command";
  await f.listeners.submit({ preventDefault() {} });
  out.sent = calls.slice();
  out.cleared = [f.resets, by("what").value, by("lines").value, notes.slice()];
  out.drawn = [pane.mcpBody.children.includes(box), box.open];
  box.open = true;
  by("kind").value = "url";
  by("kind").listeners.change();
  out.urlWords = [label("what"), label("lines"), by("what").placeholder];
  by("name").value = "web";
  by("what").value = " https://e.org/mcp ";
  by("lines").value = "Authorization: Bearer xyz";
  by("scope").value = "workspace";
  answer = async () => { throw new Error("MCP server web already exists in user config"); };
  await f.listeners.submit({ preventDefault() {} });
  out.refused = [calls[1], f.resets, by("lines").value, notes.slice(-1), box.open];
  by("kind").value = "sse";
  by("kind").listeners.change();
  out.sseWords = label("what");
  by("name").value = "old";
  by("what").value = "https://e.org/sse";
  by("lines").value = "";
  await f.listeners.submit({ preventDefault() {} });
  out.sse = calls[2][2];
  process.stdout.write(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the console's functions")
def test_a_server_is_added_from_the_form_and_what_was_typed_is_not_kept(tmp_path):
    # the form sends a command line or an address, for the repository or the workspace; once added, what was typed
    # (an environment's values, a header's) is cleared from the page; a refusal leaves it to be corrected
    script = read("web", "console.js")
    grab = lambda name: re.search(rf"(?:async )?function {name}\(.*?\n\}}\n", script, re.S).group(0)
    one = lambda name: re.search(rf"function {name}\([^\n]*\}}\n", script).group(0)
    consts = re.search(r"const MCP_WHERE = .*?\n(?=\nfunction mcpPath)", script, re.S).group(0)
    probe = tmp_path / "add.js"
    probe.write_text("const WS = '/api/ade/workspace';\n" + consts + grab("el") + grab("button") + one("mcpPath") +
                     "".join(grab(f) for f in ("mcpWhere", "mcpRuns", "mcpState", "mcpItem", "mcpAct", "drawMcp",
                                               "mcpAddForm")) + ADD_PROBE)
    got = json.loads(subprocess.run([NODE, str(probe)], capture_output=True, text=True, check=True).stdout)
    assert got["labels"] == ["Name", "It is", "Command line", "Environment, one NAME=value a line (optional)", "For"]
    assert got["placeholder"] == "npx -y @modelcontextprotocol/server-github"
    assert got["sent"] == [["POST", "/api/ade/workspace/sessions/abc/chat/mcp", {"action": "add", "spec": {
        "name": "gh", "scope": "repository", "lines": "GITHUB_TOKEN=abc", "command": "npx -y server-github"}}]]
    assert got["folded"] == ["details", "Add a server"]
    assert got["cleared"] == [1, "", "", [""]] and got["drawn"] == [True, False]     # added: folded again
    assert got["urlWords"] == ["Address", "Headers, one Name: value a line (optional)", "https://example.com/mcp"]
    assert got["refused"] == [["POST", "/api/ade/workspace/sessions/abc/chat/mcp", {"action": "add", "spec": {
        "name": "web", "scope": "workspace", "lines": "Authorization: Bearer xyz", "url": "https://e.org/mcp"}}],
        1, "Authorization: Bearer xyz", ["MCP server web already exists in user config"], True]
    assert got["sseWords"] == "Address" and got["sse"] == {"action": "add", "spec": {
        "name": "old", "scope": "workspace", "lines": "", "url": "https://e.org/sse", "transport": "sse"}}
