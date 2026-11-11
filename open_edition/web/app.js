"use strict";
// Proofpane Community. No framework, no external resources, and no HTML parsing of data: every value from the
// server or the user is written with textContent.

const $ = (id) => document.getElementById(id);
const SECTIONS = ["signin", "register", "verify", "ade", "check", "key", "account", "admin"];
let me = null;
let lastVerify = null;

function stored(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value);
  } catch (e) { return null; }
  return null;
}

async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  const token = stored("pp-open-token");
  if (token) headers.Authorization = "Bearer " + token;
  let res;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) {
    // a browser says only "Load failed" or "Failed to fetch" when a connection drops
    throw new Error("The connection to the ADE dropped before its answer came (" +
                    (e && e.message ? e.message : "no answer") + "). Check the network and try again.");
  }
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) {
    if (res.status === 401) { stored("pp-open-token", null); }
    throw new Error((data && typeof data.detail === "string") ? data.detail : "The request failed (" + res.status + ").");
  }
  return data;
}

function show(id) {
  for (const s of SECTIONS) $(s).hidden = s !== id;
  $("error").hidden = true;
}

function fail(err) {
  $("error").textContent = err && err.message ? err.message : String(err);
  $("error").hidden = false;
}

function cell(row, text, cls) {
  const td = document.createElement("td");
  td.textContent = text === null || text === undefined ? "" : String(text);
  if (cls) td.className = cls;
  row.appendChild(td);
}

function download(name, obj) {
  const blob = new Blob([JSON.stringify(obj, null, 1) + "\n"], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function signedIn(user) {
  // a Claude sign-in another account sent its code to is that account's: nothing of it is read, or said, for this one
  if (claudeCoded && claudeCoded.user !== user.id) forgetClaudeCode();
  me = user;
  $("nav").hidden = false;
  $("nav-admin").hidden = !user.admin;
  route();
}

function route() {
  if (!me) return;
  // the ADE app is the front door; the account pages answer the hashes that name them, as before
  if (!location.hash) { location.replace("/app/"); return; }
  let want = location.hash.slice(1);
  // #ade-connect?repo=...&branch=...&dirs=a,b fills the connect form; it names no secret, and the token is typed
  // into the form by its owner. The fragment never reaches the server.
  if (want.startsWith("ade-connect?")) {
    ade.prefill = new URLSearchParams(want.slice("ade-connect?".length));
    history.replaceState(null, "", location.pathname + "#ade");
    want = "ade";
  }
  const id = ["verify", "ade", "check", "key", "account", "admin"].includes(want) ? want : "verify";
  if (id === "admin" && !me.admin) { show("verify"); return; }
  show(id);
  if (id !== "ade") stopRefresh();
  if (id === "key") loadKey().catch(fail);
  if (id === "admin") loadInvitations().catch(fail);
  if (id === "account") loadAccount().catch(fail);
  if (id === "ade") { loadAde().catch(fail); startApprovals(); startLinks(); loadWorkspace().catch(fail); }
}

async function loadKey() {
  const k = await api("GET", "/api/public-key");
  $("key-id").textContent = k.key;
  $("key-value").value = JSON.stringify(k, null, 1);
}

async function loadInvitations() {
  const data = await api("GET", "/api/admin/invitations");
  const body = $("invite-list").tBodies[0];
  body.textContent = "";
  const when = (t) => (t ? new Date(t * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC" : "");
  for (const inv of data.invitations) {
    const row = body.insertRow();
    cell(row, inv.email); cell(row, inv.created_by); cell(row, when(inv.created_at));
    cell(row, when(inv.expires_at)); cell(row, when(inv.used_at));
  }
}

$("signin-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  try {
    const out = await api("POST", "/api/auth/login", { email: f.email.value, password: f.password.value });
    stored("pp-open-token", out.token);
    f.password.value = "";
    signedIn(out.user);
  } catch (e) { fail(e); }
});

// ---- passkeys (passkeys.py): sign in with one here; the app's Account adds and removes them -----------------------

const passkeysHere = typeof window.PublicKeyCredential !== "undefined" && !!navigator.credentials;

function bytesB64u(buf) {
  let s = "";
  for (const b of new Uint8Array(buf)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function passkeyError(e) {
  // the browser's own refusals come as DOMExceptions with a name, and a message that names nothing useful
  if (e && (e.name === "NotAllowedError" || e.name === "AbortError")) {
    return new Error("The passkey request was cancelled or timed out.");
  }
  return e;
}

// The challenge the passkey signs to sign in: never the server's own, which a linked session could take for a step of
// the owner's (a command, a statement of the encryption keys) were it a step's challenge, but the sha256 of
// "proofpane-confirm/1", a zero byte, "sign-in", a zero byte and the server's bytes, as the server checks it
// (passkeys.derived_challenge) and the app derives it (ui/src/passkey.ts, derivedChallenge). The options are asked for
// naming that purpose: the server refuses them asked for without one, as a page of an older release asked.
async function signInChallenge(server) {
  const head = new TextEncoder().encode("proofpane-confirm/1\0sign-in\0");
  const theirs = keyBytes(server);
  const all = new Uint8Array(head.length + theirs.length);
  all.set(head);
  all.set(theirs, head.length);
  return new Uint8Array(await crypto.subtle.digest("SHA-256", all));
}

$("passkey-signin").hidden = !passkeysHere;
$("passkey-signin").addEventListener("click", () => (async () => {
  const opts = await api("POST", "/api/auth/passkey/options", { purpose: "sign-in" });
  const pk = opts.publicKey;
  const challenge = await signInChallenge(pk.challenge);
  let cred;
  try {
    cred = await navigator.credentials.get({ publicKey: {
      challenge, rpId: pk.rpId, timeout: 300000, userVerification: "required", allowCredentials: [] } });
  } catch (e) { throw passkeyError(e); }
  const r = cred.response;
  const out = await api("POST", "/api/auth/passkey", { challenge_id: opts.challenge_id, credential: {
    id: cred.id, type: cred.type, response: {
      clientDataJSON: bytesB64u(r.clientDataJSON), authenticatorData: bytesB64u(r.authenticatorData),
      signature: bytesB64u(r.signature), userHandle: r.userHandle ? bytesB64u(r.userHandle) : null } } });
  stored("pp-open-token", out.token);
  signedIn(out.user);
})().catch(fail));

async function loadAccount() {
  $("account-email").textContent = me.email;
}

// ---- a one-tap Copy beside each block a person is meant to paste (the pair command, the hook token's setup) ---------

function copyButtons() {
  for (const pre of document.querySelectorAll("pre.copyable")) {
    const row = el("p", null, "row-buttons");
    const note = el("span", null, "small");
    const b = buttonFor("Copy", async () => {
      note.textContent = "";
      try {
        if (!navigator.clipboard) throw new Error("no clipboard");
        await navigator.clipboard.writeText(pre.textContent);
        b.textContent = "Copied";
        setTimeout(() => { b.textContent = "Copy"; }, 2000);
      } catch (e) {
        // refused on a page not served over HTTPS, or by the browser's permission
        note.textContent = "This browser did not allow copying here: select the text and copy it instead.";
      }
    });
    row.append(b, " ", note);
    pre.parentNode.insertBefore(row, pre);
  }
}

copyButtons();

let inviteToken = null;
$("register-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const out = await api("POST", "/api/auth/register", { token: inviteToken, password: ev.target.password.value });
    stored("pp-open-token", out.token);
    ev.target.password.value = "";
    inviteToken = null;
    location.hash = "#verify";
    signedIn(out.user);
  } catch (e) { fail(e); }
});

$("sign-out").addEventListener("click", () => {
  stored("pp-open-token", null);
  me = null;
  forgetClaudeCode();           // the Claude sign-in it sent its code to is the account's: read, and said, no more
  stopRefresh();
  ade.open = null;
  $("nav").hidden = true;
  show("signin");
});

$("verify-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const body = { title: f.title.value, body: f.body.value, diff: f.diff.value };
  for (const k of ["repo", "base_sha", "head_sha"]) if (f[k].value.trim()) body[k] = f[k].value.trim();
  try {
    const out = await api("POST", "/api/verify", body);
    lastVerify = out;
    const d = out.decision || {};
    $("verify-decision").textContent = "proofpane/verify: " + d.verify + " · proofpane/claims: " + d.claims +
      (d.rule_id ? " · rule " + d.rule_id : "") + (d.because && d.because.length ? " · because " + d.because.join(", ") : "");
    const tbody = $("verify-obligations").tBodies[0];
    tbody.textContent = "";
    for (const o of out.manifest.obligations) {
      const row = tbody.insertRow();
      cell(row, o.id); cell(row, o.origin); cell(row, o["class"]);
      cell(row, o.outcome, o.outcome === "pass" ? "pass" : (o.outcome === "fail" ? "fail" : ""));
      cell(row, o.claim && o.claim.text ? o.claim.text : (o.resolver || ""));
    }
    $("verify-digest").textContent = out.digest;
    $("verify-result").hidden = false;
  } catch (e) { fail(e); }
});

$("download-manifest").addEventListener("click", () => lastVerify && download("manifest.json", lastVerify.manifest));
$("download-signature").addEventListener("click", () => lastVerify && download("manifest.sig.json", lastVerify.signature));

$("check-form").kind.addEventListener("change", (ev) => {
  $("check-signature-label").hidden = ev.target.value !== "signature";
});

function readFile(input) {
  return new Promise((resolve, reject) => {
    const file = input.files && input.files[0];
    if (!file) { reject(new Error("Choose a file.")); return; }
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(new Error("The file could not be read."));
    r.readAsText(file);
  });
}

$("check-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  try {
    const text = await readFile(f.file);
    let out;
    if (f.kind.value === "manifest") out = await api("POST", "/api/check/manifest", { text });
    else if (f.kind.value === "chain") out = await api("POST", "/api/check/chain", { text });
    else {
      const sigText = await readFile(f.signature);
      let value, signature;
      try { value = JSON.parse(text); signature = JSON.parse(sigText); } catch (e) { throw new Error("Both files must be JSON."); }
      out = await api("POST", "/api/check/signature", { value, signature });
    }
    $("check-result").textContent = JSON.stringify(out, null, 2);
    $("check-result").hidden = false;
  } catch (e) { fail(e); }
});

$("invite-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const out = await api("POST", "/api/admin/invitations", { email: ev.target.email.value });
    $("invite-link").querySelector("code").textContent = out.link;
    $("invite-link").hidden = false;
    ev.target.email.value = "";
    await loadInvitations();
  } catch (e) { fail(e); }
});

// ---- ADE ------------------------------------------------------------------------------------------------------
// The board of the coding sessions the owner runs themselves. Everything below is written with textContent too.

const ade = { open: null, timer: null, verifying: false };
const REFRESH_MS = 60000;

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = String(text);
  if (cls) e.className = cls;
  return e;
}

function utc(t) { return t ? t.slice(0, 16).replace("T", " ") + " UTC" : ""; }

