"use strict";
// Proofpane Community: the console, after Mission Control. A list
// of every workspace session with its state, needs-you first, with every question waiting in any chat above it; and one
// to four panes, each a chat with Claude Code (which runs in the workspace through the Claude Agent SDK) or a link to a
// session's terminal. Two panes sit side by side, three or four in a 2x2; the lines between them and the list's edge
// drag, and are remembered for the account. On a phone the list is a strip above one pane. Every chat's events come on
// one socket for the whole page, and the page speaks through the chat routes. Everything it shows is written with
// textContent: a chat's text is data.

const $ = (id) => document.getElementById(id);
const WS = "/api/ade/workspace";
const MAX_PANES = 4;
const con = { sessions: [], logs: new Map(), panes: [], focus: 0, socket: null, retry: 1000, repos: false, user: null,
              hasRepos: false, claude: null };
// the app refuses a chat before Claude is signed in in the workspace, with these words (workspace_routes.CLAUDE_FIRST)
const CLAUDE_FIRST = "Sign in to Claude first, with Sign in to Claude at the top of the Workspace section.";
let token = null;
try { token = localStorage.getItem("pp-open-token"); } catch (e) { token = null; }

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = String(text);
  if (cls) e.className = cls;
  return e;
}

function note(text) {
  $("con-note").textContent = text || "";
  $("con-note").hidden = !text;
}

async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { "Content-Type": "application/json", Authorization: "Bearer " + token },
                                body: body === undefined ? undefined : JSON.stringify(body) });
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  if (!r.ok) throw new Error(data && typeof data.detail === "string" ? data.detail : "The ADE answered " + r.status + ".");
  return data;
}

// a file from this device into a chat's worktree: its
// path there goes into the message, for Claude to read
const MAX_UPLOAD = 10 * 1024 * 1024;
async function upload(sid, file) {
  if (file.size > MAX_UPLOAD) throw new Error("A file is at most 10 MB.");
  const r = await fetch(WS + "/sessions/" + encodeURIComponent(sid) + "/upload?name=" + encodeURIComponent(file.name),
                        { method: "POST", headers: { "Content-Type": "application/octet-stream",
                                                     Authorization: "Bearer " + token }, body: file });
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  if (!r.ok) throw new Error(data && typeof data.detail === "string" ? data.detail : "The ADE answered " + r.status + ".");
  return data;
}

function attached(text, path) {
  return (text && !text.endsWith("\n") ? text + "\n" : text || "") + "Attached: " + path + "\n";
}

function size(n) { return n < 1024 * 1024 ? Math.max(1, Math.round(n / 1024)) + " KB" : (n / 1048576).toFixed(1) + " MB"; }

function phone() { return window.matchMedia("(max-width: 640px)").matches; }

// what this browser remembers for the account: the list's width and where the lines between panes are
function remembered(name, fallback) {
  try {
    const v = JSON.parse(localStorage.getItem("pp-console:" + con.user + ":" + name));
    return v === null ? fallback : v;
  } catch (e) { return fallback; }
}
function remember(name, value) {
  try { localStorage.setItem("pp-console:" + con.user + ":" + name, JSON.stringify(value)); } catch (e) { /* kept */ }
}

// ---- a session's state, in the list's words -------------------------------------------------------------------------

const WORDS = { needs_you: "needs you", running: "running", idle: "idle", stopped: "stopped", failed: "failed" };
const FIRST = { needs_you: 0, running: 1 };

function stateOf(s) {
  if (s.chat) return s.chat.state;
  if (s.running) return "running";
  return s.exit_code !== null && s.exit_code !== undefined ? "ended" : "stopped";
}

function stateWord(s) {
  const st = stateOf(s);
  if (st === "ended") return s.exit_code < 0 ? "ended by signal " + -s.exit_code : "ended with status " + s.exit_code;
  return WORDS[st] || st;
}

function sortedSessions(list) {
  const rank = (s) => (stateOf(s) in FIRST ? FIRST[stateOf(s)] : 2);
  return list.slice().sort((a, b) => rank(a) - rank(b) || (b.created_at || 0) - (a.created_at || 0));
}

function label(s) {
  const what = s.program === "chat" ? "Chat" : s.program === "github" ? "GitHub sign-in" :
    s.program === "claude-sign-in" ? "Claude sign-in" : s.program;
  return what + (s.repo ? " in " + s.repo : "");
}

function since(t, now) {
  const s = Math.max(0, Math.floor((now || Date.now() / 1000) - t));
  return s < 60 ? "just now" : s < 3600 ? Math.floor(s / 60) + " min ago" : s < 86400 ? Math.floor(s / 3600) + " h ago" :
    Math.floor(s / 86400) + " d ago";
}

function money(x) { return "$" + Number(x).toFixed(4); }
// what a chat's turns cost is Claude Code's own estimate at API prices, not a bill: signed in with a Claude plan, the
// plan is paid, not each token
function estimate(x) { return "est. " + money(x); }
const ESTIMATE = "Claude Code's own estimate at API prices, not a bill: signed in with a Claude plan, you pay the " +
                 "plan, not each token.";

// every question waiting in any chat, oldest first
function waitingAsks(sessions) {
  const out = [];
  for (const s of sessions) for (const a of (s.chat && s.chat.asks) || []) out.push({ s, a });
  return out.sort((x, y) => (x.a.time || 0) - (y.a.time || 0));
}

