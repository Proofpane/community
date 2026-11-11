"""The page's Linked sessions panel: no event text is parsed as HTML (the page script
has no HTML sink at all, test_diff_and_boundary), the only link made from what a session posts is claudeLink's on the
link's session_ref (test_ade_guards), and an event is shown as one line of literal text, run here through node."""

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone

import pytest

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
NODE = shutil.which("node")


def read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_every_element_the_panel_uses_is_on_the_page():
    script, page = read("web", "app.js"), read("web", "index.html")
    used = set(re.findall(r'\$\("(ade-links?[-\w]*)"\)', script))
    assert {"ade-links", "ade-links-empty", "ade-link-others", "ade-link-other-list"} <= used
    for element in used:
        assert page.count(f'id="{element}"') == 1, element


def test_tickets_re_pairs_and_revokes_are_the_app_s_which_asks_for_the_person_s_own_word():
    # each takes a confirmation now (link.create_ticket, link.revoke): the app asks for it, so this page links there
    script, page = read("web", "app.js"), read("web", "index.html")
    assert '<a href="/app/#channel">' in page
    for gone in ("ade-link-ticket", "ade-link-new", "ade-link-setup", "/api/ade/link/tickets", "newTicket"):
        assert gone not in script and gone not in page, gone


def test_a_held_command_is_approved_in_the_app_which_asks_for_the_person_s_own_word():
    # approving takes a confirmation made for the request now (ade/approvals.py): this page cannot ask for one, so
    # it offers no Approve, which the server would refuse for every account but one with nothing to confirm with (the
    # desktop server's own, which the app serves too); it links to the app's Approvals, and keeps Deny
    script = read("web", "app.js")
    assert '"/app/#approvals"' in script and "Approve it in the app" in script
    assert "Approve once" not in script and "answer(true)" not in script
    assert 'buttonFor("Deny", run(() => answer(false)))' in script