function claudeLink(url, text) {
  // the one link the board makes from what a repository says: a session's own page on claude.ai, and nothing else
  if (!url || !url.startsWith("https://claude.ai/")) return null;
  const a = el("a", text);
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  return a;
}

function table(headers, rows) {
  const t = el("table");
  const hr = t.createTHead().insertRow();
  for (const h of headers) hr.appendChild(el("th", h));
  const body = t.createTBody();
  for (const r of rows) {
    const row = body.insertRow();
    for (const v of r) {
      if (v instanceof Node) { const td = document.createElement("td"); td.appendChild(v); row.appendChild(td); }
      else if (Array.isArray(v)) cell(row, v[0], v[1]);
      else cell(row, v);
    }
  }
  cards(t);
  const wrap = el("div", null, "scroll");
  wrap.appendChild(t);
  return wrap;
}

function cards(t) {
  // on a narrow screen a row becomes a card, and each cell says which column it is (style.css, .cards)
  t.classList.add("cards");
  const heads = [...t.tHead.rows[0].cells].map((th) => th.textContent);
  for (const row of t.tBodies[0].rows) [...row.cells].forEach((td, i) => { td.dataset.label = heads[i] || ""; });
}

function stopRefresh() {
  if (ade.timer) { clearInterval(ade.timer); ade.timer = null; }
  if (ade.approvalTimer) { clearInterval(ade.approvalTimer); ade.approvalTimer = null; }
}

function unix(t) { return t ? new Date(t * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC" : ""; }

// ---- approvals, read every ten seconds while the ADE is open ----------------------------------------------------

async function loadApprovals() {
  const data = await api("GET", "/api/ade/approvals");
  const list = $("ade-approvals");
  list.textContent = "";
  const pending = data.requests.filter((r) => r.state === "pending");
  const done = data.requests.filter((r) => r.state !== "pending").slice(0, 8);
  ade.pending = pending;
  attention();
  $("ade-approvals-empty").hidden = pending.length > 0;
  for (const r of pending.concat(done)) {
    const li = el("li", null, r.state === "pending" ? "pending" : "");
    li.appendChild(el("div", r.session ? r.token_name + " · session " + r.session : r.token_name, "small"));
    li.appendChild(el("div", r.command, "cmd"));
    if (r.state === "pending") {
      li.appendChild(el("div", "asked " + unix(r.created_at) + " · open until " + unix(r.until), "small"));
      const words = el("input", null, "short");
      words.placeholder = "Your words (optional)";
      const note = el("p", null, "error");
      note.hidden = true;
      const run = (fn) => async (b) => {
        note.hidden = true;
        try { await fn(b); } catch (e) { note.textContent = e && e.message ? e.message : String(e); note.hidden = false; }
      };
      const answer = async (approve) => {
        await api("POST", "/api/ade/approvals/" + encodeURIComponent(r.id), { approve, text: words.value.trim() || null });
        await loadApprovals();
      };
      // approving takes the account's passkey or password, made for this request, which this page cannot ask for: the
      // app does (Approvals); denying takes the sign-in alone, here as there
      const approveThere = el("a", "Approve it in the app", "");
      approveThere.href = "/app/#approvals";
      const box = el("div", null, "row-buttons");
      box.append(words, " ", approveThere, " ", buttonFor("Deny", run(() => answer(false))));
      li.append(box, note);
    } else {
      li.appendChild(el("div", r.state + (r.words ? ": " + r.words : "") + " · asked " + unix(r.created_at), "small"));
    }
    list.appendChild(li);
  }
}

// ---- what needs the owner, in the tab's title and, when they allow it, as a notification ------------------------

const TITLE = "Proofpane Community";
const seen = new Set();
// A notification's words, by what waits: fixed, as the server's push notices are (ade/push.py WORDS), never a command,
// a channel's or a file's name, a linked session's name or a question's words, which stay on the page
const NOTICE_WORDS = {
  approval: ["A command waits for you", "Open Approvals."],
  board: ["A board needs you", "Open the ADE."],
  "link-look": ["A linked session needs a look", "Open Linked sessions."],
  "link-question": ["A question waits for you", "Open Linked sessions to read it."],
  "link-waiting": ["A session waits for you", "Open Linked sessions."],
};
// each notification's tag, made here at random for what it is about: its key, which may hold a name, stays on the page
const tags = new Map();

function tagOf(key) {
  if (!tags.has(key)) {
    tags.set(key, "pp-" + Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, "0")).join(""));
  }
  return tags.get(key);
}

function notice(item) {
  const [title, body] = NOTICE_WORDS[item.kind];
  try {
    new Notification(title, { body: body, tag: tagOf(item.key) });
  } catch (e) {
    // a browser that shows notifications only from a service worker (nearly every mobile one) refuses: none shown here
  }
}

function attention() {
  // pending approvals, and the board's alerts that need a person (silence, an unread change, a STOP)
  const items = (ade.pending || []).map((r) => ({ key: "approval:" + r.id, kind: "approval" }))
    .concat((ade.alerts || []).filter((a) => a.level === "alert").map((a) => ({
      key: [a.kind, a.channel, a.file || ""].join(":"), kind: "board" })))
    .concat(ade.linkItems || []);
  document.title = (items.length ? "(" + items.length + ") " : "") + TITLE;
  const allowed = typeof Notification !== "undefined" && Notification.permission === "granted";
  for (const i of items) {
    if (seen.has(i.key)) continue;
    seen.add(i.key);
    if (allowed && document.hidden) notice(i);
  }
}

function notifyState() {
  const s = typeof Notification === "undefined" ? "this browser has no notifications"
    : ({ granted: "on: new approvals and alerts notify you while this tab is open", denied: "blocked in this browser's settings",
         default: "" })[Notification.permission];
  $("ade-notify").hidden = typeof Notification === "undefined" || Notification.permission !== "default";
  $("ade-notify-state").textContent = s || "";
}

$("ade-notify").addEventListener("click", () => {
  Promise.resolve(Notification.requestPermission()).then(notifyState, notifyState);
});

// ---- push: the same items when no page is open (ade/push.py), through this browser's push service ---------------

function keyBytes(b64) {
  const s = atob(b64.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((b64.length + 3) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

async function pushSubscription() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return undefined;
  const reg = await navigator.serviceWorker.getRegistration("/");
  return reg ? reg.pushManager.getSubscription() : null;
}

async function pushState() {
  const sub = await pushSubscription();
  if (sub === undefined) {
    const phone = /iPhone|iPad/.test(navigator.userAgent) && !navigator.standalone;
    $("ade-push-state").textContent = phone
      ? "On an iPhone, add this page to the Home Screen first (Share, then Add to Home Screen) and open it from there."
      : "This browser has no push notifications.";
    for (const id of ["ade-push-on", "ade-push-test", "ade-push-off"]) $(id).hidden = true;
    return;
  }
  $("ade-push-on").hidden = !!sub;
  $("ade-push-test").hidden = !sub;
  $("ade-push-off").hidden = !sub;
  $("ade-push-state").textContent = sub ? "On for this device: waiting approvals and alerts that need you arrive here." : "";
}

$("ade-push-on").addEventListener("click", () => (async () => {
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("Notifications are not allowed for this site in this browser.");
  const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  await navigator.serviceWorker.ready;
  const info = await api("GET", "/api/ade/push");
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(info.key) });
  const json = sub.toJSON();
  await api("POST", "/api/ade/push/subscribe", { endpoint: json.endpoint, keys: json.keys });
  await pushState();
})().catch(fail));

$("ade-push-test").addEventListener("click", () => (async () => {
  const out = await api("POST", "/api/ade/push/test");
  $("ade-push-state").textContent = "Sent to " + out.sent + " of " + out.devices + " device(s).";
})().catch(fail));

$("ade-push-off").addEventListener("click", () => (async () => {
  const sub = await pushSubscription();
  if (sub) {
    await api("POST", "/api/ade/push/unsubscribe", { endpoint: sub.endpoint });
    await sub.unsubscribe();
  }
  await pushState();
})().catch(fail));

function startApprovals() {
  notifyState();
  pushState().catch(fail);
  if (ade.approvalTimer) clearInterval(ade.approvalTimer);
  loadApprovals().catch(fail);
  ade.approvalTimer = setInterval(() => {
    if (!document.hidden && !$("ade").hidden) loadApprovals().catch(fail);
  }, 10000);
}

// ---- the session channel (ade/link.py): linked sessions, their status and questions ------------------------------
// Read every ten seconds while the ADE is open. What a session posts is data: written with textContent, one event per
// line, every character not shown as itself written as its code point, and the one link made from it is claudeLink's,
// on the link's session_ref. What a session did and its conversation are on the app's page (/app/#channel), which
// shows each command as it stands.

// what the app's page writes as a code point (Channel.tsx HIDDEN): a character that is not shown as itself
const HIDDEN_ALL = /[\p{Cf}\p{Co}\p{Zl}\p{Zp}\u{E0000}-\u{EFFFF}\u{FE00}-\u{FE0D}\u{034F}\u{115F}\u{1160}\u{17B4}\u{17B5}\u{180B}-\u{180F}\u{2800}\u{3164}\u{FFA0}]|(?:^|[\u{FE0E}\u{FE0F}])[\u{FE0E}\u{FE0F}]/gu;
function visible(text, clear) {
  // a text sealed by end-to-end encryption (e2e1.) opens only in the app, in a browser that unlocks the account's keys;
  // one never sealed (`clear`: a checkpoint's) is its own words, whatever it begins with
  if (!clear && String(text).startsWith("e2e1.")) return "Encrypted: read it in the app";
  return String(text).replace(HIDDEN_ALL, (m) => [...m].map((c) =>
    "[U+" + c.codePointAt(0).toString(16).toUpperCase().padStart(4, "0") + "]").join(""));
}

function linkLine(ev) {
  const text = visible(ev.text).replace(/\\/g, "\\\\").replace(/\r/g, "\\r").replace(/\n/g, "\\n");
  return "[" + ev.seq + "] " + utc(ev.time) + " " + ev.kind + (ev.mirror ? " (mirrored: " + ev.mirror + ")" : "") + " " +
    ev.from + ">" + ev.to + ": " + text;
}

// a card's line is short: the whole event is on the app's page
function shortLine(line) { return line.length > 280 ? line.slice(0, 280) + " ..." : line; }

function questionItem(q) {
  const d = el("div");
  d.appendChild(el("div", "Question [" + q.seq + "] from " + q.from + " to " + q.to + ", " + utc(q.time), "small"));
  d.appendChild(el("div", visible(q.text), "line"));
  const box = el("div", null, "row-buttons");
  box.appendChild(buttonFor("Close", async () => {
    await api("POST", "/api/ade/link/questions/" + encodeURIComponent(q.seq) + "/close");
    await loadLinks();
  }));
  d.appendChild(box);
  return d;
}

