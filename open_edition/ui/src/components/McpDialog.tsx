import { useCallback, useEffect, useState, type ReactNode } from "react";
import { CircleAlert, Loader2, Plug, Plus, ShieldCheck } from "lucide-react";
import { Api, type McpDeclared, type McpLive, type McpView } from "../api";
import { Modal } from "./Modal";

// A chat's MCP servers (the agent's mcp.py): a repository's own servers (its .mcp.json) run only once approved here,
// each as it was shown; the workspace's own (claude mcp add) and a Claude plan's claude.ai connectors run as Claude
// Code runs them. A change takes effect from the chat's next message.

const WHERE: Record<string, string> = {
  user: "added for every repository in the workspace", local: "added for this repository",
  claudeai: "a connector of your Claude account (claude.ai)", managed: "set by the workspace", dynamic: "given to this chat",
  project: "a .mcp.json",
};

function where(s: McpDeclared | McpLive): string {
  const files = (s as McpDeclared).files;
  if (Array.isArray(files)) return files.map((f) => (f === "this repository" ? "this repository's .mcp.json" : f)).join(", ");
  return (s.scope && WHERE[s.scope]) || s.scope || "";
}

/** How it runs: the command line or the address, and the names of what it is given, never their values. */
function runs(s: McpDeclared | McpLive): string {
  const names = Array.isArray(s.names) && s.names.length ? s.names.join(", ") : "";
  if (s.kind === "command") return `Runs ${s.line}${names ? ` (with ${names} set)` : ""}`;
  if (s.kind === "http" || s.kind === "sse") return `Connects to ${s.line}${names ? ` (with its ${names} header)` : ""}`;
  return s.line || "";
}

function state(s: McpLive): { text: string; tone: "good" | "warn" | "bad" | "plain" } {
  if (s.removed) return { text: "Removed: it goes with the chat's next message.", tone: "plain" };
  if (s.status === "connected") return { text: `On${s.tools ? `, ${s.tools} ${s.tools === 1 ? "tool" : "tools"}` : ""}`, tone: "good" };
  if (s.status === "failed") return { text: `Failed${s.error ? `: ${s.error}` : ""}`, tone: "bad" };
  if (s.status === "needs-auth") return { text: "Needs its own sign-in, which this page cannot give yet (claude in a terminal can: /mcp).", tone: "warn" };
  if (s.status === "pending") return { text: "Starting…", tone: "plain" };
  if (s.status === "disabled") return { text: "Off", tone: "plain" };
  return { text: s.status || "", tone: "plain" };
}

const TONE = { good: "text-done", warn: "text-wait", bad: "text-fail", plain: "text-muted" };

/** How many of a repository's servers wait for the owner's approval. */
export function heldCount(v: McpView | null): number {
  return (v?.servers || []).filter((s) => !s.approved && !s.conflict).length;
}

export function McpDialog({ session, onClose, onChange }: { session: string; onClose: () => void; onChange?: (v: McpView) => void }) {
  const [view, setView] = useState<McpView | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [adding, setAdding] = useState(false);

  const show = useCallback((v: McpView) => { setView(v); onChange?.(v); }, [onChange]);
  useEffect(() => {
    Api.chatMcp(session).then(show).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [session, show]);

  const act = async (key: string, body: Record<string, unknown>) => {
    setBusy(key); setError("");
    try { show(await Api.chatMcpAct(session, body)); return true; }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); return false; }
    finally { setBusy(""); }
  };

  const live = view?.live || [];
  const declared = new Set((view?.servers || []).map((s) => s.name));
  const rows: ReactNode[] = [];
  for (const s of view?.servers || []) {
    const on = live.find((x) => x.name === s.name);
    const waiting = !s.approved && !s.conflict;
    const text = s.conflict ? "Two .mcp.json files define it differently: it does not run."
      : waiting ? "Not started: it runs in your workspace, with its access to your repositories and sign-ins, only once you approve it."
      : on ? state(on).text : "Approved: it starts with the chat's next message.";
    rows.push(
      <Row key={`d-${s.name}`} s={s} waiting={waiting} text={text} tone={s.conflict ? "bad" : waiting ? "warn" : on ? state(on).tone : "plain"}>
        {waiting && (
          <button className="btn btn-sm btn-primary" disabled={!!busy} onClick={() => act(`a-${s.name}`, { action: "approve", name: s.name, fingerprint: s.fingerprint })}>
            {busy === `a-${s.name}` ? <Loader2 size={12} className="animate-spin" /> : <ShieldCheck size={12} />} Approve
          </button>
        )}
        {s.approved && (
          <button className="btn btn-sm" disabled={!!busy} onClick={() => act(`s-${s.name}`, { action: "stop", name: s.name })}>
            {busy === `s-${s.name}` ? <Loader2 size={12} className="animate-spin" /> : null} Stop using
          </button>
        )}
      </Row>,
    );
  }
  for (const s of live) {
    if (s.scope === "project" && declared.has(s.name)) continue;
    const st = state(s);
    rows.push(
      <Row key={`l-${s.name}-${s.scope}`} s={s} text={st.text} tone={st.tone}>
        {(s.scope === "user" || s.scope === "local") && !s.removed && (
          <button className="btn btn-sm" disabled={!!busy} onClick={() => act(`r-${s.name}`, { action: "remove", name: s.name, scope: s.scope })}>
            {busy === `r-${s.name}` ? <Loader2 size={12} className="animate-spin" /> : null} Remove
          </button>
        )}
      </Row>,
    );
  }

  return (
    <Modal title="Connectors" onClose={onClose} width={680}>
      <div className="space-y-3 text-[12.5px]">
        <p className="text-muted">
          A repository&apos;s own MCP servers (its .mcp.json) run only once you approve them, each as it is shown here; the ones
          you add, and your Claude account&apos;s connectors, run as Claude Code runs them. A change takes effect from the chat&apos;s next message.
        </p>
        {view?.strict && (
          <div className="flex items-start gap-2 rounded-md border border-fail/40 bg-fail/[0.07] px-3 py-2 text-fail">
            <CircleAlert size={14} className="mt-0.5 flex-none" /> No MCP server runs in this chat: {view.strict}. Fix or remove that file.
          </div>
        )}
        {!view && !error && <div className="text-muted"><Loader2 size={13} className="mr-1.5 inline animate-spin" /> Asking the workspace…</div>}
        {view && (rows.length ? <ul className="divide-y divide-line rounded-lg border border-line">{rows}</ul>
          : <div className="text-muted">{view.running ? "This chat has no MCP server." : "No MCP server yet; claude says what it has when it starts, with the chat's next message."}</div>)}
        {view && !view.running && rows.length > 0 && <div className="text-[11.5px] text-faint">Claude is not running in this chat now: the list shows what it had.</div>}
        {error && <div className="text-fail">{error}</div>}
        {adding ? <AddForm busy={busy === "add"} onCancel={() => setAdding(false)} onAdd={async (spec) => { if (await act("add", { action: "add", spec })) setAdding(false); }} />
          : <button className="btn btn-sm" onClick={() => setAdding(true)}><Plus size={12} /> Add a server</button>}
      </div>
    </Modal>
  );
}