TOKENS = r"""
function node(tag) {
  const n = { tag, children: [], own: "", className: "", type: "", dataset: {},
              appendChild(x) { this.children.push(x); return x; }, addEventListener(type, f) { this.listener = f; } };
  Object.defineProperty(n, "textContent", {
    get() { return this.own + this.children.map((c) => c.textContent).join(""); },
    set(v) { this.own = String(v); this.children = []; } });
  return n;
}
const body = node("tbody");
body.insertRow = () => body.appendChild(node("tr"));
global.document = { createElement: node };
const $ = (id) => ({ "ade-hook-list": { tBodies: [body] } })[id];
const asked = [];
async function api(method, path) { asked.push(method + " " + path); return { tokens: JSON.parse(process.argv[2]) }; }
function fail(e) { throw e; }
loadHookTokens().then(() => process.stdout.write(JSON.stringify({ asked, rows: body.children.map((row) =>
  row.children.map((td) => [td.own, ...td.children.map((c) => c.tag + "." + c.className + ": " + c.textContent)])) })));
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_a_hook_token_says_when_its_hook_last_called_in_or_that_it_never_has(tmp_path):
    # a hook that is not set up, or cannot start, asks nothing, and its session's gated commands can then run unasked:
    # the token list says which tokens no hook has reached the ADE with, and, while one is live, what that can mean
    script = read("web", "app.js")
    grab = lambda name: re.search(rf"function {name}\(.*?\n\}}\n", script, re.S).group(0)
    probe = tmp_path / "tokens.js"
    probe.write_text("".join(grab(f) for f in ("el", "cell", "buttonFor", "armed"))
                     + re.search(r"function unix\([^\n]*\}\n", script).group(0)
                     + re.search(r"const HOOK_NOT_CALLED_IN = .*?;\n", script, re.S).group(0)
                     + re.search(r"async function loadHookTokens\(\) \{\n.*?\n\}\n", script, re.S).group(0) + TOKENS)
    t0 = 1_790_000_000
    tokens = [{"id": "a", "name": "Mac", "created_at": t0, "last_used_at": None, "revoked_at": None},
              {"id": "b", "name": "Linux", "created_at": t0, "last_used_at": t0 + 600, "revoked_at": None},
              {"id": "c", "name": "Old", "created_at": t0, "last_used_at": None, "revoked_at": t0 + 1200},
              {"id": "d", "name": "Gone", "created_at": t0, "last_used_at": t0 + 600, "revoked_at": t0 + 1200}]
    got = json.loads(subprocess.run([NODE, str(probe), json.dumps(tokens)], capture_output=True, text=True,
                                    check=True).stdout)
    utc = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    line = ("div.small: Its hook has not reached the ADE yet. A hook asks here only at a gated command; one that is "
            "not set up, or cannot start, can let Claude Code run those commands without asking you.")
    revoke = "button.muted-button: Revoke"
    assert got["asked"] == ["GET /api/ade/hook-tokens"]
    assert got["rows"] == [
        [["Mac"], [utc(t0)], ["never called in", line], ["", revoke]],
        [["Linux"], [utc(t0)], [utc(t0 + 600)], ["", revoke]],
        [[f"Old (revoked {utc(t0 + 1200)})"], [utc(t0)], ["never called in"], [""]],
        [[f"Gone (revoked {utc(t0 + 1200)})"], [utc(t0)], [utc(t0 + 600)], [""]]]


def test_the_panel_reads_only_the_owner_s_routes():
    script = read("web", "app.js")
    start = script.index("// ---- the session channel (ade/link.py)")
    panel = script[start:script.index("async function loadHookTokens()", start)]
    assert sorted(set(re.findall(r'"/api/ade/link/[a-z]+', panel))) == [
        '"/api/ade/link/questions', '"/api/ade/link/sessions']          # a timeline is the app's page's


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's function")
def test_an_event_is_one_line_of_literal_text(tmp_path):
    script = read("web", "app.js")
    source = (re.search(r"function utc\(t\) \{[^\n]*\}\n", script).group(0)
              + re.search(r"const HIDDEN_ALL = [^\n]*\n", script).group(0)
              + re.search(r"function visible\(text[^)]*\) \{\n.*?\n\}\n", script, re.S).group(0)
              + re.search(r"function linkLine\(ev\) \{\n.*?\n\}\n", script, re.S).group(0))
    probe = tmp_path / "probe.js"
    probe.write_text(source + "process.stdout.write(JSON.stringify(linkLine(JSON.parse(process.argv[2]))));\n")
    event = {"seq": 7, "time": "2026-09-29T01:02:03Z", "kind": "question", "from": "cloud", "to": "all",
             "text": '<img src=x onerror="alert(1)">\nsecond \\ line\r'}
    run = subprocess.run([NODE, str(probe), json.dumps(event)], capture_output=True, text=True, check=True)
    assert json.loads(run.stdout) == ('[7] 2026-09-29 01:02 UTC question cloud>all: <img src=x onerror="alert(1)">'
                                      '\\nsecond \\\\ line\\r')
    # a bidi control from a session's words is written as its code point, as the app's page writes it; a mirrored
    # note says so
    event = {"seq": 8, "time": "2026-09-29T01:02:03Z", "kind": "note", "from": "mac", "to": "owner",
             "mirror": "owner", "text": "ship \u202eit"}
    run = subprocess.run([NODE, str(probe), json.dumps(event)], capture_output=True, text=True, check=True)
    assert json.loads(run.stdout) == "[8] 2026-09-29 01:02 UTC note (mirrored: owner) mac>owner: ship [U+202E]it"
    # a text sealed by end-to-end encryption opens only in the app: this page says so, never its letters
    event = {"seq": 9, "time": "2026-10-08T01:02:03Z", "kind": "question", "from": "mac", "to": "owner",
             "text": "e2e1." + "A" * 120}
    run = subprocess.run([NODE, str(probe), json.dumps(event)], capture_output=True, text=True, check=True)
    assert json.loads(run.stdout) == "[9] 2026-10-08 01:02 UTC question mac>owner: Encrypted: read it in the app"
    items = script[script.index("ade.linkItems ="):script.index("attention();", script.index("ade.linkItems ="))]
    assert "q.text" not in items and "l.name" not in items      # the title bar's items carry no words: they are counted


def test_the_commands_the_page_shows_name_the_address_the_ade_gives():
    # the pair command and the hook's address come from the server's answer, never from where the page is open
    script = read("web", "app.js")
    assert "location.origin" not in script
    assert '"#   \\"PROOFPANE_ADE_URL\\": " + JSON.stringify(out.url)' in script   # the block's env line
    # the line that links a session is the app's now (a ticket takes the person's own word), setup:
    # built from the server's answer too, and runs the client where SETUP_A.md installs it, isolated as the manual's
    # own settings run it
    channel = read("ui", "src", "components", "Channel.tsx")
    assert "location.origin" not in channel
    assert "`${CLIENT} setup --url ${out.url} --ticket ${out.ticket}" in channel
    # CLIENT is shellForms.ts's, which Channel, Account and History take it from
    forms = read("ui", "src", "shellForms.ts")
    assert 'export const CLIENT = `python3 -I "$HOME/.proofpane-ade/ade_link.py"`;' in forms
    assert re.search(r'^import \{[^}]*\bCLIENT\b[^}]*\} from "\.\./shellForms";$', channel, re.M)
    assert "const CLIENT" not in channel
    # the link block's lines start the client in UTF-8 mode too
    assert ' -I -X utf8 \\"/absolute/path/to/home/.proofpane-ade/ade_link.py\\" ' in read("ade_link.settings.json")


HARNESS = r"""
const copied = [];
function node(tag) {
  return { tag, children: [], textContent: "", className: "", type: "",
           append(...xs) { this.children.push(...xs); }, appendChild(x) { this.children.push(x); return x; },
           addEventListener(type, f) { this.listener = f; } };
}
const pre = { textContent: process.argv[2],
              parentNode: { inserted: [], insertBefore(n, ref) { this.inserted.push([n, ref]); } } };