function linkCard(l, questions) {
  const li = el("li", null, l.silent ? "silent" : "");
  const head = el("div");
  // when it was last heard, by any request it signed; by its heartbeat from a server that gives none
  const heard = l.last_heard_at !== undefined ? l.last_heard_at : l.last_heartbeat_at;
  head.append(el("strong", l.name), " · " + l.kind + " · " +
              (heard ? "last heard " + unix(heard) : "not heard from yet"));
  // how the server says it is heard, so that "silent" keeps its meaning (ade/link.py heard_through):
  // through its hook, at each run of it; through its client alone, whose background heartbeat keeps it heard while its
  // session runs; or through its client alone, only when its session calls it, so that not being heard does not say
  // it stopped. Nothing where the server says nothing, or a word this page does not know
  const ways = { hook: "through its hook",
                 keeper: "through its client alone, which keeps it heard while its session runs",
                 client: "through its client alone, when its session calls it" };
  const through = heard && Object.prototype.hasOwnProperty.call(ways, l.heard_through) ? ways[l.heard_through] : null;
  if (through) head.appendChild(el("span", " · " + through, "small"));
  // a keeper link's silence: its session ended, or its container restarted, which ends its keeper, and it has not
  // called its client since, which would start another
  if (l.silent) head.appendChild(el("span", " · silent for more than two of its " + l.interval_min + "-minute intervals"
                                    + (l.heard_through === "keeper" ? ": its session ended, or its container restarted "
                                       + "and it has not called its client since" : ""), "fail"));
  li.appendChild(head);
  if (l.session_ref) {
    const ref = el("div", null, "small");
    ref.appendChild(claudeLink(l.session_ref, "the session on claude.ai") || document.createTextNode("session " +
                                                                                                    l.session_ref));
    li.appendChild(ref);
  }
  const cp = l.checkpoint && typeof l.checkpoint === "object" ? l.checkpoint : {};
  // a checkpoint's field as it is: text as text, anything else as its JSON, blank text as nothing, each
  // character not shown as itself as its code point; a list's items, where the field is one line
  const said = (v) => (v == null || (typeof v === "string" && !v.trim()) ? ""
    : visible(typeof v === "string" ? v : JSON.stringify(v), true));
  const items = (v) => (Array.isArray(v) ? v.map(said).join(" \u00b7 ") : said(v));
  const nothing = l.checkpoint ? "(its checkpoint says nothing of what it is doing)" : "(no checkpoint yet)";
  li.appendChild(el("div", "doing: " + (said(cp.doing) || nothing)));
  // the newest thing it posted, on the card itself, so a phone shows activity without a tap
  if (l.last_event) li.appendChild(el("div", "latest: " + shortLine(linkLine(l.last_event)), "line"));
  if (items(cp.waiting_on)) li.appendChild(el("div", "waiting on: " + items(cp.waiting_on), "small"));
  if (!Array.isArray(cp.open) && said(cp.open)) li.appendChild(el("div", "open: " + said(cp.open), "small"));
  if (Array.isArray(cp.open) && cp.open.length) {
    const open = el("ul", null, "entries");
    for (const o of cp.open.slice(0, 20)) open.appendChild(el("li", said(o)));
    li.append(el("div", "open:", "small"), open);
    if (cp.open.length > 20) li.appendChild(el("div", "and " + (cp.open.length - 20) + " more", "small"));
  }
  if (l.checkpoint_at) li.appendChild(el("div", "checkpoint version " + l.checkpoint_version + ", " +
                                         unix(l.checkpoint_at), "small"));
  for (const q of questions) li.appendChild(questionItem(q));
  // what it did and its conversation: on the app's page, which shows each command as it stands
  const box = el("div", null, "row-buttons");
  const n = l.events || 0;
  const open = el("a", "In the app: what it did · " + n + (n === 1 ? " event" : " events") + ", and its conversation");
  open.href = "/app/#channel";
  box.appendChild(open);
  li.append(box);
  return li;
}

async function loadLinks() {
  const data = await api("GET", "/api/ade/link/sessions");
  const live = new Set(data.links.map((l) => l.id));
  ade.linkItems = data.links.filter((l) => l.silent).map((l) => ({ key: "link-silent:" + l.id, kind: "link-look" }))
    .concat(data.questions.filter((q) => q.to === "owner" || q.to === "all").map((q) => ({
      key: "link-question:" + q.seq, kind: "link-question" })))
    .concat((data.waitings || []).map((w) => ({ key: "link-waiting:" + w.seq, kind: "link-waiting" })));
  attention();
  const list = $("ade-links");
  list.textContent = "";
  $("ade-links-empty").hidden = data.links.length > 0;
  for (const l of data.links) list.appendChild(linkCard(l, data.questions.filter((q) => q.link_id === l.id)));
  const others = data.questions.filter((q) => !live.has(q.link_id));
  const other = $("ade-link-other-list");
  other.textContent = "";
  for (const q of others) {
    const li = el("li");
    li.appendChild(questionItem(q));
    other.appendChild(li);
  }
  $("ade-link-others").hidden = others.length === 0;
}

function startLinks() {
  if (ade.linkTimer) clearInterval(ade.linkTimer);
  loadLinks().catch(fail);
  ade.linkTimer = setInterval(() => {
    if (!document.hidden && !$("ade").hidden) loadLinks().catch(fail);
  }, 10000);
}

// A token's Last used: when the ADE last took a request made with it, an ask or a poll (ade/approvals.py,
// hook_identity; never its creation, nor a request it refused), else "never called in", and this line while the token
// is live. A hook asks only at a gated command, so a new session's token says so too. One not set up asks nothing, and
// by Claude Code's documentation one that does not start, or exits 1 (Python's exit on a half-written hook), lets the
// command go on, but one that exits 2 (Python's when it cannot open its script) blocks it: hence "can let".
const HOOK_NOT_CALLED_IN = "Its hook has not reached the ADE yet. A hook asks here only at a gated command; one that " +
  "is not set up, or cannot start, can let Claude Code run those commands without asking you.";

async function loadHookTokens() {
  const data = await api("GET", "/api/ade/hook-tokens");
  const body = $("ade-hook-list").tBodies[0];
  body.textContent = "";
  for (const t of data.tokens) {
    const row = body.insertRow();
    cell(row, t.name + (t.revoked_at ? " (revoked " + unix(t.revoked_at) + ")" : ""));
    cell(row, unix(t.created_at));
    const used = el("td", t.last_used_at ? unix(t.last_used_at) : "never called in");
    // none for a revoked token: a hook that still sends it is refused here, and so denies a gated command
    if (!t.last_used_at && !t.revoked_at) used.appendChild(el("div", HOOK_NOT_CALLED_IN, "small"));
    row.appendChild(used);
    const td = document.createElement("td");
    if (!t.revoked_at) {
      td.appendChild(buttonFor("Revoke", async (b) => {
        if (!armed(b, "Click again to revoke")) return;
        await api("DELETE", "/api/ade/hook-tokens/" + encodeURIComponent(t.id));
        await loadHookTokens();
      }));
    }
    row.appendChild(td);
  }
}

$("ade-hooks").addEventListener("toggle", () => { if ($("ade-hooks").open) loadHookTokens().catch(fail); });

$("ade-hook-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const out = await api("POST", "/api/ade/hook-tokens", { name: ev.target.name.value });
    ev.target.reset();
    $("ade-hook-setup").textContent = [
      "mkdir -p ~/.proofpane-ade && umask 077 && printf %s '" + out.token + "' > ~/.proofpane-ade/hook-token",
      "# then add the block in open_edition/ade_hook.settings.json, its env and disableAllHooks included, to the " +
        "repository's .claude/settings.local.json (Claude Code 2.1.139 or later), every path in it absolute, with " +
        "this address in its env:",
      "#   \"PROOFPANE_ADE_URL\": " + JSON.stringify(out.url) + ",",
      "# its interpreter by the real binary's path, which this prints: " +
        "python3 -c 'import os,sys; print(os.path.realpath(sys.executable))'",
      "# it holds only in that folder: put it in each repository and worktree where a session runs " +
        "(ade_link_and_hook.settings.json holds it and the stop's block in one file)",
    ].join("\n");
    $("ade-hook-new").hidden = false;
    await loadHookTokens();
  } catch (e) { fail(e); }
});

function scheduleRefresh() {
  stopRefresh();
  ade.timer = setInterval(() => {
    if (ade.open && !document.hidden && !$("ade").hidden) openBoard(ade.open, true).catch(fail);
  }, REFRESH_MS);
}

async function loadAde() {
  const data = await api("GET", "/api/ade/repos");
  const body = $("ade-repos").tBodies[0];
  body.textContent = "";
  for (const r of data.repos) {
    const row = body.insertRow();
    const name = document.createElement("td");
    name.textContent = (r.source === "local" ? "clone " : "") + r.repo + (r.has_token ? " (token)" : "");
    // a connection that writes no records says so, and what would (the server's words: no token, a clone, or no key)
    if (r.records_note) name.appendChild(el("div", r.records_note, "hint"));
    row.appendChild(name);
    cell(row, r.branch);
    cell(row, r.dirs.length ? r.dirs.join(", ") : "none");
    cell(row, r.verify ? "on" : "off");
    const td = document.createElement("td");
    td.className = "row-buttons";
    const open = el("button", "Open");
    open.type = "button";
    open.addEventListener("click", () => openBoard(r.id).catch(fail));
    const remove = el("button", "Remove", "muted-button");
    remove.type = "button";
    remove.addEventListener("click", () => removeRepo(r, remove).catch(fail));
    td.append(open, " ", remove);
    if (r.verify) {
      const checks = el("button", "Checks", "muted-button");
      checks.type = "button";
      checks.addEventListener("click", () => toggleChecks(r, row));
      td.append(" ", checks);
    }
    row.appendChild(td);
  }
  cards($("ade-repos"));
  $("ade-repos").hidden = data.repos.length === 0;
  $("ade-empty").hidden = data.repos.length > 0;
  $("ade-source-label").hidden = data.local.length === 0;
  $("ade-token-label").hidden = !data.can_store_tokens;
  $("ade-no-token").hidden = data.can_store_tokens;
  if (!data.repos.length) $("ade-connect").open = true;
  if (ade.prefill) applyPrefill();
  if (ade.open && !data.repos.some((r) => r.id === ade.open)) { ade.open = null; $("ade-board").hidden = true; }
  if (ade.open) openBoard(ade.open, true).catch(fail);
  loadProofpane().catch(fail);
}

// ---- the account's pairing with its Proofpane organisation (ade/proofpane.py), for its linked sessions' usage ----
// The page is told the pairing's address, the device's name and id and when it was paired, never its token.

async function loadProofpane() {
  showProofpane(await api("GET", "/api/ade/proofpane"));
}