function button(text, fn, cls) {
  const b = el("button", text, cls === undefined ? "muted-button" : cls);
  b.type = "button";
  b.addEventListener("click", (ev) => {
    ev.stopPropagation();
    Promise.resolve().then(() => fn(b)).catch((e) => note(e.message));
  });
  return b;
}

function answerButtons(sid, ask) {
  const answer = (decision) => async (b) => {
    b.disabled = true;
    await api("POST", WS + "/sessions/" + encodeURIComponent(sid) + "/chat/answer", { id: ask.id, decision });
  };
  const row = el("p", null, "row-buttons");
  row.append(button("Allow once", answer("allow"), ""), " ");
  if (ask.for_session) row.append(button("Allow for this session", answer("allow_session")), " ");
  row.append(button("Deny", answer("deny")));
  return row;
}

// ---- the list and the queue -----------------------------------------------------------------------------------------

function renderList() {
  const list = $("con-list");
  list.textContent = "";
  const all = sortedSessions(con.sessions);
  const open = new Set(con.panes.map((p) => p.sid));
  for (const s of all) {
    const st = stateOf(s);
    const li = el("li", null, "con-row st-" + st + (open.has(s.id) ? " open" : ""));
    const top = el("div", null, "con-row-top");
    top.append(el("span", label(s), "con-name"), el("span", stateWord(s), "con-status st-" + st));
    li.appendChild(top);
    const c = s.chat;
    li.appendChild(el("div", c && c.last_text ? c.last_text : (s.task || "").split("\n")[0], "con-line"));
    const meta = el("div", null, "small con-row-meta");
    const bits = [];
    if (c && c.cost) bits.push(estimate(c.cost));
    const at = c && c.last_at ? c.last_at : s.ended_at || s.created_at;
    if (at) bits.push(since(at));
    meta.appendChild(el("span", bits.join(" · ")));
    if (!open.has(s.id) && con.panes.length && con.panes.length < MAX_PANES && !phone()) {
      meta.appendChild(button("Beside", () => openPane(s.id, true), "muted-button con-beside"));
    }
    li.appendChild(meta);
    li.tabIndex = 0;
    li.setAttribute("role", "button");
    li.addEventListener("click", () => openPane(s.id, false).catch((e) => note(e.message)));
    li.addEventListener("keydown", (ev) => { if (ev.key === "Enter") li.click(); });
    list.appendChild(li);
  }
  $("con-empty").hidden = all.length > 0;
  const waiting = waitingAsks(con.sessions);
  const needs = all.filter((s) => stateOf(s) === "needs_you").length;
  $("con-count").textContent = all.length ? "(" + all.length + ")" : "";
  document.title = (needs ? "(" + needs + ") " : "") + "Console · Proofpane Community";
  const queue = $("con-queue");
  queue.textContent = "";
  for (const { s, a } of waiting) {
    const li = el("li", null, "con-row st-needs_you");
    const top = el("div", null, "con-row-top");
    top.append(el("span", label(s), "con-name"), button("Open", () => openPane(s.id, false), "muted-button con-beside"));
    li.append(top, el("div", a.name + (a.summary ? ": " + a.summary : ""), "con-line"), answerButtons(s.id, a));
    queue.appendChild(li);
  }
  $("con-queue-box").hidden = waiting.length === 0;
  $("con-queue-count").textContent = waiting.length ? "(" + waiting.length + ")" : "";
}

async function loadSessions() {
  const st = await api("GET", WS);
  if (!st.enabled || st.allowed === false) {
    $("con-state").textContent = st.detail || "The workspace is not on for this account.";
    return false;
  }
  $("con-start-row").hidden = !!st.running || !!st.starting;
  $("con-state").textContent = st.running ? "" : st.starting ? "Starting the workspace..." : "";
  if (!st.running) return false;
  con.sessions = (await api("GET", WS + "/sessions")).sessions;
  renderList();
  for (const p of con.panes) drawHead(p);
  if (!con.repos) {
    const repos = (await api("GET", WS + "/repos")).repos;
    const select = $("con-new").repo;
    select.textContent = "";
    for (const r of repos) {
      const o = el("option", r.name + " · " + r.label);
      o.value = r.name;
      select.appendChild(o);
    }
    if (!repos.length) {
      const o = el("option", "No repository yet: clone one on the ADE page");
      o.value = "";
      o.disabled = true;
      select.appendChild(o);
    }
    // a chat starts once there is a repository to start it in
    con.hasRepos = repos.length > 0;
    gateChat();
    con.repos = true;
  }
  return true;
}

// A chat starts once Claude is signed in in the workspace, on the ADE page: Claude Code's own sign-in never comes up in
// a terminal, where a phone cannot copy. Asked when the console starts and when "New chat" opens.
async function loadClaude() {
  try {
    const c = await api("GET", WS + "/claude");
    con.claude = !!(c.installed && c.signed_in);
  } catch (e) {
    con.claude = null;
  }
  gateChat();
  loadCapabilities();
}

// what the installed Claude Code offers a chat (chat-settings.js): read at the start and every minute, so the choices
// and the version follow an update of it; the version it had before is said once
async function loadCapabilities() {
  let caps;
  try { caps = await api("GET", WS + "/capabilities"); } catch (e) { return; }
  const before = CHAT_SETTINGS.use(caps);
  $("con-version").textContent = caps && caps.claude_version ? "Claude Code " + caps.claude_version : "";
  if (before) note("Claude Code updated from " + before + " to " + caps.claude_version + ".");
}