global.document = { querySelectorAll: (sel) => sel === "pre.copyable" ? [pre] : [], createElement: node };
// node has a navigator of its own that assignment cannot replace
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: { writeText: async (t) => {
  if (process.argv[3] === "refuse") throw new Error("NotAllowedError");
  copied.push(t);
} } } });
function fail(e) { throw e; }
copyButtons();
const [row, before] = pre.parentNode.inserted[0];
const [button, , note] = row.children;
button.listener();
setTimeout(() => process.stdout.write(JSON.stringify({
  label: button.textContent, beside: before === pre, copied, note: note.textContent })), 50);
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
@pytest.mark.parametrize("clipboard", ["allow", "refuse"])
def test_a_command_block_has_a_copy_button_that_copies_its_text(tmp_path, clipboard):
    # on a phone, the pair command and the hook token's block were copied by hand before there was a button
    script, page = read("web", "app.js"), read("web", "index.html")
    for block in ("ade-hook-setup",):
        assert f'<pre id="{block}" class="copyable"></pre>' in page
    grab = lambda name: re.search(rf"function {name}\(.*?\n\}}\n", script, re.S).group(0)
    probe = tmp_path / "copy.js"
    probe.write_text(grab("el") + grab("buttonFor") + grab("copyButtons") + HARNESS)
    command = "python3 open_edition/ade_link.py pair --url https://open.example.test --ticket PP-ABCDEFGH --name mac"
    run = subprocess.run([NODE, str(probe), command, clipboard], capture_output=True, text=True, check=True)
    got = json.loads(run.stdout)
    assert got["beside"] is True
    if clipboard == "allow":
        assert got["copied"] == [command] and got["label"] == "Copied" and got["note"] == ""
    else:
        assert got["copied"] == [] and got["label"] == "Copy" and "select the text" in got["note"]