function showProofpane(data) {
  const p = data.pairing;
  $("ade-proofpane-state").textContent = p
    ? "Paired with " + p.address + " as " + (p.name || "a device") + " (device " + p.device_id + "), since " +
      (p.paired_at || "").slice(0, 16).replace("T", " ") + " UTC. Your linked sessions' usage goes there, " +
      "each reply once."
    : "Not paired: your linked sessions' usage stays with them.";
  const u = data.usage;
  const said = u ? u.sent + " replies' usage sent, " + u.waiting + " waiting, " + u.dropped + " dropped" : "";
  const last = u && u.last_dropped ? " (the last: " + u.last_dropped.reason + ")" : "";
  $("ade-proofpane-usage").textContent = said + last;
  $("ade-proofpane-usage").hidden = !u || u.sent + u.waiting + u.dropped === 0;
  // the addresses this deployment pairs with, which its operator names (OPEN_EDITION_PROOFPANE_ADDRESSES)
  const named = data.addresses || [];
  $("ade-proofpane-addresses").textContent = named.length
    ? "This deployment pairs with " + named.join(", ") + "."
    : "This deployment names no Proofpane to pair with.";
  $("ade-proofpane-form").hidden = !data.can_pair || !named.length;
  $("ade-proofpane-no-key").hidden = data.can_pair;
  $("ade-proofpane-unpair").hidden = !p;
  if ($("ade-proofpane-form").querySelector("button")) {
    $("ade-proofpane-form").querySelector("button").textContent = p ? "Pair again, replacing this pairing" : "Pair";
  }
}

function toggleChecks(r, row) {
  // the checks a commit's CI must pass, in Verify's own form; empty clears them
  const next = row.nextElementSibling;
  if (next && next.classList.contains("checks-row")) { next.remove(); return; }
  const editor = document.createElement("tr");
  editor.className = "checks-row";
  const td = document.createElement("td");
  td.colSpan = 5;
  td.appendChild(el("p", "The checks a commit's CI must pass before Verify calls it passed, as Verify reads them. " +
    "Leave it empty to clear them. For a private repository the token needs Checks and Commit statuses: read.", "small"));
  const area = el("textarea");
  area.rows = 6;
  area.spellcheck = false;
  area.placeholder = JSON.stringify({ expected_checks: [{ name: "test", app: "github-actions" }],
    expected_statuses: [{ context: "ci/build", creator: "ci-bot" }] }, null, 1);
  area.value = r.verify_config ? JSON.stringify(r.verify_config, null, 1) : "";
  const note = el("p", null, "error");
  note.hidden = true;
  const save = buttonFor("Save the checks", async () => {
    note.hidden = true;
    let cfg = null;
    if (area.value.trim()) {
      try { cfg = JSON.parse(area.value); } catch (e) { note.textContent = "That is not JSON."; note.hidden = false; return; }
    }
    try {
      await api("PATCH", "/api/ade/repos/" + encodeURIComponent(r.id), { verify_config: cfg });
    } catch (e) { note.textContent = e && e.message ? e.message : String(e); note.hidden = false; return; }
    await loadAde();
  }, "");
  td.append(area, save, note);
  editor.appendChild(td);
  row.after(editor);
}

async function removeRepo(r, button) {
  // two clicks, not confirm(): a second click within four seconds removes it
  if (!button.dataset.armed) {
    button.dataset.armed = "1";
    button.textContent = "Click again to remove";
    setTimeout(() => { delete button.dataset.armed; button.textContent = "Remove"; }, 4000);
    return;
  }
  await api("DELETE", "/api/ade/repos/" + encodeURIComponent(r.id));
  if (ade.open === r.id) { ade.open = null; $("ade-board").hidden = true; stopRefresh(); }
  await loadAde();
}

async function openBoard(id, quiet) {
  ade.open = id;
  $("ade-board").hidden = false;
  if (!quiet) $("ade-board-meta").textContent = "Reading the branch...";
  const b = await api("GET", "/api/ade/repos/" + encodeURIComponent(id) + "/board");
  if (ade.open !== id) return;
  renderBoard(b);
  // opened by the owner, not by the minute's refresh: bring the conversation into view
  if (!quiet && !$("ade-convo").hidden) $("ade-convo").scrollIntoView({ block: "start" });
  scheduleRefresh();
  const unverified = b.commits.filter((c) => !c.verification).length;
  if (b.connection.verify && unverified && !ade.verifying) verifyAll(id).catch(fail);
}

async function verifyAll(id) {
  // a few commits per request, until none is left or six rounds have run
  ade.verifying = true;
  $("ade-verify-now").disabled = true;
  try {
    for (let round = 0; round < 6; round++) {
      const out = await api("POST", "/api/ade/repos/" + encodeURIComponent(id) + "/verify");
      if (!out.done.length || !out.remaining) break;
    }
  } finally {
    ade.verifying = false;
    $("ade-verify-now").disabled = false;
  }
  if (ade.open === id) await openBoard(id, true);
}

function renderBoard(b) {
  const c = b.connection;
  $("ade-board-title").textContent = (c.source === "local" ? "clone " : "") + c.repo + " · " + c.branch;
  $("ade-board-meta").textContent = "At commit " + b.head.slice(0, 7) + ", read " + utc(b.now) +
    ". It is read again every minute while this page is open.";
  $("ade-verify-now").hidden = !c.verify;
  ade.alerts = b.alerts;
  attention();
  const alerts = $("ade-alerts");
  alerts.textContent = "";
  for (const a of b.alerts) alerts.appendChild(el("li", a.channel + ": " + a.text, a.level));
  if (!b.alerts.length) alerts.appendChild(el("li", "Nothing needs you right now.", "info"));
  const sessions = $("ade-sessions").tBodies[0];
  sessions.textContent = "";
  for (const s of b.sessions) {
    const row = sessions.insertRow();
    const td = document.createElement("td");
    const link = claudeLink(s.url, s.name);
    if (link) td.appendChild(link); else td.textContent = s.name;
    row.appendChild(td);
    cell(row, s.kind); cell(row, utc(s.last_seen)); cell(row, s.commits);
  }
  cards($("ade-sessions"));
  renderConversation(b);
  const box = $("ade-channels");
  box.textContent = "";
  for (const ch of b.channels) box.appendChild(renderChannel(ch, b));
  renderCommits(b);
}

function sigText(sig) {
  if (!sig) return "";
  if (sig.state === "signed") return "signed by " + sig.by + ", " + utc(sig.at);
  if (sig.state === "bad") return "signature does not hold: " + sig.reason;
  return "unsigned";
}

function buttonFor(label, fn, cls) {
  const b = el("button", label, cls || "muted-button");
  b.type = "button";
  // through a promise, so an error thrown before the first await (no words written) is shown, not lost
  b.addEventListener("click", () => Promise.resolve().then(() => fn(b)).catch(fail));
  return b;
}

function armed(b, label) {
  // two clicks, not confirm(): the first arms the button for four seconds
  if (b.dataset.armed) return true;
  const was = b.textContent;
  b.dataset.armed = "1";
  b.textContent = label;
  setTimeout(() => { delete b.dataset.armed; b.textContent = was; }, 4000);
  return false;
}

function renderChannel(ch, board) {
  const id = board.connection.id;
  const writable = board.writable;
  const box = el("div", null, "channel");
  box.appendChild(el("h3", ch.name + (ch.readme && ch.readme.title ? " · " + ch.readme.title : "")));
  if (ch.readme && ch.readme.summary) box.appendChild(el("p", ch.readme.summary, "small"));
  // every action here is one signed commit carrying the owner's words; nothing is sent without them
  let words = null;
  const need = () => {
    const t = words ? words.value.trim() : "";
    if (!t) throw new Error("Write your words first, in the box under " + ch.name + ": they go into the commit.");
    return t;
  };
  const act = async (path, body) => {
    await api("POST", "/api/ade/repos/" + encodeURIComponent(id) + "/" + path, Object.assign({ dir: ch.name }, body));
    await openBoard(id, true);
  };
  // an action's error is shown here, beside the words, not at the foot of a long page; a success redraws the board
  const note = el("p", null, "error");
  note.hidden = true;
  const run = (fn) => async (b) => {
    note.hidden = true;
    try { await fn(b); } catch (e) { note.textContent = e && e.message ? e.message : String(e); note.hidden = false; }
  };
  const button = (label, fn) => buttonFor(label, run(fn));
  if (writable) {
    words = el("input", null, "words");
    words.placeholder = "Your words for the next action here: they go into the commit with your name and the time";
    box.append(words, note);
  }
  if (ch.stop) {
    box.appendChild(el("p", "STOP (" + ch.stop.file + ", " + sigText(ch.stop.signature) + "): " + ch.stop.text, "fail"));
    if (writable) box.appendChild(button("Let them go on", async (b) => {
      if (armed(b, "Click again to remove STOP")) await act("resume", { text: need() });
    }));
  } else if (writable) {
    box.appendChild(button("Stop these sessions", async (b) => {
      if (armed(b, "Click again to write STOP")) await act("stop", { text: need() });
    }));
  }
  if (ch.windows.length || writable) {
    box.appendChild(el("h4", "Windows"));
    const heads = ["File", "Window", "Status", "Written by", "Signature"].concat(writable ? [""] : []);
    box.appendChild(table(heads, ch.windows.map((w) => {
      const row = [
        w.file + (w.name ? " · " + w.name : ""),
        w.start ? utc(w.start) + " to " + utc(w.end) : "",
        [w.status + (w.minutes_left !== undefined ? ", " + w.minutes_left + " min left" : "") +
          (w.problems.length ? ": " + w.problems.join("; ") : ""), w.status === "open" ? "pass" : (w.status === "invalid" ? "fail" : "")],
        [w.writers.map((x) => x.name).join(", ") + (w.writer_problem ? " (" + w.writer_problem + ")" : ""), w.writer_problem ? "fail" : ""],
        [sigText(w.signature), w.signature && w.signature.state === "bad" ? "fail" : ""],
      ];
      if (writable) {
        const cellBox = el("span", null, "row-buttons");
        if (w.status === "open" || w.status === "upcoming") {
          cellBox.append(button("+30 min", () => act("window", { file: w.file, minutes: 30, text: need() })), " ",
            button("Close", () => act("window", { file: w.file, close: true, text: need() })));
        }
        row.push(cellBox);
      }
      return row;
    })));
    if (writable) {
      const form = el("div", null, "row-buttons");
      const name = el("input", null, "short");
      name.placeholder = "New window's name";
      const minutes = el("select");
      for (const m of [15, 30, 60, 120]) { const o = el("option", m + " min"); o.value = String(m); minutes.appendChild(o); }
      minutes.value = "30";
      form.append(name, " ", minutes, " ", button("Open a window now", () =>
        act("window", { name: name.value, minutes: Number(minutes.value), text: need() })));
      box.appendChild(form);
    }
  }
  const open = ch.questions.filter((q) => q.open);
  const closed = ch.questions.filter((q) => !q.open);
  if (ch.questions.length) {
    box.appendChild(el("h4", "Questions"));
    box.appendChild(table(["Id", "Asked", "Question", "Answer or state"], open.concat(closed).map((q) => {
      let state = [q.answer ? q.answer.text : (q.withdrawn ? "withdrawn" : (q.processed ? "processed" : "waiting for an answer")),
        q.open ? "fail" : ""];
      if (writable && q.open) {
        const form = el("span", null, "row-buttons");
        const input = el("input", null, "short");
        input.placeholder = "Your answer to " + q.id;
        form.append(input, " ", button("Answer", () => act("answer", { id: q.id, text: input.value })));
        state = form;
      }
      return [q.id, utc(q.asked_at), q.summary, state];
    })));
    const sigs = Object.entries(ch.answers_signatures || {});
    if (sigs.length) box.appendChild(el("p", sigs.map(([f, sig]) => f + ": " + sigText(sig)).join(" · "), "small"));
  }
  if (ch.instructions.length) {
    box.appendChild(el("h4", "Instructions"));
    box.appendChild(table(["File", "Last change", "Read", "Last section"], ch.instructions.map((i) => [
      i.file,
      i.changed ? utc(i.changed.time) + " · " + i.changed.short + " · " + i.changed.session.name : "",
      [i.unread ? "not read since the change (" + i.unread_minutes + " min)" : (i.read_at ? "read " + utc(i.read_at) : "no log reads it"),
        i.unread ? "fail" : ""],
      i.last_section || "",
    ])));
  }
  for (const log of ch.logs) {
    box.appendChild(el("h4", "Log: " + log.file));
    const facts = [
      log.writer ? "written by " + log.writer : null,
      log.interval_minutes ? "heartbeat every " + log.interval_minutes + " min" : null,
      log.last_line_at ? "last line " + utc(log.last_line_at) : null,
      log.silent_minutes !== null ? "quiet for " + log.silent_minutes + " min" : null,
      log.finished ? "finished" : (log.live ? "the live heartbeat log" : null),
    ].filter(Boolean);
    box.appendChild(el("p", facts.join(" · "), "small"));
    if (log.waiting) box.appendChild(el("p", "Waiting for " + log.waiting.waiting_for + " since " + utc(log.waiting.time) + ": " + log.waiting.text, "fail"));
    // the latest heartbeat once, then the steps; every recent line stays one click away
    const line = (e) => el("li", utc(e.time) + " · " + e.kind + (e.label ? " · " + e.label : "") + " · " +
      (e.text.length > 240 ? e.text.slice(0, 240) + "..." : e.text));
    const beat = log.recent.find((e) => e.kind === "heartbeat");
    const steps = log.recent.filter((e) => e.kind !== "heartbeat").slice(0, 6);
    const list = el("ul", null, "entries");
    for (const e of (beat ? [beat] : []).concat(steps)) list.appendChild(line(e));
    box.appendChild(list);
    if (log.recent.length > steps.length + (beat ? 1 : 0)) {
      const more = el("details", null, "more");
      more.appendChild(el("summary", "All " + log.recent.length + " recent lines"));
      const all = el("ul", null, "entries");
      for (const e of log.recent) all.appendChild(line(e));
      more.appendChild(all);
      box.appendChild(more);
    }
  }
  if (ch.problems.length) box.appendChild(el("p", "Problems: " + ch.problems.join("; "), "fail"));
  return box;
}

