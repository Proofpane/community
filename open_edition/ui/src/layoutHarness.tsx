// The layout cases' page (layout.html; tests/test_slash_menu_layout.py, tests/test_phone_fit_layout.py): the
// workspace's chat box (?which=chat, or chatbusy while Claude works, or pane in a pane 220 pixels wide as a split
// leaves it), a linked session's conversation (?which=link, or linknarrow as a phone's panel draws it) or its card
// (?which=card, whose Instruct the measure taps), the strip that says Claude Code is not signed in (?which=banner, or
// bannerwho naming the account new sessions start on), a change of two files (?which=diff), the Agents panel with two
// runs waiting (?which=agents), a dialog with as long a title as an agent's name gives (?which=modal, or modalword
// with no space in the name), the command palette with the app's longest actions (?which=palette), and the New
// session form, whose Task row the measure gives a pull request (?which=newsession), and a Mac's session in the
// History list, sent in the clear and moved into the workspace (?which=history, or historypanel in the Linked
// sessions panel's 560 pixels at a desktop's width), the sign-in page (?which=signin), the Files panel's code editor
// on a file (?which=editor, whose search panel the measure opens), Linked sessions on linking a session
// (?which=linkform, as the Manager's Link a session opens it), and the keys under a Claude Code session's terminal on a
// desktop (?which=deskkeys, or deskkeyspane in a pane 220 pixels wide), each drawn as
// the app draws it but outside the Linked sessions panel (whose keyboard the page cases hold), with fixed data and no
// server, so that a headless browser can measure their rows, strips, fields and the slash menu's modals at a phone's
// width. Never part of the app's build.
import "./layoutStubs";
import "./index.css";
import { createRoot } from "react-dom/client";
import { CalendarDays, Pencil, Radio } from "lucide-react";
import type { LinkView, SessionRecord } from "./api";
import { useStore } from "./store";
import { Channel, Conversation, LinkCard } from "./components/Channel";
import { CodeEditor } from "./components/CodeEditor";
import { ChatPane } from "./components/ChatPane";
import { ClaudeSignInBanner } from "./components/ClaudeSignInBanner";
import { DiffView } from "./components/DiffView";
import { Agents } from "./components/Agents";
import { Modal } from "./components/Modal";
import { CommandPalette, type PaletteAction } from "./components/CommandPalette";
import { NewSession } from "./components/NewSession";
import { SessionRow } from "./components/History";
import { SignIn } from "./components/SignIn";
import { DeskKeys } from "./components/TerminalPane";
import type { HistorySession } from "./api";

const T = Math.floor(Date.now() / 1000);
// a tool's name as long as the hooks keep one (activity.py's NAME), 80 characters
const TOOL80 = "mcp__the_release_notes_server__write_the_notes_for_every_repository_we_keep_now_";
const link: LinkView = {
  id: "L1", name: "MacBook Pro", kind: "local", session_ref: null, interval_min: 15, created_at: T - 3600,
  last_seen_at: T - 60, last_heartbeat_at: T - 60, silent: false, acked_seq: 0, checkpoint_version: 0,
  checkpoint_at: null, events: 0, last_event: null, commands: [], stopped_at: null, last_heard_at: T - 60,
  // words of some length, and a digest no line can break, for the modals to wrap at a phone's width
  checkpoint: { doing: "reading e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 and its cases",
    next: "the edge cases, then one last check", waiting_on: "the owner's word on the release" },
};
const sessions = { links: [link], questions: [], public_url: "https://pp.example" };
const answers: Record<string, unknown> = {
  "/api/ade/link/sessions": sessions, "/api/ade/link/timeline/L1?limit=100": { events: [] },
  "/api/account/passkeys": { high_security: false,
    passkeys: [{ name: "this MacBook Pro's Touch ID", fingerprint: "a".repeat(32), tier: "device" }] },
  "/api/ade/controller": { controller: { account: "default", state: "on" } },
  // the Agents panel's tree: no agent yet and two runs waiting, so its header counts both (only their number is read)
  "/api/ade/agents": { groups: [], waiting: [{ run_id: "r1" }, { run_id: "r2" }], live: 0, polled_at: null },
  // the New session form's pull requests of its one repository, one of them, for its Task row to name
  "/api/ade/workspace/repos/app/pulls?limit=50": { repo: "app", pulls: [{ number: 1234,
    title: "Make the strips fit a phone", body: "", cut: false, url: null, branch: "phone-fit", base: "main",
    author: "owner", draft: false, updated_at: null }] },
};
window.fetch = async (url: RequestInfo | URL) => {
  const got = answers[String(url)];
  return new Response(JSON.stringify(got ?? { detail: "no route" }), { status: got === undefined ? 404 : 200 });
};
// a chat whose model, effort and permissions are each chosen, so each picker shows a name of some length
const record = {
  id: "0123456789ab", repo: "app", program: "chat", task: "a task", base: "abc", branch: null, created_at: 1,
  finished: null, running: true, exit_code: null, ended_at: null,
  chat: { state: "idle", chosen: { model: "opus", effort: "max", permission_mode: "acceptEdits" } },
} as unknown as SessionRecord;
useStore.setState({ links: sessions as never, chat: { [record.id]: [] }, loadChat: async () => {},
  user: { id: "u1", email: "a-rather-long-address-for-a-phone@proofpane.example" } });