CARD = r"""
function node(tag) {
  const n = { tag, children: [], own: "", className: "", hidden: false,
              append(...xs) { for (const x of xs) this.children.push(typeof x === "string" ? { own: x } : x); },
              appendChild(x) { this.children.push(x); return x; },
              addEventListener(type, f) { this.listener = f; } };
  Object.defineProperty(n, "textContent", {
    get() { return this.own + this.children.map((c) => c.tag ? c.textContent : c.own).join(""); },
    set(v) { this.own = String(v); this.children = []; } });
  return n;
}
global.document = { createElement: node, createTextNode: (t) => ({ own: t }) };
function fail(e) { throw e; }
const card = linkCard(JSON.parse(process.argv[2]), []);
const lines = card.children.filter((c) => c.tag === "div").map((d) => d.textContent);
const open = card.children.find((c) => c.className === "row-buttons").children[0];
process.stdout.write(JSON.stringify({ lines, tag: open.tag, text: open.textContent, href: open.href }));
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
@pytest.mark.parametrize("events", [0, 1, 4])
def test_a_card_shows_its_newest_event_and_sends_its_timeline_to_the_app(tmp_path, events):
    # on a phone, a session's activity is seen without a tap
    script = read("web", "app.js")
    grab = lambda name: re.search(rf"function {name}\(.*?\n\}}\n", script, re.S).group(0)
    one = lambda name: re.search(rf"function {name}\([^\n]*\}}\n", script).group(0)
    probe = tmp_path / "card.js"
    probe.write_text("".join(grab(f) for f in ("el", "buttonFor", "claudeLink", "linkLine", "linkCard"))
                     + "".join(one(f) for f in ("utc", "unix", "shortLine"))
                     + re.search(r"const HIDDEN_ALL = [^\n]*\n", script).group(0) + grab("visible") + CARD)
    newest = {"seq": 9, "time": "2026-09-28T20:01:02Z", "kind": "step", "from": "mac", "to": "all",
              "text": "built the image\n" + "x" * 400}
    link = {"id": "L1", "name": "mac", "kind": "local", "silent": False, "last_heartbeat_at": 1790625600,
            "checkpoint": {"doing": "the sign-in fix"}, "events": events, "last_event": newest if events else None}
    got = json.loads(subprocess.run([NODE, str(probe), json.dumps(link)], capture_output=True, text=True,
                                    check=True).stdout)
    latest = [x for x in got["lines"] if x.startswith("latest: ")]
    if events:
        whole = "[9] 2026-09-28 20:01 UTC step mac>all: built the image\\n" + "x" * 400     # one line, cut to 280
        assert latest == ["latest: " + whole[:280] + " ..."]
    else:
        assert latest == []
    word = "event" if events == 1 else "events"
    assert (got["tag"], got["href"]) == ("a", "/app/#channel")
    assert got["text"] == f"In the app: what it did · {events} {word}, and its conversation"


def _card_lines(tmp_path):
    """The old page's linkCard, run in node: the lines of the card it makes of a link whose checkpoint is the one
    given."""
    script = read("web", "app.js")
    grab = lambda name: re.search(rf"function {name}\(.*?\n\}}\n", script, re.S).group(0)
    one = lambda name: re.search(rf"function {name}\([^\n]*\}}\n", script).group(0)
    probe = tmp_path / "card.js"
    probe.write_text("".join(grab(f) for f in ("el", "buttonFor", "claudeLink", "linkLine", "linkCard"))
                     + "".join(one(f) for f in ("utc", "unix", "shortLine"))
                     + re.search(r"const HIDDEN_ALL = [^\n]*\n", script).group(0) + grab("visible") + CARD)
    base = {"id": "L1", "name": "mac", "kind": "local", "silent": False, "last_heartbeat_at": 1790625600, "events": 0,
            "last_event": None}
    return lambda cp: json.loads(subprocess.run([NODE, str(probe), json.dumps(dict(base, checkpoint=cp))],
                                                capture_output=True, text=True, check=True).stdout)["lines"]


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_the_old_page_s_card_says_when_a_session_was_last_heard(tmp_path):
    # by the server's time heard, any request the session signed; from a
    # server that gives none, by its heartbeat; and not heard from yet where neither is known
    script = read("web", "app.js")
    grab = lambda name: re.search(rf"function {name}\(.*?\n\}}\n", script, re.S).group(0)
    one = lambda name: re.search(rf"function {name}\([^\n]*\}}\n", script).group(0)
    probe = tmp_path / "card.js"
    probe.write_text("".join(grab(f) for f in ("el", "buttonFor", "claudeLink", "linkLine", "linkCard"))
                     + "".join(one(f) for f in ("utc", "unix", "shortLine"))
                     + re.search(r"const HIDDEN_ALL = [^\n]*\n", script).group(0) + grab("visible") + CARD)
    base = {"id": "L1", "name": "mac", "kind": "local", "silent": False, "checkpoint": {"doing": "the fix"},
            "events": 0, "last_event": None}
    head = lambda link: json.loads(subprocess.run([NODE, str(probe), json.dumps(link)], capture_output=True, text=True,
                                                  check=True).stdout)["lines"][0]
    heard = "mac \u00b7 local \u00b7 last heard 2026-09-28 20:00 UTC"
    assert head(dict(base, last_heartbeat_at=None, last_heard_at=1790625600)) == heard
    assert head(dict(base, last_heartbeat_at=1790625600)) == heard                  # a server that gives no time heard
    assert head(dict(base, last_heartbeat_at=None, last_heard_at=None)) == "mac \u00b7 local \u00b7 not heard from yet"


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_the_old_page_s_card_shows_a_checkpoint_s_text_as_it_is(tmp_path):
    # its open where it is text, as the app's card shows it; and a checkpoint that names nothing it is
    # doing says so, not that there is none
    lines = _card_lines(tmp_path)
    assert "open: the pin" in lines({"doing": "the fix", "open": "the pin"})
    assert "doing: (its checkpoint says nothing of what it is doing)" in lines({"open": ["the pin"]})
    assert "doing: (no checkpoint yet)" in lines(None)
    assert 'doing: {"step":2}' in lines({"doing": {"step": 2}})                    # its JSON, where it is not text
    assert 'open: {"a":1}' in lines({"doing": "the fix", "open": {"a": 1}})


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_the_old_page_s_card_shows_what_a_checkpoint_waits_on_and_its_hidden_characters_as_code_points(tmp_path):
    # what it waits on as it is, as the app's card shows it; every character not shown as
    # itself written as its code point, as the rest of the page writes one, but a checkpoint's words, never sealed,
    # as they are; blank text taken for nothing; and how many open items more there are than it shows
    lines = _card_lines(tmp_path)
    dot = " \u00b7 "
    got = lines({"doing": "the fix", "waiting_on": ["the owner's go", "CI"]})
    assert "waiting on: the owner's go" + dot + "CI" in got
    assert 'waiting on: {"who":"owner"}' in lines({"doing": "the fix", "waiting_on": {"who": "owner"}})
    got = lines({"doing": {"say": "a\u202eb"}, "open": "c\u200bd", "waiting_on": "e2e1.its own words"})
    assert 'doing: {"say":"a[U+202E]b"}' in got and "open: c[U+200B]d" in got
    assert "waiting on: e2e1.its own words" in got
    got = lines({"doing": "  ", "open": [f"item {i}" for i in range(1, 23)], "waiting_on": " "})
    assert "doing: (its checkpoint says nothing of what it is doing)" in got
    assert "and 2 more" in got and not any(x.startswith("waiting on:") for x in got)


NOTICES = r"""
const shown = [];
let refusing = process.argv[3] === "refuse";
global.Notification = class {
  static permission = "granted";
  constructor(title, opts) {
    if (refusing) throw new TypeError("Failed to construct 'Notification': Illegal constructor.");
    shown.push({ title, body: opts.body, tag: opts.tag });
  }
};
global.document = { title: "", hidden: true };
const ade = {};
const data = JSON.parse(process.argv[2]);
ade.pending = data.pending;
ade.alerts = data.alerts;
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
@pytest.mark.parametrize("browser", ["shows", "refuse"])
def test_the_page_s_notifications_say_only_what_waits_in_fixed_words(tmp_path, browser):
    # the old page's own notifications, shown while its tab is hidden: the words the server's push notices use
    # (ade/push.py WORDS), never a command, a board channel's or file's name, a linked session's name or a question's
    # words; each tag made at random, never its key; and a browser that refuses to make one (a mobile one) shows
    # none, while the tab's title still counts
    script = read("web", "app.js")
    start = script.index('const TITLE = "Proofpane Community";')
    words = script[start:script.index("function notifyState()", start)]
    items = re.search(r"  ade\.linkItems = .*?\)\)\);\n", script, re.S).group(0)
    probe = tmp_path / "notices.js"
    probe.write_text(NOTICES + words + items + "attention();\nattention();\n"
                     "process.stdout.write(JSON.stringify({ title: document.title, shown }));\n")
    data = {
        "pending": [{"id": "r1", "command": "curl -H 'Authorization: Bearer sk-live-1' https://x"}],
        "alerts": [{"level": "alert", "kind": "silence", "channel": "deals/acquire-globex", "file": "NEXT.md",
                    "text": "silent for 3 hours"},
                   {"level": "info", "kind": "read", "channel": "deals/other", "file": "", "text": "read"}],
        "links": [{"id": "L1", "name": "payroll-mac", "silent": True, "interval_min": 5},
                  {"id": "L2", "name": "quiet-one", "silent": False, "interval_min": 5}],
        "questions": [{"seq": 7, "to": "owner", "from": "payroll-mac", "text": "Ship Initech's merger memo?"},
                      {"seq": 8, "to": "cloud", "from": "mac", "text": "not the owner's"}],
    }
    run = subprocess.run([NODE, str(probe), json.dumps(data), browser], capture_output=True, text=True, check=True)
    got = json.loads(run.stdout)
    assert got["title"] == "(4) Proofpane Community"
    if browser == "refuse":
        assert got["shown"] == []
        return
    assert [(n["title"], n["body"]) for n in got["shown"]] == [
        ("A command waits for you", "Open Approvals."), ("A board needs you", "Open the ADE."),
        ("A linked session needs a look", "Open Linked sessions."),
        ("A question waits for you", "Open Linked sessions to read it.")]
    tags = [n["tag"] for n in got["shown"]]
    assert all(re.fullmatch(r"pp-[0-9a-f]{16}", t) for t in tags) and len(set(tags)) == 4
    shown = json.dumps(got["shown"]).lower()
    for word in ("sk-live", "curl", "globex", "next.md", "silence", "payroll", "initech", "r1", "l1", ":7"):
        assert word not in shown, word


