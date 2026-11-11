// The layout cases' page (layout.html; tests/test_slash_menu_layout.py): the workspace's chat box (?which=chat, or
// chatbusy while Claude works, or pane in a pane 220 pixels wide as a split leaves it), a linked session's
// conversation (?which=link) or its card (?which=card, whose Instruct the measure taps), each drawn as the app draws it
// but outside the Linked sessions panel (whose keyboard the page cases hold), with fixed data and no server, so that a
// headless browser can measure their rows, and the slash menu's modals, at a phone's width. Never part of the app's
// build.
import "./layoutStubs";
import "./index.css";
import { createRoot } from "react-dom/client";
import type { LinkView, SessionRecord } from "./api";
import { useStore } from "./store";
import { Conversation, LinkCard } from "./components/Channel";
import { ChatPane } from "./components/ChatPane";

const T = Math.floor(Date.now() / 1000);
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
const drawn = which === "link" ? <Conversation l={link} onBack={() => {}} onClose={() => {}} />
  : which === "card" ? <LinkCard l={link} questions={[]} onTicket={() => {}} />
  : which === "chatbusy" ? <ChatPane record={busy} />
  : which === "pane" ? (
    <div style={{ width: 220, display: "flex", flexDirection: "column" }}><ChatPane record={record} /></div>
  )
  : <ChatPane record={record} />;
createRoot(document.getElementById("root")!).render(
  <div style={{ height: "100vh", display: "flex", flexDirection: "column" }}>{drawn}</div>,
);