function gateChat() {
  $("con-claude").hidden = con.claude !== false;
  $("con-new").querySelector("button[type=submit]").disabled = !con.hasRepos || con.claude === false;
}

// ---- a pane: one chat -----------------------------------------------------------------------------------------------

// a message's text as paragraphs and code blocks, split at ``` fences (an unclosed fence is still code); no markup
function textBlocks(text) {
  const out = [];
  let buf = [];
  let code = null;
  for (const line of String(text).split("\n")) {
    if (/^\s*```/.test(line)) {
      if (code === null) {
        out.push(["p", buf.join("\n")]);
        buf = [];
        code = [];
      } else {
        out.push(["pre", code.join("\n")]);
        code = null;
      }
    } else if (code !== null) {
      code.push(line);
    } else {
      buf.push(line);
    }
  }
  if (code !== null) out.push(["pre", code.join("\n")]);
  out.push(["p", buf.join("\n")]);
  return out.filter(([, t]) => t.trim() !== "");
}

function copyRow(pre) {
  const row = el("div", null, "row-buttons");
  const b = el("button", "Copy", "muted-button");
  b.type = "button";
  b.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(pre.textContent);
      b.textContent = "Copied";
      setTimeout(() => { b.textContent = "Copy"; }, 2000);
    } catch (e) {
      b.textContent = "Select the text to copy it";
    }
  });
  row.appendChild(b);
  return row;
}

function message(cls, who, text) {
  const d = el("div", null, "con-msg " + cls);
  d.appendChild(el("div", who, "con-who small"));
  for (const [kind, t] of textBlocks(text)) {
    if (kind === "pre") {
      const pre = el("pre", t, "copyable");
      d.append(copyRow(pre), pre);
    } else {
      d.appendChild(el("p", t));
    }
  }
  return d;
}

const DECIDED = { allow: "allowed once", allow_session: "allowed for this session", deny: "denied",
                  stop: "stopped with the turn" };

// one event, drawn into a pane's log; a tool's result and an answer change the card drawn before them
// ---- a chat's MCP servers (the agent's mcp.py) ------------------------------------------------------------------
// A repository's own servers (its .mcp.json) run only once approved here, each as it was shown; the workspace's own
// (claude mcp add) and a Claude plan's claude.ai connectors run as Claude Code runs them. A change takes effect from the
// chat's next message.

const MCP_WHERE = { user: "added for every repository in the workspace", local: "added for this repository",
                    claudeai: "a connector of your Claude account (claude.ai)", managed: "set by the workspace",
                    dynamic: "given to this chat", project: "a .mcp.json" };
const MCP_STATE = { connected: "on", pending: "starting", failed: "failed", "needs-auth": "needs its sign-in",
                    disabled: "off" };
const MCP_CHANGE = { approved: "You approved", stopped: "You stopped using", added: "You added", removed: "You removed" };

function mcpPath(sid) { return WS + "/sessions/" + encodeURIComponent(sid) + "/chat/mcp"; }

// where a server comes from
function mcpWhere(s) {
  if (Array.isArray(s.files)) return s.files.map((f) => (f === "this repository" ? "this repository's .mcp.json" : f))
    .join(", ");
  return MCP_WHERE[s.scope] || s.scope || "";
}

// how it runs: the command line or the address, and the names of what it is given (never their values)
function mcpRuns(s) {
  const names = Array.isArray(s.names) && s.names.length ? s.names.join(", ") : "";
  if (s.kind === "command") return "Runs " + s.line + (names ? " (with " + names + " set)" : "");
  if (s.kind === "http" || s.kind === "sse") return "Connects to " + s.line + (names ? " (with its " + names + " header)" : "");
  return s.line || "";
}

// its state, as claude says
function mcpState(s) {
  if (s.status === "connected") return "On" + (s.tools ? ", " + s.tools + (s.tools === 1 ? " tool" : " tools") : "");
  if (s.status === "failed") return "Failed" + (s.error ? ": " + s.error : "");
  if (s.status === "needs-auth") return "Needs its own sign-in, which this page cannot give yet (claude in a terminal " +
                                        "can: /mcp).";
  if (s.status === "pending") return "Starting...";
  if (s.status === "disabled") return "Off";
  return s.status || "";
}

// an "mcp" line of the chat's log: what claude has at its start, what was held back and why
function mcpLine(ev) {
  if (ev.strict) return "No MCP server runs in this chat: " + ev.strict + ". Fix or remove that file.";
  const bits = [];
  if (ev.servers && ev.servers.length) {
    bits.push("MCP servers: " + ev.servers.map((s) => s.name + " (" + (MCP_STATE[s.status] || s.status) + ")")
      .join(", ") + ".");
  }
  if (ev.held && ev.held.length) bits.push("Held back until you approve them under Connectors: " +
                                           ev.held.join(", ") + ".");
  if (ev.stray && ev.stray.length) bits.push("Turned off, as no .mcp.json here declares them: " +
                                             ev.stray.join(", ") + ".");
  return bits.join(" ");
}

// the owner's change, in the chat's log
function mcpChange(ev) {
  return (MCP_CHANGE[ev.change] || ev.change) + " " + ev.name + (ev.line ? " (" + ev.line + ")" : "") +
    (ev.change === "approved" || ev.change === "added" ? ": it starts with the chat's next message." : ".");
}

async function loadMcp(pane) {
  try {
    drawMcp(pane, await api("GET", mcpPath(pane.sid)));
  } catch (e) {
    note(e.message);
  }
}

function mcpAct(pane, body) {
  return async (b) => {
    b.disabled = true;
    try {
      drawMcp(pane, await api("POST", mcpPath(pane.sid), body));
      note("");
    } finally { b.disabled = false; }
  };
}

function mcpItem(s, where, state, waiting) {
  const li = el("li", null, waiting ? "waiting" : "");
  const top = el("div");
  top.append(el("strong", s.name), " ", el("span", where, "small"));
  li.append(top, el("div", mcpRuns(s), "con-mcp-line"));
  const row = el("p", null, "row-buttons");
  row.appendChild(el("span", state, "small"));
  li.appendChild(row);
  return { li, row };
}

function drawMcp(pane, v) {
  const box = pane.mcpBody;
  box.textContent = "";
  if (v.strict) box.appendChild(el("p", "No MCP server runs in this chat: " + v.strict + ". Fix or remove that file.",
                                   "error"));
  const live = Array.isArray(v.live) ? v.live : [];
  const list = el("ul", null, "con-mcp-list");
  const declared = new Set();
  for (const s of v.servers || []) {
    declared.add(s.name);
    const on = live.find((x) => x.name === s.name);
    const state = s.conflict ? "Two .mcp.json files define it differently: it does not run."
      : !s.approved ? "Not started: it runs in your workspace, with its access to your repositories and sign-ins, " +
                      "only once you approve it."
      : on ? mcpState(on) : "Approved: it starts with the chat's next message.";
    const { li, row } = mcpItem(s, mcpWhere(s), state, !s.approved && !s.conflict);
    if (!s.conflict && !s.approved) {
      row.appendChild(button("Approve", mcpAct(pane, { action: "approve", name: s.name, fingerprint: s.fingerprint }),
                             ""));
    } else if (s.approved) {
      row.appendChild(button("Stop using", mcpAct(pane, { action: "stop", name: s.name })));
    }
    list.appendChild(li);
  }
  for (const s of live) {
    if (s.scope === "project" && declared.has(s.name)) continue;
    const { li, row } = mcpItem(s, mcpWhere(s), s.removed ? "Removed: it goes with the chat's next message."
                                                          : mcpState(s), false);
    if ((s.scope === "user" || s.scope === "local") && !s.removed) {
      row.appendChild(button("Remove", mcpAct(pane, { action: "remove", name: s.name, scope: s.scope })));
    }
    list.appendChild(li);
  }
  if (list.children.length) box.appendChild(list);
  else box.appendChild(el("p", v.running ? "This chat has no MCP server." : "No MCP server yet; claude says what it " +
                                           "has when it starts, with the chat's next message.", "small"));
  if (!v.running) box.appendChild(el("p", "Claude is not running in this chat now: the list shows what it had.", "small"));
  box.append(pane.mcpAdd, el("p", "A change takes effect from the chat's next message. Your Claude account's own " +
                                  "connectors are set on claude.ai.", "small"));
}

// the form that adds a server with Claude Code's own `claude mcp add`, folded until opened; what is typed, secrets
// included, is not kept on the page once it is added
function mcpAddForm(pane) {
  const box = el("details", null, "con-mcp-add");
  box.appendChild(el("summary", "Add a server"));
  const f = el("form");
  box.appendChild(f);
  const field = (tag, name, label, extra) => {
    const x = el(tag);
    x.name = name;
    Object.assign(x, extra || {});
    for (const a of ["autocapitalize", "autocorrect", "spellcheck"]) x.setAttribute(a, a === "spellcheck" ? "false" : "off");
    const l = el("label", label + " ");
    l.appendChild(x);
    f.appendChild(l);
    return x;
  };
  const choose = (name, label, list) => {
    const x = el("select");
    x.name = name;
    for (const [value, text] of list) {
      const o = el("option", text);
      o.value = value;
      x.appendChild(o);
    }
    const l = el("label", label + " ");
    l.appendChild(x);
    f.appendChild(l);
    return x;
  };
  const name = field("input", "name", "Name", { required: true, placeholder: "github" });
  const kind = choose("kind", "It is", [["command", "a command it runs"], ["url", "an address it connects to"],
                                        ["sse", "an address, over SSE (an older server)"]]);
  const what = field("input", "what", "Command line", { required: true });
  const lines = field("textarea", "lines", "Environment, one NAME=value a line (optional)", { rows: 2 });
  const scope = choose("scope", "For", [["repository", "this repository"],
                                        ["workspace", "every repository in the workspace"]]);
  const address = () => kind.value === "url" || kind.value === "sse";
  const words = () => {
    const url = address();
    what.parentNode.firstChild.textContent = url ? "Address " : "Command line ";
    what.placeholder = url ? "https://example.com/mcp" : "npx -y @modelcontextprotocol/server-github";
    lines.parentNode.firstChild.textContent = url ? "Headers, one Name: value a line (optional) "
                                                  : "Environment, one NAME=value a line (optional) ";
  };
  kind.addEventListener("change", words);
  words();
  const add = el("button", "Add");
  add.type = "submit";
  f.append(add, el("p", "Claude Code keeps it in the workspace; the page shows the names you give it, never their " +
                        "values.", "small"));
  f.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const spec = { name: name.value.trim(), scope: scope.value, lines: lines.value };
    if (address()) {
      spec.url = what.value.trim();
      if (kind.value === "sse") spec.transport = "sse";
    } else {
      spec.command = what.value;
    }
    add.disabled = true;
    try {
      const v = await api("POST", mcpPath(pane.sid), { action: "add", spec });
      f.reset();
      words();
      box.open = false;
      drawMcp(pane, v);
      note("");
    } catch (e) {
      note(e.message);
    } finally { add.disabled = false; }
  });
  return box;
}

function drawEvent(pane, ev, pending) {
  const box = pane.log;
  if (ev.kind === "user") box.appendChild(message("me", "You", ev.text));
  else if (ev.kind === "text") box.appendChild(message("claude", "Claude", ev.text));
  else if (ev.kind === "tool") {
    const d = el("div", null, "con-tool");
    const st = el("span", "running", "con-status st-running");
    d.append(el("span", ev.name + (ev.summary ? " · " + ev.summary : ""), "con-tool-what"), " ", st);
    pane.cards.set("tool:" + ev.id, { d, st });
    box.appendChild(d);
  } else if (ev.kind === "tool_end") {
    const card = pane.cards.get("tool:" + ev.id);
    if (card) {
      card.st.textContent = ev.error ? "failed" : "done";
      card.st.className = "con-status " + (ev.error ? "st-failed" : "st-idle");
      if (ev.summary) card.d.appendChild(el("div", ev.summary, "small con-line"));
    }
  } else if (ev.kind === "ask") {
    // the question about a tool call goes in that call's card, before its result
    const tool = pane.cards.get("tool:" + ev.id);
    const d = el("div", null, "con-ask");
    d.appendChild(el("div", tool ? "Claude asks to use it." : "Claude asks to use " + ev.name +
                                                                   (ev.summary ? ": " + ev.summary : "")));
    const row = pending.has(ev.id) ? answerButtons(pane.sid, ev) : el("p", null, "row-buttons");
    if (!pending.has(ev.id)) row.appendChild(el("span", "no longer waiting", "small"));
    d.appendChild(row);
    pane.cards.set("ask:" + ev.id, { d, row });
    (tool ? tool.d : box).appendChild(d);
  } else if (ev.kind === "answered") {
    const card = pane.cards.get("ask:" + ev.id);
    if (card) {
      card.row.textContent = "";
      card.row.appendChild(el("span", DECIDED[ev.decision] || ev.decision, "small"));
    }
  } else if (ev.kind === "result") {
    const parts = [ev.error ? "Turn ended: " + ev.subtype : "Turn done"];
    if (typeof ev.duration_ms === "number") parts.push((ev.duration_ms / 1000).toFixed(1) + " s");
    if (typeof ev.cost === "number") parts.push(estimate(ev.cost));
    box.appendChild(el("div", parts.join(" · "), "small con-result"));
  } else if (ev.kind === "error") {
    box.appendChild(el("div", ev.text, "error"));
  } else if (ev.kind === "upload") {
    box.appendChild(el("div", "You attached " + ev.name + " (" + size(ev.size) + "), at " + ev.path + " in the " +
                       "session's worktree, kept out of its commits.", "small"));
  } else if (ev.kind === "settings") {
    box.appendChild(el("div", "Settings: " + CHAT_SETTINGS.words(ev) + ".", "small"));
  } else if (ev.kind === "mcp" || ev.kind === "mcp_change") {
    box.appendChild(el("div", ev.kind === "mcp" ? mcpLine(ev) : mcpChange(ev), ev.strict ? "error" : "small"));
  } else if (ev.kind === "session") {
    // where it runs, so that nobody takes it for their own editor's session
    box.appendChild(el("div", "Claude Code" + (ev.model ? " (" + ev.model + ")" : "") + " is working in this session, " +
                       "in your workspace in the cloud (a machine of its own on Fly), not on your computer.", "small"));
  }
}

function makePane(sid) {
  const root = el("section", null, "con-pane");
  const top = el("div", null, "con-pane-top");
  const title = el("strong");
  const status = el("span", null, "con-status");
  const pane = { sid, root, title, status, cards: new Map() };
  const close = button("Close", () => closePane(pane));
  top.append(title, " ", status, " ", close);
  pane.meta = el("div", null, "small con-line");
  const row = el("p", null, "row-buttons");
  pane.interrupt = button("Stop this turn", () =>
    api("POST", WS + "/sessions/" + encodeURIComponent(pane.sid) + "/chat/interrupt", {}));
  pane.finish = button("Finish and verify", async (b) => {
    b.disabled = true;
    try {
      const out = await api("POST", WS + "/sessions/" + encodeURIComponent(pane.sid) + "/finish", {});
      note("Finished: " + (out.status || "see Finished sessions on the ADE page") + ".");
    } finally { b.disabled = false; }
  });
  pane.term = el("a", "Open its terminal");
  pane.term.target = "_blank";
  pane.term.rel = "noopener";
  row.append(pane.interrupt, " ", pane.finish, " ", pane.term);
  // its model, effort and permission mode (chat-settings.js): the model and the mode reach a running Claude at once,
  // a new effort the next message
  pane.settings = el("details", null, "con-settings");
  pane.settingsWords = el("summary", "Settings");
  pane.fields = CHAT_SETTINGS.fields({});
  const apply = button("Apply", async (b) => {
    b.disabled = true;
    try {
      await api("POST", WS + "/sessions/" + encodeURIComponent(pane.sid) + "/chat/settings", pane.fields.read());
      pane.settings.open = false;
      note("");
      await loadSessions();
    } finally { b.disabled = false; }
  }, "");
  pane.settings.append(pane.settingsWords, pane.fields.box, apply);
  // its MCP servers, read when the panel opens
  pane.mcp = el("details", null, "con-mcp");
  pane.mcpBody = el("div", null, "con-mcp-body");
  pane.mcpAdd = mcpAddForm(pane);
  pane.mcp.append(el("summary", "Connectors (MCP)"), pane.mcpBody);
  pane.mcp.addEventListener("toggle", () => { if (pane.mcp.open) loadMcp(pane); });
  const head = el("div", null, "con-pane-head");
  head.append(top, pane.meta, row, pane.settings, pane.mcp);
  pane.log = el("div", null, "con-log");
  pane.log.setAttribute("aria-live", "polite");
  pane.form = el("form", null, "con-send");
  pane.box = el("textarea");
  pane.box.name = "message";
  pane.box.rows = 2;
  pane.box.required = true;
  pane.send = el("button", "Send");
  pane.send.type = "submit";
  // a label around the file's input: a tap on it opens the phone's own picker, which a script's click may not
  pane.file = el("input");
  pane.file.type = "file";
  pane.file.hidden = true;
  pane.attachWord = el("span", "Attach");
  pane.attach = el("label", null, "button-link con-attach");
  pane.attach.append(pane.attachWord, pane.file);
  pane.file.addEventListener("change", async () => {
    const file = pane.file.files && pane.file.files[0];
    pane.file.value = "";
    if (!file) return;
    pane.file.disabled = true;
    pane.attachWord.textContent = "Attaching...";
    try {
      const out = await upload(pane.sid, file);
      pane.box.value = attached(pane.box.value, out.path);
      note("");
    } catch (e) {
      note(e.message);
    } finally {
      pane.file.disabled = false;
      pane.attachWord.textContent = "Attach";
    }
  });
  // the microphone (voice.js), where the browser can listen: what is said goes into the field, for you to send
  pane.voice = VOICE.attach(pane.box, { quiet: true });
  pane.form.append(pane.box, pane.attach);
  if (pane.voice) pane.form.append(pane.voice.button, pane.voice.line);
  pane.form.append(pane.send);
  pane.form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (pane.voice) pane.voice.cancel();         // no late words into the field once it is sent
    const text = pane.box.value;
    if (!text.trim()) return;
    api("POST", WS + "/sessions/" + encodeURIComponent(pane.sid) + "/chat", { text })
      .then(() => { pane.box.value = ""; note(""); })
      .catch((e) => note(e.message));
  });
  pane.box.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) pane.form.requestSubmit();
  });
  root.append(head, pane.log, pane.form);
  root.addEventListener("pointerdown", () => focusPane(pane));
  return pane;
}

function drawHead(pane) {
  const s = con.sessions.find((x) => x.id === pane.sid);
  if (!s) return;
  pane.title.textContent = label(s);
  pane.status.textContent = stateWord(s);
  pane.status.className = "con-status st-" + stateOf(s);
  const c = s.chat;
  pane.meta.textContent = (s.task || "").split("\n")[0] + (c && c.cost ? " · " + estimate(c.cost) + " so far" : "");
  pane.meta.title = c && c.cost ? ESTIMATE : "";
  pane.settings.hidden = s.program !== "chat";
  pane.mcp.hidden = s.program !== "chat";
  pane.settingsWords.textContent = "Settings: " + CHAT_SETTINGS.words(c && c.chosen);
  if (!pane.settings.open) pane.fields.show(c && c.chosen);         // never under the reader's hands
  const busy = !!c && (c.state === "running" || c.state === "needs_you");
  pane.interrupt.hidden = !busy;
  pane.finish.hidden = !s.repo;
  pane.form.hidden = s.program !== "chat";
  pane.box.disabled = busy;
  pane.send.disabled = busy;
  if (pane.voice) pane.voice.enable(!busy);
  pane.box.placeholder = busy ? "Claude is working: wait, or stop this turn" : "Message Claude";
  pane.term.hidden = s.program === "chat" || !(s.running || (s.exit_code !== null && s.exit_code !== undefined));
  if (!pane.term.hidden) pane.term.href = "terminal.html#" + s.id;
}

function drawLog(pane) {
  pane.log.textContent = "";
  pane.cards.clear();
  const s = con.sessions.find((x) => x.id === pane.sid);
  if (s && s.program !== "chat") {
    pane.log.appendChild(el("p", "This session runs in a terminal: open it with the link above.", "small"));
    return;
  }
  const log = con.logs.get(pane.sid);
  if (!log) return;
  const pending = new Set(log.pending);
  for (const ev of log.events) drawEvent(pane, ev, pending);
  pane.log.scrollTop = pane.log.scrollHeight;
}

async function readLog(sid, log) {
  // the log from where this page got to, a page at a time
  let more = true;
  while (more) {
    const got = await api("GET", WS + "/sessions/" + encodeURIComponent(sid) + "/chat?after=" + log.seq);
    for (const ev of got.events) {
      if (ev.seq > log.seq) {
        log.events.push(ev);
        log.seq = ev.seq;
      }
    }
    log.pending = got.pending;
    more = got.more && got.events.length > 0;
  }
}

// ---- the panes: one to four, the lines between them remembered ------------------------------------------------------

function saveHash() {
  const want = con.panes.map((p) => p.sid).join(",");
  if (location.hash.slice(1) !== want) history.replaceState(null, "", want ? "#" + want : location.pathname);
}

function focusPane(pane) {
  const i = con.panes.indexOf(pane);
  if (i < 0 || i === con.focus) return;
  con.focus = i;
  layout();
}

async function openPane(sid, beside) {
  const at = con.panes.findIndex((p) => p.sid === sid);
  if (at >= 0) {
    con.focus = at;
  } else {
    const pane = makePane(sid);
    if (beside && con.panes.length < MAX_PANES) {
      con.panes.push(pane);
      con.focus = con.panes.length - 1;
    } else if (con.panes.length) {
      silence(con.panes[con.focus]);
      con.panes[con.focus] = pane;
    } else {
      con.panes = [pane];
      con.focus = 0;
    }
    const s = con.sessions.find((x) => x.id === sid);
    if (s && s.program === "chat") {
      const log = con.logs.get(sid) || { events: [], seq: 0, pending: [] };
      con.logs.set(sid, log);
      await readLog(sid, log);
    }
    drawHead(pane);
    drawLog(pane);
  }
  saveHash();
  layout();
  renderList();
}

// a pane that goes stops listening
function silence(pane) { if (pane && pane.voice) pane.voice.close(); }

function closePane(pane) {
  silence(pane);
  con.panes = con.panes.filter((p) => p !== pane);
  con.focus = Math.min(con.focus, Math.max(0, con.panes.length - 1));
  saveHash();
  layout();
  renderList();
}

// which cell each pane and line takes: two side by side, three or four in a 2x2 (a third one across the bottom)
function cells(n) {
  if (n <= 1) return { panes: ["one"], lines: [] };
  if (n === 2) return { panes: ["left", "right"], lines: ["cols"] };
  return { panes: n === 3 ? ["tl", "tr", "bottom"] : ["tl", "tr", "bl", "br"], lines: ["cols", "rows"] };
}

const pick = $("con-pick");

function layout() {
  const box = $("con-panes");
  const shown = phone() ? con.panes.slice(con.focus, con.focus + 1) : con.panes;
  const plan = cells(shown.length);
  box.textContent = "";
  box.className = "con-panes n" + shown.length;
  if (!shown.length) {
    box.appendChild(pick);
    return;
  }
  shown.forEach((p, i) => {
    p.root.className = "con-pane at-" + plan.panes[i] + (con.panes[con.focus] === p && shown.length > 1 ? " focus" : "");
    box.appendChild(p.root);
  });
  for (const which of plan.lines) {
    const line = el("div", null, "con-split split-" + which);
    line.title = "Drag to resize · double-click to share evenly";
    line.addEventListener("pointerdown", (ev) => dragSplit(ev, which));
    line.addEventListener("dblclick", () => setSplit(which, 0.5, true));
    box.appendChild(line);
  }
  for (const which of ["cols", "rows"]) setSplit(which, remembered("split-" + which, 0.5), false);
}

function setSplit(which, ratio, save) {
  const r = Math.min(0.8, Math.max(0.2, Number(ratio) || 0.5));
  const box = $("con-panes");
  box.style.setProperty(which === "cols" ? "--col-a" : "--row-a", (2 * r).toFixed(3) + "fr");
  box.style.setProperty(which === "cols" ? "--col-b" : "--row-b", (2 * (1 - r)).toFixed(3) + "fr");
  if (save) remember("split-" + which, Math.round(r * 1000) / 1000);
  return r;
}

function dragSplit(ev, which) {
  ev.preventDefault();
  const box = $("con-panes").getBoundingClientRect();
  let last = null;
  const move = (e) => {
    last = which === "cols" ? (e.clientX - box.left) / box.width : (e.clientY - box.top) / box.height;
    setSplit(which, last, false);
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    if (last !== null) setSplit(which, last, true);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

function sideGrip() {
  const grip = $("con-side-grip");
  const main = $("con-main");
  const set = (px, save) => {
    const w = Math.min(520, Math.max(220, Math.round(Number(px) || 300)));
    main.style.setProperty("--side", w + "px");
    if (save) remember("side", w);
  };
  set(remembered("side", 300), false);
  grip.addEventListener("dblclick", () => set(300, true));
  grip.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    const left = $("con-side").getBoundingClientRect().left;
    let last = null;
    const move = (e) => { last = e.clientX - left; set(last, false); };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      if (last !== null) set(last, true);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  });
}

// ---- one socket for every chat --------------------------------------------------------------------------------------

function onEvent(sid, ev) {
  const s = con.sessions.find((x) => x.id === sid);
  if (!s) {
    loadSessions().catch((e) => note(e.message));       // a session this page has not listed yet
    return;
  }
  const c = s.chat || (s.chat = { state: "stopped", pending: [], asks: [], cost: 0 });
  c.asks = c.asks || [];
  if (ev.kind === "status") {
    c.state = ev.state;
    if (!["running", "needs_you"].includes(ev.state)) c.asks = [];
  }
  if (ev.kind === "ask") c.asks = c.asks.concat([{ id: ev.id, name: ev.name, summary: ev.summary,
                                                   for_session: ev.for_session, time: ev.time }]);
  if (ev.kind === "answered") c.asks = c.asks.filter((a) => a.id !== ev.id);
  if ((ev.kind === "text" || ev.kind === "user") && ev.text) c.last_text = ev.text.replace(/\s+/g, " ").slice(0, 200);
  if (ev.kind === "result" && typeof ev.cost === "number") c.cost = (c.cost || 0) + ev.cost;
  c.last_at = ev.time;
  s.running = ["idle", "running", "needs_you"].includes(c.state);
  const log = con.logs.get(sid);
  const panes = con.panes.filter((p) => p.sid === sid);
  if (log) {
    if (ev.seq !== log.seq + 1) {
      readLog(sid, log).then(() => panes.forEach((p) => { drawHead(p); drawLog(p); })).catch((e) => note(e.message));
    } else {
      log.events.push(ev);
      log.seq = ev.seq;
      if (ev.kind === "ask") log.pending = log.pending.concat([ev.id]);
      if (ev.kind === "answered") log.pending = log.pending.filter((x) => x !== ev.id);
      if (ev.kind === "status" && !["running", "needs_you"].includes(ev.state)) log.pending = [];
      for (const p of panes) {
        const atEnd = p.log.scrollHeight - p.log.scrollTop - p.log.clientHeight < 40;
        drawEvent(p, ev, new Set(log.pending));
        if (atEnd) p.log.scrollTop = p.log.scrollHeight;
        if (ev.kind === "mcp" && p.mcp.open) loadMcp(p);     // claude started: what it has now
      }
    }
  }
  renderList();
  panes.forEach(drawHead);
}

function connect() {
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + WS + "/chat");
  con.socket = ws;
  ws.onopen = () => ws.send(JSON.stringify({ token }));
  ws.onmessage = (ev) => {
    let msg = null;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.type === "ready") {
      con.retry = 1000;
      note("");
      // what happened while this page was away
      loadSessions().then(async () => {
        for (const p of con.panes) {
          const log = con.logs.get(p.sid);
          if (log) await readLog(p.sid, log);
          drawHead(p);
          drawLog(p);
        }
      }).catch((e) => note(e.message));
    } else if (msg.type === "event") {
      onEvent(msg.session, msg.event);
    } else if (msg.type === "dropped") {
      ws.close();
    } else if (msg.type === "error") {
      note(typeof msg.detail === "string" ? msg.detail : "The chat's socket closed.");
    }
  };
  ws.onclose = () => {
    con.socket = null;
    setTimeout(start, con.retry);
    con.retry = Math.min(con.retry * 2, 30000);
  };
}

async function start() {
  try {
    if (!con.user) {
      con.user = (await api("GET", "/api/auth/me")).id;
      sideGrip();
    }
    if (await loadSessions()) {
      if (!con.socket) connect();
      loadClaude();
      if (!con.panes.length) {
        const want = location.hash.slice(1).split(",").filter((sid) => con.sessions.some((s) => s.id === sid));
        for (const sid of want.slice(0, MAX_PANES)) await openPane(sid, true);
        if (!want.length) layout();
      }
    } else {
      setTimeout(start, 5000);
    }
  } catch (e) {
    note(e.message);
    setTimeout(start, 5000);
  }
}

// ---- what the page asks ---------------------------------------------------------------------------------------------

$("con-new").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const f = ev.target;
  if (newVoice) newVoice.cancel();
  api("POST", WS + "/sessions", { repo: f.repo.value, program: "chat", task: f.task.value,
                                  chat_settings: newChat.read() })
    .then(async (s) => {
      f.reset();
      $("con-new-box").open = false;
      await loadSessions();
      await openPane(s.id, con.panes.length > 0 && con.panes.length < MAX_PANES && !phone());
    })
    .catch((e) => {
      if (e.message === CLAUDE_FIRST) {
        con.claude = false;
        gateChat();
      } else {
        note(e.message);
      }
    });
});
$("con-new-box").addEventListener("toggle", () => { if ($("con-new-box").open) loadClaude(); });
const newChat = CHAT_SETTINGS.fields({});
$("con-new-settings").appendChild(newChat.box);
// the microphone for the chat's first message (voice.js), where the browser can listen
const newVoice = VOICE.attach($("con-new").task);
if (newVoice) $("con-new-voice").append(newVoice.button, newVoice.line);
$("con-start").addEventListener("click", (ev) => {
  ev.currentTarget.disabled = true;
  api("POST", WS + "/start").catch((e) => note(e.message)).finally(() => {
    $("con-start").disabled = false;
    start();
  });
});
window.matchMedia("(max-width: 640px)").addEventListener("change", () => { layout(); renderList(); });
setInterval(() => { if (!document.hidden && con.socket) loadSessions().catch(() => {}); }, 10000);
setInterval(() => { if (!document.hidden && con.socket) loadCapabilities(); }, 60000);

if (!token) note("Sign in on the ADE page first, then open the console again.");
else start();