# ---- the page refuses what the server refuses ------------------------------

def _rules():
    """The page's own rules for text (the shapes it refuses, the characters it hides), which moved from Channel.tsx to
    textRules.ts when the page began opening sealed text and holding it to the same rules."""
    return read("ui", "src", "textRules.ts")


def test_the_page_names_the_server_s_secret_shapes_one_for_one():
    from proofpane_open.ade import link
    page = re.findall(r'^  \["([^"]+)", /(.+)/(i?)\],$', _rules(), re.M)
    server = [(label, pattern.pattern, "i" if pattern.flags & re.IGNORECASE else "")
              for label, pattern in link.SECRET_SHAPES]
    assert page == server
    # the page's patterns have no u flag, so \b and the case folding are ASCII there: the server's are ASCII too
    assert all(pattern.flags & ~re.IGNORECASE == re.ASCII for _, pattern in link.SECRET_SHAPES)


# a sample each shape refuses, on both sides
SHAPE_SAMPLES = ["x sk-ant-y", "x sk-abcdefghijklmnop y", "x ghs_y", "x github_pat_y", "x FlyV1 y",
                 "x AKIAy", "-----BEGIN RSA PRIVATE KEY", "x adeh_y", "x PP-ABCDEFGH y", "x sk_live_y", "x xoxb-y",
                 "x AIza" + "B" * 30 + " y", "x glpat-y", "x npm_" + "a1" * 18 + " y",
                 "x eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.s y"]