const RULES = {
  "all-pass": ["passed", "pass"],
  "advisory-not-vouched": ["passed; advisory not vouched for", "wait"],
  "needs-person": ["needs a person", "wait"],
  "nothing-blocking": ["nothing that can block was checked", "wait"],
  "any-fail": ["failed", "fail"],
};

function verdict(v) {
  // the gate's own rule in words, and what it names; a person needed is not a failure of the change
  if (!v) return ["not verified yet", ""];
  if (v.status !== "verified") return [v.status + ": " + (v.reason || ""), v.status === "error" ? "fail" : ""];
  const [words, cls] = RULES[v.rule] || [v.verify + " / " + v.claims, v.verify === "success" ? "pass" : "fail"];
  const because = (v.because || []).map((b) => b.id + " " + (b.resolver || "claim") + " " + b.outcome +
    (b.reason ? " (" + b.reason + ")" : ""));
  return [words + (because.length ? ": " + because.join("; ") : "") + (v.reason ? " · " + v.reason : ""), cls];
}

function renderCommits(b) {
  $("ade-commits-box").hidden = !b.connection.verify;
  setTimeout(() => cards($("ade-commits")), 0);
  const body = $("ade-commits").tBodies[0];
  body.textContent = "";
  for (const c of b.commits) {
    const row = body.insertRow();
    cell(row, c.short + (c.merge ? " (merge)" : "") + " · " + utc(c.time));
    cell(row, c.session.name);
    const [text, cls] = verdict(c.verification);
    cell(row, text, cls);
    cell(row, c.subject);
    const td = document.createElement("td");
    td.className = "row-buttons";
    if (c.verification && c.verification.status === "verified") {
      const m = el("button", "Manifest");
      m.type = "button";
      m.addEventListener("click", () => fetchVerification(b.connection.id, c.sha, "manifest").catch(fail));
      const s = el("button", "Signature");
      s.type = "button";
      s.addEventListener("click", () => fetchVerification(b.connection.id, c.sha, "signature").catch(fail));
      td.append(m, " ", s);
    }
    row.appendChild(td);
  }
}

async function fetchVerification(id, sha, what) {
  const v = await api("GET", "/api/ade/repos/" + encodeURIComponent(id) + "/verifications/" + encodeURIComponent(sha));
  if (what === "manifest") download("manifest-" + sha.slice(0, 7) + ".json", v.manifest);
  else download("manifest-" + sha.slice(0, 7) + ".sig.json", v.signature);
}

// ---- The conversation view ---------------------------------------------------------------------------------------
// The board's two transcripts, the cloud session's and the local one's, laid out like a chat with a coding agent:
// what a session wrote is a message, a read or a window is a tool call that opens, consecutive heartbeats are one
// line, a stop is a card, and the owner's answers are their replies. Rebuilt from the channel branch, not mirrored.

const MSG_LIMIT = 700;

function applyPrefill() {
  const p = ade.prefill;
  ade.prefill = null;
  const f = $("ade-form");
  if (p.get("repo")) f.repo.value = p.get("repo");
  if (p.get("branch")) f.branch.value = p.get("branch");
  if (p.get("dirs")) f.dirs.value = p.get("dirs").split(",").map((d) => d.trim()).filter(Boolean).join("\n");
  f.verify.checked = p.get("verify") === "1";
  $("ade-connect").open = true;
  if (!$("ade-token-label").hidden) f.token.focus();
}

function hhmm(t) { return t ? t.slice(11, 16) + " UTC" : ""; }
function day(t) { return t ? t.slice(5, 10) : ""; }
function base(path) { return path ? path.split("/").pop() : ""; }

function meta(it) {
  return [it.time ? day(it.time) + " " + hhmm(it.time) : (it.at ? it.at + " UTC" : "time unknown"), it.who, base(it.file)]
    .filter(Boolean).join(" · ");
}

function longText(text) {
  const box = el("div", null, "text");
  if (!text || text.length <= MSG_LIMIT) { box.textContent = text || ""; return box; }
  box.textContent = text.slice(0, MSG_LIMIT) + "...";
  const more = el("details", null, "more");
  more.appendChild(el("summary", "Show all " + text.length + " characters"));
  more.appendChild(el("div", text, "text"));
  const wrap = el("div");
  wrap.append(box, more);
  return wrap;
}

function toolItem(name, arg, time, detail) {
  const d = el("details", null, "tool");
  const sum = el("summary");
  sum.append(el("span", null, "mark"), el("span", name, "name"), el("span", arg, "arg"), el("span", hhmm(time), "at"));
  d.appendChild(sum);
  if (detail) d.appendChild(el("pre", detail));
  return d;
}

function renderItem(it) {
  if (it.kind === "message") {
    const m = el("div", null, "msg");
    m.appendChild(el("div", meta(it), "meta"));
    if (it.title) m.appendChild(el("div", it.title, "title"));
    m.appendChild(longText(it.text));
    return m;
  }
  if (it.kind === "tool") return toolItem(it.name, it.arg, it.time, it.detail);
  if (it.kind === "heartbeats") {
    return el("div", "Heartbeat " + (it.count > 1 ? "x" + it.count + " from " + hhmm(it.first) + " to " : "at ") +
      hhmm(it.time) + (it.unread ? " · says an update is unread" : "") + (it.state ? " · " + it.state.slice(0, 140) : ""), "beat");
  }
  if (it.kind === "waiting" || it.kind === "done") {
    const c = el("div", null, "card " + it.kind);
    c.appendChild(el("div", (it.kind === "waiting" ? "Waiting for " + (it.waiting_for || "you") : "Done") + " · " + meta(it), "meta"));
    c.appendChild(longText(it.text));
    return c;
  }
  if (it.kind === "question") {
    const wrap = el("div");
    const c = el("div", null, "card question" + (it.state === "open" ? " open" : ""));
    c.appendChild(el("div", it.id + " · " + (it.for ? "for " + it.for + " · " : "") + meta(it), "meta"));
    c.appendChild(longText(it.text));
    wrap.appendChild(c);
    if (it.answer) {
      const u = el("div", null, "msg user");
      u.appendChild(el("div", it.id + ": " + it.answer, "bubble"));
      wrap.appendChild(u);
    } else {
      wrap.appendChild(el("div", it.state === "open" ? "Waiting for your answer, below." : it.state, "beat"));
    }
    return wrap;
  }
  return el("div", it.text || "", "beat");
}

function liveLog(b) {
  for (const ch of b.channels) for (const log of ch.logs) if (log.live) return log;
  return null;
}

function paneHead(role, b) {
  const head = el("div", null, "pane-head");
  const dot = el("span", null, "dot");
  if (role === "cloud") {
    const cloud = b.sessions.filter((s) => s.kind === "cloud");
    const latest = cloud[0];
    head.append(dot, el("span", latest ? latest.name : "Cloud session", "who"),
      el("span", latest ? "last commit " + day(latest.last_seen) + " " + hhmm(latest.last_seen) : "no commit read", "when"));
    if (latest && latest.last_seen && Date.now() - Date.parse(latest.last_seen) < 3 * 3600 * 1000) dot.classList.add("live");
    const link = claudeLink(latest && latest.url, "Open in claude.ai");
    if (link) head.appendChild(link);
    return head;
  }
  const log = liveLog(b);
  const waiting = b.channels.some((ch) => ch.logs.some((l) => l.waiting));
  const silent = b.alerts.some((a) => a.kind === "silence");
  dot.classList.add(silent ? "quiet" : (waiting ? "wait" : (log ? "live" : "none")));
  head.append(dot, el("span", "Local session", "who"),
    el("span", log ? (silent ? "quiet for " + log.silent_minutes + " min" : "heartbeat " + hhmm(log.last_heartbeat_at) +
      (log.interval_minutes ? ", every " + log.interval_minutes + " min" : "")) : "no heartbeat log", "when"));
  if (log && log.writer) head.appendChild(el("span", "commits as " + log.writer.replace(/^agent session as /, ""), "as"));
  return head;
}