const which = new URLSearchParams(location.search).get("which");
const busy = { ...record, chat: { ...(record.chat as object), state: "running" } } as SessionRecord;
// two files changed, one renamed, with paths longer than a phone's line
const diff = [
  "diff --git a/open_edition/ui/src/components/a-component-with-a-rather-long-name/AndItsHelpers.tsx "
    + "b/open_edition/ui/src/components/a-component-with-a-rather-long-name/AndItsHelpers.tsx",
  "--- a/open_edition/ui/src/components/a-component-with-a-rather-long-name/AndItsHelpers.tsx",
  "+++ b/open_edition/ui/src/components/a-component-with-a-rather-long-name/AndItsHelpers.tsx",
  "@@ -1,2 +1,2 @@", " const kept = 1;", "-const was = 2;", "+const now = 3;",
  "diff --git a/docs/an-old-name-for-a-page.md b/docs/a-new-and-much-longer-name-for-the-same-page.md",
  "similarity index 90%", "rename from docs/an-old-name-for-a-page.md",
  "rename to docs/a-new-and-much-longer-name-for-the-same-page.md",
  "--- a/docs/an-old-name-for-a-page.md", "+++ b/docs/a-new-and-much-longer-name-for-the-same-page.md",
  "@@ -1 +1 @@", "-Old words.", "+New words.", "",
].join("\n");
// the command palette's two longest actions, in App.tsx's own words, and a short one
const actions: PaletteAction[] = [
  { id: "links", icon: <Radio size={14} />, run: () => {},
    label: "Linked sessions: your machines and their Claude Code sessions, by repository, and the sessions linked to "
      + "talk" },
  { id: "calendar", icon: <CalendarDays size={14} />, run: () => {},
    label: "Calendar: your agents' schedules, their coming times and what came of their firings" },
  { id: "rename", icon: <Pencil size={14} />, run: () => {}, label: "Rename this session" },
];
// a Mac's session as the History list draws it, each of its three columns holding something: the machine and the
// program on the left, a pill saying it came in the clear in the middle, and its move into the workspace on the right
const machine = { id: "s1", title: "Make sign-in work", project: "/Users/me/code/demo", branch: "sign-in",
  client: "terminal", live: null, started_at: T - 7200, last_at: T - 3600, link_id: "L1", link: "MacBook Pro",
  kind: "local", listed_at: T - 60, repositories: ["/Users/me/code/demo"] } as HistorySession;
const drawn = which === "banner" ? <ClaudeSignInBanner onSignIn={() => {}} />
  : which === "bannerwho" ? <ClaudeSignInBanner account="Work, the team's own subscription" onSignIn={() => {}} />
  : which === "diff" ? <div className="min-h-0 flex-1 overflow-y-auto"><DiffView diff={diff} /></div>
  : which === "agents" ? <Agents onClose={() => {}} onCalendar={() => {}} />
  : which === "linknarrow" ? <Conversation l={link} onBack={() => {}} onClose={() => {}} narrow />
  : which === "palette" ? <CommandPalette sessions={[]} actions={actions} onPick={() => {}} onClose={() => {}} />
  : which === "history" ? (
    <div className="p-3"><ul className="space-y-1">
      <SessionRow s={{ ...machine, raw: machine, seal: null, inClear: true }} moved="started" />
    </ul></div>
  )
  : which === "historypanel" ? (
    <div style={{ width: 560 }} className="p-3"><ul className="space-y-1">
      <SessionRow s={{ ...machine, raw: machine, seal: null, inClear: true }} moved="started" />
    </ul></div>
  )
  : which === "newsession" ? (
    <NewSession repos={["app"]} githubRepos={["app"]} agents={[{ kind: "claude", available: true }]}
      onStart={() => {}} onClose={() => {}} onClone={() => {}} />
  )
  // the keys under a Claude Code session's terminal on a desktop, with the state line's longest words (a tool's name
  // as long as the hooks keep one, 80 characters, beside the longest name the page gives a session), across the page
  // or in a pane 220 pixels wide as a split leaves it
  : which === "deskkeys" || which === "deskkeyspane" ? (
    <div style={which === "deskkeyspane" ? { width: 220 } : undefined}>
      <DeskKeys onKey={() => {}} onAttach={() => {}}
        agent={{ who: "MiniMax M3", activity: { state: "working", tool: TOOL80, at: T - 5 } }} />
    </div>
  )
  : which === "modalword" ? (
    <Modal title={`${"TheNightlyReleaseCheckerForEveryRepositoryWeKeepAndTheNotesItWritesEachMorningAtSix"
      .slice(0, 80)}'s tools`}
      onClose={() => {}}><p>Its tools.</p></Modal>
  )
  // a dialog's title as long as an agent's name may be (the form's 80 characters) in "<name>'s tools"
  : which === "modal" ? (
    <Modal title={`${"The nightly release checker for every repository we keep, and the notes it writes"
      .slice(0, 80)}'s tools`}
      onClose={() => {}}><p>Its tools.</p></Modal>
  )
  : which === "link" ? <Conversation l={link} onBack={() => {}} onClose={() => {}} />
  : which === "signin" ? <SignIn />
  // a file in the Files panel's code editor, the panel's height its own
  : which === "editor" ? (
    <div className="min-h-0 flex-1">
      <CodeEditor text={"const kept = 1;\nexport function now() {\n  return kept + 2;\n}\n"} path="src/now.ts"
        onDirty={() => {}} onSave={() => {}} handle={{ current: null }} />
    </div>
  )
  : which === "linkform" ? <Channel onClose={() => {}} linking />
  : which === "card" ? <LinkCard l={link} questions={[]} onTicket={() => {}} />
  : which === "chatbusy" ? <ChatPane record={busy} />
  : which === "pane" ? (
    <div style={{ width: 220, display: "flex", flexDirection: "column" }}><ChatPane record={record} /></div>
  )
  : <ChatPane record={record} />;
createRoot(document.getElementById("root")!).render(
  <div style={{ height: "100vh", display: "flex", flexDirection: "column" }}>{drawn}</div>,
);