def _shape_points():
    """The code points tried: the Basic Multilingual Plane but the surrogates, and planes 1 and 14's blocks with
    letters, digits, case pairs and invisible characters."""
    points = [c for c in range(0x10000) if not 0xD800 <= c <= 0xDFFF]
    for start, end in ((0x10400, 0x10450), (0x1D400, 0x1D800), (0x1E900, 0x1E960), (0x1F100, 0x1F200),
                       (0xE0000, 0xE0080)):
        points.extend(range(start, end))
    return points


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's patterns")
def test_the_page_and_the_server_refuse_the_same_secret_shapes(tmp_path):
    """Behaviour, not source text: each shape's sample with one character changed, around where it matches (the one
    before it, its first, its middle, its last and the one after it), to every code point tried, refused or not alike
    by the server's pattern and the page's (a long s, the Kelvin sign and an e with an acute accent were once
    refused on one side only)."""
    from proofpane_open.ade import link
    page = re.findall(r'^  \["[^"]+", (/.+/i?)\],$', _rules(), re.M)
    assert len(page) == len(link.SECRET_SHAPES) == len(SHAPE_SAMPLES)
    cases = []
    for (_, pattern), sample in zip(link.SECRET_SHAPES, SHAPE_SAMPLES):
        found = pattern.search(sample)
        assert found, sample
        a, b = found.span()
        cases.append({"sample": sample, "at": sorted({i for i in (a - 1, a, (a + b) // 2, b - 1, b)
                                                      if 0 <= i < len(sample)})})
    points = _shape_points()
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"cases": cases, "points": points}))
    probe = tmp_path / "probe.js"
    probe.write_text("const fs = require('fs');\n"
                     f"const shapes = [{', '.join(page)}];\n"
                     "const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));\n"
                     "const out = [];\n"
                     "spec.cases.forEach((c, i) => {\n"
                     "  const chars = Array.from(c.sample);\n"
                     "  for (const at of c.at) {\n"
                     "    let bits = '';\n"
                     "    for (const p of spec.points) {\n"
                     "      const s = chars.slice(0, at).join('') + String.fromCodePoint(p)\n"
                     "        + chars.slice(at + 1).join('');\n"
                     "      bits += shapes[i].test(s) ? '1' : '0';\n"
                     "    }\n"
                     "    out.push(bits);\n"
                     "  }\n"
                     "});\n"
                     "process.stdout.write(JSON.stringify(out));\n")
    got = json.loads(subprocess.run([NODE, str(probe), str(spec)], capture_output=True, text=True, check=True).stdout)
    want = []
    for (_, pattern), c in zip(link.SECRET_SHAPES, cases):
        for at in c["at"]:
            head, tail = c["sample"][:at], c["sample"][at + 1:]
            want.append("".join("1" if pattern.search(head + chr(p) + tail) else "0" for p in points))
    differ = [(n, f"U+{points[k]:04X}") for n, (g, w) in enumerate(zip(got, want)) for k in range(len(points))
              if g[k] != w[k]][:10]
    assert len(got) == len(want) and differ == []


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's pattern")
def test_the_page_and_the_server_refuse_the_same_characters(tmp_path):
    import unicodedata
    from proofpane_open.ade import link_commands
    pattern = re.search(r"^export const HIDDEN = (/.+/u);$", _rules(), re.M).group(1)
    probe = tmp_path / "probe.js"
    probe.write_text(f"const HIDDEN = {pattern};\nconst UNASSIGNED = /\\p{{Cn}}/u;\nconst out = [];\n"
                     "for (let c = 0; c <= 0x10FFFF; c++) {\n"
                     "  if (c >= 0xD800 && c <= 0xDFFF) continue;\n"
                     "  const ch = String.fromCodePoint(c);\n"
                     "  if (!UNASSIGNED.test(ch) && HIDDEN.test(ch)) out.push(c);\n"
                     "}\nprocess.stdout.write(JSON.stringify(out));\n")
    page = set(json.loads(subprocess.run([NODE, str(probe)], capture_output=True, text=True, check=True).stdout))
    server = {c for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF and unicodedata.category(chr(c)) != "Cn"
              and link_commands.hidden_character(chr(c)) is not None}
    # both know the character, and the page refuses it exactly where the server does
    known = {c for c in page | server if unicodedata.category(chr(c)) != "Cn"}
    assert {c for c in page if c in known} == {c for c in server if c in known}