function composer(role, b) {
  const box = el("div", null, "composer");
  if (role === "cloud") {
    box.appendChild(el("span", "Talk to the cloud session in its own page; what it writes to the channel shows here.", "hint"));
    return box;
  }
  const open = [];
  for (const ch of b.channels) for (const q of ch.questions) if (q.open) open.push({ q, dir: ch.name });
  if (!open.length) { box.appendChild(el("span", "No question is waiting for your answer.", "hint")); return box; }
  if (!b.writable) {
    box.appendChild(el("span", open.length + " question(s) wait for you. To answer from here, connect the repository with a token that can write.", "hint"));
    return box;
  }
  const pick = el("select");
  for (const { q, dir } of open) { const o = el("option", q.id); o.value = dir + "\n" + q.id; pick.appendChild(o); }
  const text = el("textarea");
  text.rows = 1;
  text.placeholder = "Reply to the session: your answer goes into a signed commit";
  const note = el("p", null, "error");
  note.hidden = true;
  const send = buttonFor("Send", async () => {
    note.hidden = true;
    const words = text.value.trim();
    if (!words) { note.textContent = "Write your answer first."; note.hidden = false; return; }
    const [dir, id] = pick.value.split("\n");
    try {
      await api("POST", "/api/ade/repos/" + encodeURIComponent(b.connection.id) + "/answer", { dir, id, text: words });
      text.value = "";
      await openBoard(b.connection.id, true);
    } catch (e) { note.textContent = e && e.message ? e.message : String(e); note.hidden = false; }
  }, "send");
  box.append(pick, text, send, note);
  return box;
}

function renderPane(role, pane, b) {
  const box = $("ade-pane-" + role);
  const keep = box.querySelector(".transcript");
  const atBottom = !keep || keep.scrollHeight - keep.scrollTop - keep.clientHeight < 40;
  box.textContent = "";
  box.appendChild(paneHead(role, b));
  const list = el("div", null, "transcript");
  if (pane.earlier) list.appendChild(el("p", pane.earlier + " earlier items are on the Details view and in the files", "earlier"));
  if (!pane.items.length) list.appendChild(el("p", "Nothing from this side of the channel yet.", "earlier"));
  for (const it of pane.items) list.appendChild(renderItem(it));
  box.append(list, composer(role, b));
  // after layout, so the newest item is whole; a refresh does not pull a reader back down
  if (atBottom) requestAnimationFrame(() => { list.scrollTop = list.scrollHeight; });
}

function renderConversation(b) {
  if (!b.conversation) return;
  renderPane("cloud", b.conversation.cloud, b);
  renderPane("local", b.conversation.local, b);
}

for (const button of $("ade-view").querySelectorAll("button")) {
  button.addEventListener("click", () => {
    for (const other of $("ade-view").querySelectorAll("button")) other.classList.toggle("on", other === button);
    $("ade-convo").hidden = button.dataset.view !== "convo";
    $("ade-details").hidden = button.dataset.view !== "details";
  });
}

for (const button of $("ade-pane-switch").querySelectorAll("button")) {
  button.addEventListener("click", () => {
    for (const other of $("ade-pane-switch").querySelectorAll("button")) other.classList.toggle("on", other === button);
    $("ade-pane-cloud").classList.toggle("off", button.dataset.pane !== "cloud");
    $("ade-pane-local").classList.toggle("off", button.dataset.pane !== "local");
  });
}
$("ade-pane-local").classList.add("off");  // on a phone the cloud pane shows first; on a wide screen both do

$("ade-refresh").addEventListener("click", () => { if (ade.open) openBoard(ade.open).catch(fail); });
$("ade-verify-now").addEventListener("click", () => { if (ade.open && !ade.verifying) verifyAll(ade.open).catch(fail); });

$("ade-proofpane-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const body = { code: f.code.value.trim() };
  if (f.address.value.trim()) body.address = f.address.value.trim();
  try {
    showProofpane(await api("POST", "/api/ade/proofpane", body));
    f.code.value = "";
    $("ade-proofpane").open = false;
  } catch (e) { fail(e); }
});

$("ade-proofpane-unpair").addEventListener("click", async (ev) => {
  // two clicks, not confirm(): a second click within four seconds unpairs
  const button = ev.target;
  if (!button.dataset.armed) {
    button.dataset.armed = "1";
    button.textContent = "Click again to unpair";
    setTimeout(() => { delete button.dataset.armed; button.textContent = "Unpair"; }, 4000);
    return;
  }
  delete button.dataset.armed;
  button.textContent = "Unpair";
  try { showProofpane(await api("DELETE", "/api/ade/proofpane")); } catch (e) { fail(e); }
});

$("ade-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const body = {
    source: $("ade-source-label").hidden ? "github" : f.source.value,
    repo: f.repo.value.trim(), branch: f.branch.value.trim(),
    dirs: f.dirs.value.split("\n").map((d) => d.trim()).filter(Boolean), verify: f.verify.checked,
  };
  if (f.token.value.trim()) body.token = f.token.value.trim();
  try {
    const out = await api("POST", "/api/ade/repos", body);
    f.reset();
    $("ade-connect").open = false;
    ade.open = out.id;
    await loadAde();
  } catch (e) { fail(e); }
});

// ---- the hosted workspace: shown only where this deployment turns it on ----------
// A session's terminal opens in its own page, terminal.html, which needs inline style for xterm.js; this page does
// not. Everything here is written with textContent, as elsewhere.

const WS = "/api/ade/workspace";

function wsNote(text) {
  $("ws-note").textContent = text || "";
  $("ws-note").hidden = !text;
}

function wsRun(fn) {
  return async (b) => {
    // given straight to addEventListener it is handed the click, not the button: the steps get the button (taken now,
    // before any wait, while the event still names it), so a button is really disabled and armed() can arm it
    if (typeof Event !== "undefined" && b instanceof Event) b = b.currentTarget;
    wsNote("");
    try { await fn(b); } catch (e) {
      const text = e && e.message ? e.message : String(e);
      if (text === CLAUDE_FIRST) claudeRefused(); else wsNote(text);
    }
  };
}

// A claude session or a chat starts once Claude is signed in on this page, never with Claude Code's own sign-in in
// its terminal, where a phone can neither copy the link nor paste the code. The app
// refuses one with these words; the form says them before it is sent, and a session of that kind says them in its row.
const NEEDS_CLAUDE = ["claude", "chat"];
const CLAUDE_FIRST = "Sign in to Claude first, with Sign in to Claude at the top of the Workspace section.";
let claudeSignedIn = null;      // null while it is not known: the workspace is stopped, or its answer has not come
let claudeHintEls = [];

function claudeFirst(where) {
  where.textContent = CLAUDE_FIRST + " ";
  const go = el("button", "Go to Sign in to Claude", "muted-button");
  go.type = "button";
  go.addEventListener("click", () => {
    $("ws-claude").scrollIntoView({ block: "start", behavior: "smooth" });
    if (!$("ws-claude-signin").hidden) $("ws-claude-signin").focus();
  });
  where.append(go);
  where.hidden = false;
}

function claudeHints() {
  for (const hint of claudeHintEls) {
    hint.textContent = claudeSignedIn === false ? " · Claude is not signed in: sign in above first" : "";
  }
}

function gateNewSession() {
  const blocked = NEEDS_CLAUDE.includes($("ws-new").program.value) && claudeSignedIn === false;
  $("ws-new-submit").disabled = blocked;
  if (blocked) {
    claudeFirst($("ws-new-note"));
  } else {
    $("ws-new-note").textContent = "";
    $("ws-new-note").hidden = true;
  }
}

function claudeRefused() {
  claudeSignedIn = false;
  claudeHints();
  gateNewSession();
  claudeFirst($("ws-note"));
  $("ws-claude").scrollIntoView({ block: "start", behavior: "smooth" });
}

let wsStartWatch = null;

async function loadWorkspace() {
  let st;
  try { st = await api("GET", WS); } catch (e) { $("ade-workspace").hidden = true; return; }
  $("ade-workspace").hidden = !st.enabled;
  if (!st.enabled) return;
  const allowed = st.allowed !== false;
  $("ws-state").textContent = !allowed ? (st.detail || "Not for this account.") :
    (st.running ? "The workspace is running." : "The workspace is stopped; its files stay.");
  // the month's spending on workspace machines and the cap, which the fly provider shows an admin
  const sp = st.spending;
  $("ws-spend").hidden = !sp;
  if (sp) {
    $("ws-spend").textContent = "Workspace machines, " + sp.month + ": $" + sp.spent_usd.toFixed(2) + " of the $" +
      sp.cap_usd.toFixed(2) + " cap (" + sp.running_hours.toFixed(1) + " running hours at $" + sp.usd_per_hour +
      " an hour, and $" + sp.storage_usd.toFixed(2) + " of storage). Measured " + sp.measured + ".";
  }
  // a start goes on in the workspace's own time (a new image takes a minute or two): say so, and read the state again
  // every few seconds until it runs or says why it did not
  if (st.starting) {
    $("ws-state").textContent = "Starting... A workspace whose image changed takes a minute or two.";
    if (!wsStartWatch) wsStartWatch = setInterval(() => { if (!document.hidden) loadWorkspace().catch(fail); }, 3000);
  } else if (wsStartWatch) {
    clearInterval(wsStartWatch);
    wsStartWatch = null;
  }
  if (st.start_error) wsNote("The start did not finish: " + st.start_error);
  $("ws-start").hidden = !allowed || st.running || !!st.starting;
  $("ws-stop").hidden = !allowed || !st.running;
  $("ws-refresh").hidden = !allowed || !st.running;
  $("ws-body").hidden = !allowed || !st.running;
  $("ws-claude").hidden = !allowed;
  if (!st.running) {
    claudeSignedIn = null;
    $("ws-claude-state").textContent = "Claude: start the workspace below, then sign in to Claude here.";
    for (const id of ["ws-claude-signin", "ws-claude-signout", "ws-claude-box", "ws-claude-done"]) $(id).hidden = true;
  }
  if (!allowed) { $("ws-finished").hidden = true; return; }
  if (st.running) {
    await loadWorkspaceBody();
    loadGitHub();      // gh asks GitHub, which can take a few seconds: the rest of the panel does not wait for it
    loadClaude();
  }
  await loadFinishes();
}

// GitHub, as gh in the workspace sees it: whom it is signed in to, and the repositories it can clone with one tap
let ghRepos = [];
let claudeSignedOut = false;
async function loadGitHub() {
  let gh;
  try {
    gh = await api("GET", WS + "/github");
  } catch (e) {
    $("ws-gh-state").textContent = "GitHub: " + e.message;
    return;
  }
  $("ws-gh-state").textContent = !gh.installed ? "gh is not installed in this workspace's image yet." :
    (gh.user ? "Signed in to GitHub as " + gh.user + "." : ghLive ?
      "Signing in: its one-time code shows below once gh has it." : "Not signed in to GitHub.");
  // the end of a sign-in this page watched
  $("ws-gh-done").textContent = gh.user ? "GitHub signed in as " + gh.user + "." : "";
  $("ws-gh-done").hidden = !(gh.user && ghWatched);
  $("ws-gh-signin").hidden = !gh.installed || !!gh.user || ghLive;     // one sign-in at a time
  // one dropdown, most recently pushed first, with a box that narrows it as one types: never a printed list, which an
  // account with many repositories could not use
  ghRepos = gh.repos || [];
  const n = ghRepos.length;
  $("ws-gh-count").textContent = n + (n === 1 ? " repository" : " repositories") + ", most recently pushed first" +
    (gh.capped ? " (the " + n + " most recent: clone any other by its address below)." : ".");
  $("ws-gh-pick").hidden = n === 0;
  showRepos();
}