function Row({ s, text, tone, waiting, children }: { s: McpDeclared | McpLive; text: string; tone: keyof typeof TONE; waiting?: boolean; children?: ReactNode }) {
  return (
    <li className={`px-3.5 py-3 ${waiting ? "bg-wait/[0.04]" : ""}`}>
      <div className="flex items-start gap-3">
        <Plug size={14} className="mt-0.5 flex-none text-muted" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-medium text-fg">{s.name}</span>
            <span className="text-[11.5px] text-faint">{where(s)}</span>
          </div>
          {runs(s) && <div className="mt-0.5 break-all font-mono text-[11.5px] text-fg-dim">{runs(s)}</div>}
          <div className={`mt-1 text-[12px] ${TONE[tone]}`}>{text}</div>
        </div>
        <div className="flex flex-none gap-1.5">{children}</div>
      </div>
    </li>
  );
}

type Spec = { name: string; scope: string; command?: string; url?: string; transport?: string; lines: string };

/** Claude Code's own `claude mcp add`, from the page: what is typed, secrets included, is not kept once it is added. */
function AddForm({ busy, onAdd, onCancel }: { busy: boolean; onAdd: (spec: Spec) => void; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"command" | "url" | "sse">("command");
  const [what, setWhat] = useState("");
  const [lines, setLines] = useState("");
  const [scope, setScope] = useState("repository");
  const address = kind !== "command";
  const off = { autoCapitalize: "off", autoCorrect: "off", spellCheck: false } as const;
  return (
    <form
      className="space-y-2.5 rounded-lg border border-line-strong p-3.5"
      onSubmit={(e) => {
        e.preventDefault();
        const spec: Spec = { name: name.trim(), scope, lines };
        if (address) { spec.url = what.trim(); if (kind === "sse") spec.transport = "sse"; } else spec.command = what;
        onAdd(spec);
      }}
    >
      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-[1fr_1.3fr]">
        <label className="block"><span className="label mb-1 block">Name</span><input className="input" required placeholder="github" value={name} onChange={(e) => setName(e.target.value)} {...off} /></label>
        <label className="block"><span className="label mb-1 block">It is</span>
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
            <option value="command">a command it runs</option>
            <option value="url">an address it connects to</option>
            <option value="sse">an address, over SSE (an older server)</option>
          </select>
        </label>
      </div>
      <label className="block"><span className="label mb-1 block">{address ? "Address" : "Command line"}</span>
        <input className="input font-mono text-[12px]" required placeholder={address ? "https://example.com/mcp" : "npx -y @modelcontextprotocol/server-github"} value={what} onChange={(e) => setWhat(e.target.value)} {...off} />
      </label>
      <label className="block"><span className="label mb-1 block">{address ? "Headers, one Name: value a line (optional)" : "Environment, one NAME=value a line (optional)"}</span>
        <textarea className="input font-mono text-[12px]" rows={2} value={lines} onChange={(e) => setLines(e.target.value)} {...off} />
      </label>
      <div className="flex flex-wrap items-end gap-2">
        <label className="block"><span className="label mb-1 block">For</span>
          <select className="input h-8 !py-0" value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="repository">this repository</option>
            <option value="workspace">every repository in the workspace</option>
          </select>
        </label>
        <span className="ml-auto" />
        <button type="button" className="btn btn-sm" onClick={onCancel}>Cancel</button>
        <button className="btn btn-sm btn-primary" disabled={busy}>{busy ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />} Add</button>
      </div>
      <p className="text-[11.5px] text-faint">Claude Code keeps it in the workspace; the page shows the names you give it, never their values.</p>
    </form>
  );
}