function repoOption(r) {
  return r.full_name + " · " + (r.private ? "private" : "public") + (r.pushed ? " · pushed " + r.pushed : "") +
    (r.cloned ? " · cloned" : "");
}
function showRepos() {
  const want = $("ws-gh-filter").value.trim().toLowerCase();
  const shown = ghRepos.filter((r) => !want || r.full_name.toLowerCase().includes(want));
  const select = $("ws-gh-select");
  const kept = select.value;
  select.textContent = "";
  for (const r of shown) {
    const o = el("option", repoOption(r));
    o.value = r.url;
    select.appendChild(o);
  }
  if (!shown.length) {
    const o = el("option", "No repository's name has " + JSON.stringify(want));
    o.value = "";
    o.disabled = true;
    select.appendChild(o);
  }
  if (shown.some((r) => r.url === kept)) select.value = kept;
  pickedRepo();
}
function pickedRepo() {
  const r = ghRepos.find((x) => x.url === $("ws-gh-select").value);
  $("ws-gh-clone").disabled = !r || r.cloned;
  $("ws-gh-clone").textContent = r && r.cloned ? "Cloned" : "Clone";
}
$("ws-gh-filter").addEventListener("input", showRepos);
$("ws-gh-select").addEventListener("change", pickedRepo);
$("ws-gh-clone").addEventListener("click", wsRun(async (b) => {
  const r = ghRepos.find((x) => x.url === $("ws-gh-select").value);
  if (!r) return;
  b.disabled = true;
  $("ws-state").textContent = "Cloning " + r.full_name + "...";
  try { await api("POST", WS + "/repos", { url: r.url }); } finally { b.disabled = false; }
  await loadWorkspace();
}));

// Claude Code's own sign-in, the same way: the page it prints to sign in at, as a link, and a box for the code that page
// gives, which the agent types into the sign-in. A link is made only to Claude's own sign-in address.
function claudeSignInLink(url) {
  return typeof url === "string" && (url.startsWith("https://claude.com/cai/oauth/authorize?") ||
                                     url.startsWith("https://claude.ai/oauth/authorize?")) ? url : null;
}

async function loadClaude() {
  let c;
  try {
    c = await api("GET", WS + "/claude");
  } catch (e) {
    $("ws-claude-state").textContent = "Claude: " + e.message;
    claudeSignedIn = null;
    gateNewSession();
    return;
  }
  claudeSignedIn = !!(c.installed && c.signed_in);
  claudeHints();
  gateNewSession();
  loadCapabilities();
  const who = c.email ? " as " + c.email : "";
  $("ws-claude-state").textContent = !c.installed ? "claude is not installed in this workspace's image." :
    (c.signed_in ? "Signed in to Claude" + who + (c.subscription ? " (" + c.subscription + ")" : "") + "." :
      claudeLive ? "Signing in: open the page below, then paste the code it shows." :
      claudeSignedOut ? "Signed out of Claude: Claude Code's sign-in is deleted from this workspace." :
      "Not signed in to Claude.");
  $("ws-claude-signout").hidden = !c.signed_in;
  // the end of a sign-in this page watched
  $("ws-claude-done").textContent = c.signed_in ? "Claude signed in" + who + "." : "";
  $("ws-claude-done").hidden = !(c.signed_in && claudeWatched);
  $("ws-claude-signin").hidden = !c.installed || !!c.signed_in || claudeLive;     // one sign-in at a time
}

// what the installed Claude Code offers a chat (chat-settings.js), and its version beside the Claude line
async function loadCapabilities() {
  let caps;
  try { caps = await api("GET", WS + "/capabilities"); } catch (e) { return; }
  CHAT_SETTINGS.use(caps);
  $("ws-claude-version").textContent = caps && caps.claude_version ? "Claude Code " + caps.claude_version + "." : "";
  loadClaudeUpdate();
}

// Claude Code kept current in the workspace: the mode, a check now, and
// how the last one went, in words
let updateWatch = null;
function updateWords(u) {
  if (u.updating) return "Checking for a newer Claude Code...";
  const mode = u.mode === "automatic" ? "Updates: automatic, at each start and once a day." :
    "Updates: only when you say.";
  const last = u.last;
  if (!last) return mode;
  const when = last.at ? " (" + unix(last.at) + ")" : "";
  if (last.ok && last.changed) return mode + " Updated to " + last.version + when + ".";
  if (last.ok) return mode + " " + (last.version || "It") + " is the newest" + when + ".";
  return mode + " The update" + (last.version ? " to " + last.version : "") + " did not pass its check" + when +
    ": " + last.why + "; " + (u.version ? "Claude Code " + u.version : "the image's Claude Code") + " stays in use.";
}

function showClaudeUpdate(u) {
  $("ws-claude-update").hidden = !u || !u.enabled;
  if (!u || !u.enabled) return;
  $("ws-claude-update-state").textContent = updateWords(u);
  $("ws-claude-update-now").disabled = !!u.updating;
  $("ws-claude-update-mode").textContent = u.mode === "automatic" ? "Only when I say" : "Update automatically";
  $("ws-claude-update-mode").dataset.next = u.mode === "automatic" ? "manual" : "automatic";
  if (u.updating && !updateWatch) {
    updateWatch = setInterval(() => { if (!document.hidden) loadClaudeUpdate(); }, 3000);
  } else if (!u.updating && updateWatch) {
    clearInterval(updateWatch);
    updateWatch = null;
    loadCapabilities();         // a new build: its choices and its version
  }
}

async function loadClaudeUpdate() {
  let u;
  try { u = await api("GET", WS + "/claude/update"); } catch (e) { u = null; }
  showClaudeUpdate(u);
}

// GitHub's sign-in without a terminal: while gh waits, the one-time code it printed is shown with Copy and a link
// to GitHub's page for it, and the sessions are read every few seconds until the sign-in ends; then the panel is read
// again, which says whom it signed in and lists the repositories to clone. Claude's sign-in is watched the same way.
let ghWatch = null;
let ghWatched = false;
let ghLive = false;
let claudeWatched = false;
let claudeLive = false;
let claudeSession = null;
// the Claude sign-in this page sent its code to, while it is the one running: its id, the page's account that sent it
// (forgotten with that account: the sign-out, another's sign-in), its own account, whether that was seen signed in,
// and the read of its sign-in under way (readClaudeSignIn)
let claudeCoded = null;
// GitHub's page for the code: a fixed address, never one read from what gh prints
$("ws-gh-device").href = "https://github.com/login/device";
function showSignIn(sessions) {
  const live = sessions.filter((x) => x.program === "github" && x.running);
  const code = live.map((x) => x.device_code).filter(Boolean).pop() || "";
  $("ws-gh-code").textContent = code;
  $("ws-gh-code-box").hidden = !code;
  ghLive = live.length > 0;
  if (ghLive) $("ws-gh-signin").hidden = true;
  const claude = sessions.filter((x) => x.program === "claude-sign-in" && x.running);
  const link = claudeSignInLink(claude.map((x) => x.sign_in_url).filter(Boolean).pop());
  if (link) $("ws-claude-link").href = link;
  claudeLive = claude.length > 0;
  claudeSession = claudeLive ? claude[claude.length - 1].id : null;
  // the sign-in this page sent its code to: its account's sign-in read at each look while it is the one running, and
  // once more as it is seen ended, or another runs in its place, when it is let go with the line saying its code was
  // sent; one seen signed in shows its page and its code no more, though its program still runs
  const coded = claudeCoded;
  if (coded && coded.id !== claudeSession) {
    claudeCoded = null;
    $("ws-claude-sent").hidden = true;
  }
  if (coded && !coded.signedIn) readClaudeSignIn(coded, coded !== claudeCoded);
  $("ws-claude-box").hidden = !link || !!(claudeCoded && claudeCoded.signedIn);
  if (claudeLive) {
    claudeWatched = true;
    $("ws-claude-signin").hidden = true;
  } else {
    $("ws-claude-sent").hidden = true;
  }
  if (live.length || claude.length) {
    if (live.length) ghWatched = true;
    if (!ghWatch) {
      ghWatch = setInterval(() => {
        if (!document.hidden) api("GET", WS + "/sessions").then((s) => showSignIn(s.sessions)).catch(fail);
      }, 4000);
    }
  } else if (ghWatch) {
    clearInterval(ghWatch);
    ghWatch = null;
    loadWorkspace().catch(fail);
  }
}

// The account of the Claude sign-in this page sent its code to (claudeCoded), its sign-in read as Claude Code's own
// status says it: at each look while that sign-in runs, one read at a time, a look while one is under way joining it;
// and once more as it is seen ended (`last`), whatever is under way then, since a read from before its end can say it
// is not signed in yet. As soon as one says it is signed in, the page says so, its program still running or not, with
// no wait for its end or for the workspace's reload: its page and its code put away, and the Claude line read again.
function readClaudeSignIn(coded, last) {
  if (coded.reading && !last) return;
  const reading = api("GET", WS + "/claude?account=" + encodeURIComponent(coded.account)).then((c) => {
    // not once the account that sent its code has signed out, or another has signed in on the page; not for one whose
    // code was sent since to another sign-in, whose own reads say the rest
    if (!me || me.id !== coded.user || (claudeCoded && claudeCoded !== coded)) return;
    if (coded.signedIn || !c || !c.installed || !c.signed_in) return;
    coded.signedIn = true;
    if (claudeSession === coded.id) $("ws-claude-box").hidden = true;
    $("ws-claude-sent").hidden = true;
    $("ws-claude-done").textContent = "Claude signed in" + (c.email ? " as " + c.email : "") + ".";
    $("ws-claude-done").hidden = false;
    loadClaude();
  }, () => { /* not read just now: the next look reads it again */ });
  const done = () => { if (coded.reading === reading) coded.reading = null; };
  coded.reading = reading;
  reading.then(done, done);
}

// The Claude sign-in this page sent its code to, let go with the page's account that sent it (its sign-out, another
// account's sign-in): nothing of it read again, and nothing of it said to the next account, neither that its code was
// sent nor that it signed in.
function forgetClaudeCode() {
  claudeCoded = null;
  $("ws-claude-sent").hidden = true;
  $("ws-claude-done").hidden = true;
}

async function loadWorkspaceBody() {
  // the repositories and the sessions read each on its own, and each shown as it comes: a GET /repos that is slow or
  // fails keeps neither the sessions (a sign-in's page, its code, its end) from showing, nor the other way round; the
  // first to fail is thrown as it fails, to be said as before
  const repos = api("GET", WS + "/repos").then((got) => {
    const list = $("ws-repos");
    list.textContent = "";
    const select = $("ws-new").repo;
    select.textContent = "";
    for (const r of got.repos) {
      list.appendChild(el("li", r.name + " · " + r.label + (r.branch ? " · " + r.branch : "") +
                                (r.head ? " at " + r.head.slice(0, 7) : "")));
      const o = el("option", r.name);
      o.value = r.name;
      select.appendChild(o);
    }
    if (!got.repos.length) {
      list.appendChild(el("li", "No repository yet: clone one below. A private one needs the GitHub sign-in first."));
      const empty = el("option", "No repository yet: clone one above (a private one needs the GitHub sign-in)");
      empty.disabled = true;
      select.appendChild(empty);
    }
    // a shell may also start with no repository, in the workspace's root
    const none = el("option", "No repository (a shell in the workspace)");
    none.value = "";
    select.appendChild(none);
  });
  const sessions = api("GET", WS + "/sessions").then((got) => {
    showSignIn(got.sessions);
    const body = $("ws-sessions").tBodies[0];
    body.textContent = "";
    claudeHintEls = [];
    for (const x of got.sessions.slice().reverse()) {
      const row = body.insertRow();
      cell(row, x.id + (x.branch ? " · " + x.branch : ""));
      cell(row, x.program === "github" ? "GitHub sign-in" : x.program === "claude-sign-in" ? "Claude sign-in" :
        (x.program === "chat" ? "a chat" : x.program) + (x.repo ? " in " + x.repo : ", no repository"));
      cell(row, x.task.split("\n")[0].slice(0, 200));
      // an ended session keeps its last screen and says so; a stopped one (its program not started since the workspace
      // started) has nothing to show
      const ended = !x.running && x.exit_code !== null && x.exit_code !== undefined;
      const chatWord = x.chat ? { needs_you: "needs you", running: "running", idle: "waiting for you",
                                  stopped: "stopped", failed: "failed" }[x.chat.state] || x.chat.state : null;
      cell(row, chatWord || (x.running ? "running" : !ended ? "stopped" :
        (x.exit_code < 0 ? "ended by signal " + -x.exit_code : "ended with status " + x.exit_code) +
        (x.ended_at ? " at " + unix(x.ended_at) : "")), x.running ? "pass" : "");
      if (NEEDS_CLAUDE.includes(x.program)) {
        const hint = el("span", "", "small");
        row.children[row.children.length - 1].append(hint);
        claudeHintEls.push(hint);
      }
      const tokens = el("td", x.program === "claude" ? "..." : "none recorded", "small");
      row.appendChild(tokens);
      if (x.program === "claude") {
        api("GET", WS + "/sessions/" + encodeURIComponent(x.id) + "/usage").then((u) => {
          tokens.textContent = u.messages ? u.input_tokens + " in, " + u.output_tokens + " out, " +
            (u.cache_read_input_tokens + u.cache_creation_input_tokens) + " cache, over " + u.messages + " replies" :
            "none yet";
        }, () => { tokens.textContent = "unknown"; });
      }
      const td = document.createElement("td");
      td.className = "row-buttons";
      if (x.chat || x.running || ended) {
        // a chat opens in the console; a program's terminal on its own page
        const open = el("a", x.chat ? "Open chat" : x.running ? "Terminal" : "Show what it said");
        open.href = (x.chat ? "console.html#" : "terminal.html#") + x.id;
        open.target = "_blank";
        open.rel = "noopener";
        td.append(open, " ");
      }
      const path = WS + "/sessions/" + encodeURIComponent(x.id);
      if (x.running) {
        td.append(buttonFor("Stop", wsRun(async () => { await api("POST", path + "/stop"); await loadWorkspace(); })), " ");
      } else {
        td.append(buttonFor("Start again", wsRun(async () => { await api("POST", path + "/start", {}); await loadWorkspace(); })), " ");
      }
      // a session with no repository (a sign-in, a shell in the workspace's root) has no change to finish
      if (x.repo) {
        td.append(buttonFor("Finish and verify", wsRun(async (b) => {
          b.disabled = true;
          try { await api("POST", path + "/finish"); } finally { b.disabled = false; }
          await loadWorkspace();
        }), ""), " ");
      }
      td.append(buttonFor("Remove", wsRun(async (b) => {
        if (!armed(b, "Click again: remove its worktree")) return;
        await api("DELETE", path);
        await loadWorkspace();
      })));
      row.appendChild(td);
    }
    claudeHints();
    cards($("ws-sessions"));
  });
  await Promise.all([repos, sessions]);
}

async function loadFinishes() {
  const data = await api("GET", WS + "/finishes");
  $("ws-finished").hidden = !data.finishes.length;
  const body = $("ws-finishes").tBodies[0];
  body.textContent = "";
  for (const f of data.finishes) {
    const row = body.insertRow();
    cell(row, f.session + " · " + unix(f.created_at));
    cell(row, f.base.slice(0, 7) + ".." + f.head.slice(0, 7) + " in " + f.repo);
    const [text, cls] = verdict(f);
    cell(row, text, cls);
    cell(row, f.task.split("\n")[0].slice(0, 200));
    const td = document.createElement("td");
    td.className = "row-buttons";
    if (f.status === "verified") {
      const get = async (what) => {
        const full = await api("GET", WS + "/finishes/" + encodeURIComponent(f.id));
        download("finish-" + f.session + (what === "manifest" ? ".json" : ".sig.json"), full[what]);
      };
      td.append(buttonFor("Manifest", wsRun(() => get("manifest"))), " ", buttonFor("Signature", wsRun(() => get("signature"))));
    }
    row.appendChild(td);
  }
  cards($("ws-finishes"));
}

$("ws-start").addEventListener("click", wsRun(async (b) => {
  b.disabled = true;
  $("ws-state").textContent = "Starting...";
  try {
    await api("POST", WS + "/start");
  } finally {
    b.disabled = false;
    await loadWorkspace().catch(() => {});   // the start goes on even when this request was cut short
  }
}));
$("ws-claude-signout").addEventListener("click", wsRun(async (b) => {
  if (!armed(b, "Click again: sign Claude out of this workspace")) return;
  await api("POST", WS + "/claude/sign-out");
  claudeSignedOut = true;
  claudeWatched = false;
  await loadWorkspace();
}));
$("ws-claude-update-now").addEventListener("click", wsRun(async () => {
  showClaudeUpdate(await api("POST", WS + "/claude/update", { now: true }));
}));
$("ws-claude-update-mode").addEventListener("click", wsRun(async (b) => {
  showClaudeUpdate(await api("POST", WS + "/claude/update", { mode: b.dataset.next }));
}));
$("ws-claude-signin").addEventListener("click", wsRun(async (b) => {
  b.disabled = true;
  try {
    await api("POST", WS + "/sessions", { program: "claude-sign-in", task: "Sign in to Claude" });
  } finally { b.disabled = false; }
  await loadWorkspace();
}));
$("ws-claude-code").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const f = ev.target;
  wsRun(async () => {
    if (!claudeSession) throw new Error("No Claude sign-in is running: start one first.");
    const id = claudeSession;
    const user = me && me.id;
    const sent = await api("POST", WS + "/sessions/" + encodeURIComponent(claudeSession) + "/sign-in-code",
                           { code: f.code.value.trim() });
    f.reset();
    // the page's account that sent it signed out while it was sent, or another signed in: nothing of it is said, or
    // read, for this one (forgetClaudeCode)
    if (!me || me.id !== user) return;
    // its account's sign-in read from the next look on, until it says it is signed in (showSignIn); a code sent again
    // to the same sign-in keeps the read under way
    if (!claudeCoded || claudeCoded.id !== id) {
      claudeCoded = { id, user, account: (sent && sent.account) || "default", signedIn: false, reading: null };
    }
    $("ws-claude-sent").textContent = "Code sent: Claude Code is checking it. This page says when it is signed in.";
    $("ws-claude-sent").hidden = false;
  })();
});
$("ws-gh-signin").addEventListener("click", wsRun(async (b) => {
  b.disabled = true;
  try {
    await api("POST", WS + "/sessions", { program: "github", task: "Sign in to GitHub" });
  } finally { b.disabled = false; }
  await loadWorkspace();
}));
$("ws-stop").addEventListener("click", wsRun(async (b) => {
  if (!armed(b, "Click again: its programs are hung up")) return;
  await api("POST", WS + "/stop");
  await loadWorkspace();
}));
$("ws-refresh").addEventListener("click", wsRun(() => loadWorkspace()));

$("ws-clone").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const f = ev.target;
  wsRun(async () => {
    $("ws-state").textContent = "Cloning...";
    await api("POST", WS + "/repos", { url: f.url.value.trim() });
    f.reset();
    await loadWorkspace();
  })();
});

$("ws-new").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const f = ev.target;
  if (wsVoice) wsVoice.cancel();                 // no late words into the field once it is sent
  wsRun(async () => {
    if (NEEDS_CLAUDE.includes(f.program.value) && claudeSignedIn === false) return gateNewSession();
    const chat = f.program.value === "chat";
    const x = await api("POST", WS + "/sessions", { repo: f.repo.value, program: f.program.value, task: f.task.value,
                                                   chat_settings: chat ? wsChatSettings.read() : undefined });
    f.task.value = "";
    await loadWorkspace();
    if (x && x.id) window.open((x.program === "chat" ? "console.html#" : "terminal.html#") + x.id, "_blank", "noopener");
  })();
});
$("ws-new").program.addEventListener("change", gateNewSession);
// a chat's model, effort and permission mode (chat-settings.js), shown while the program is a chat
const wsChatSettings = CHAT_SETTINGS.fields({});
$("ws-new-settings").appendChild(wsChatSettings.box);
function showChatSettings() { $("ws-new-settings").hidden = $("ws-new").program.value !== "chat"; }
$("ws-new").program.addEventListener("change", showChatSettings);
showChatSettings();
// the microphone for the task (voice.js), where the browser can listen: what is said goes into the field
const wsVoice = VOICE.attach($("ws-new").task);
if (wsVoice) $("ws-new-voice").append(wsVoice.button, wsVoice.line);

window.addEventListener("hashchange", () => { if (me) route(); });

(async function start() {
  const m = /^#invite=([A-Za-z0-9_-]+)$/.exec(location.hash);
  // the ADE app is the front door: an invitation, and a visit that names no page, open there
  if (m || (!location.hash && !stored("pp-open-token"))) { location.replace("/app/" + location.hash); return; }
  if (m) {
    inviteToken = m[1];
    history.replaceState(null, "", location.pathname);  // the token does not stay in the address bar
    try {
      const inv = await api("POST", "/api/auth/invitation", { token: inviteToken });  // in the body, never in a URL
      $("register-email").textContent = inv.email;
      show("register");
    } catch (e) { show("signin"); fail(e); }
    return;
  }
  if (stored("pp-open-token")) {
    try { signedIn(await api("GET", "/api/auth/me")); return; } catch (e) { /* fall through to sign in */ }
  }
  show("signin");
})();
