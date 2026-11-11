import { useMemo, useState } from "react";
import { Loader2, Plug, Search, Trash2 } from "lucide-react";
import {
  AgentsApi, fromRegistry, serverLine, serverName,
  type Agent, type McpServer, type Proposal, type RegistryRow, type RegistryWay,
} from "../agents";
import { useStore } from "../store";
import { Modal } from "./Modal";

const STATUS_TONE: Record<string, string> = { connected: "text-done", failed: "text-fail", "needs-auth": "text-wait", pending: "text-muted", disabled: "text-faint" };

/** An agent's tools, as its last run found them: each of its own MCP servers with its state, one held because its
 *  tools changed (Accept, after looking), and the servers its runs proposed (Add, which opens the tools with it
 *  filled in, or Dismiss). */
export function AgentTools({ agent, onChanged }: { agent: Agent; onChanged: () => Promise<void> }) {
  const setError = useStore((s) => s.setError);
  const [editing, setEditing] = useState<{ add?: McpServer; proposal?: Proposal } | null>(null);
  const [busy, setBusy] = useState(false);
  const status = new Map((agent.tools.status ?? []).map((s) => [s.name, s]));
  const unavailable = agent.tools.unavailable ?? {};
  const go = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); await onChanged(); } catch (e) { setError(e); } finally { setBusy(false); }
  };
  const offer = (p: Proposal) => {
    const way = p.entry.ways.find((w) => fromRegistry(w, "x"));
    const add = way ? fromRegistry(way, serverName(p.entry.name, agent.mcp.servers.map((s) => s.name))) : null;
    if (add) setEditing({ add, proposal: p });
    else setError(new Error("That server runs in a way an agent cannot be given (only an npm package or an address)."));
  };
  return (
    <div className="mt-2 rounded-md border border-line px-2.5 py-2 text-[12px]" aria-label={`${agent.name}'s tools`} role="group">
      <div className="flex items-center gap-2">
        <Plug size={12} className="text-muted" />
        <span className="font-medium">Tools</span>
        <span className="text-[11.5px] text-faint">
          {agent.mcp.connectors ? "its own servers and your claude.ai connectors" : "its own servers only"}
        </span>
        <button className="btn btn-sm ml-auto !h-6 text-[11px]" onClick={() => setEditing({})} disabled={agent.retired}>Edit</button>
      </div>
      {agent.mcp.servers.length === 0 && (
        <div className="mt-1 text-[11.5px] text-faint">No MCP server of its own: its runs have Claude Code's own tools.</div>
      )}
      <ul className="mt-1 space-y-1">
        {agent.mcp.servers.map((s) => {
          const st = status.get(s.name);
          const held = agent.mcp.held.includes(s.name);
          return (
            <li key={s.name} className="flex flex-wrap items-center gap-x-2">
              <span className="font-mono text-[11.5px]">{s.name}</span>
              <span className="min-w-0 flex-1 truncate text-[11px] text-faint" title={serverLine(s)}>{serverLine(s)}</span>
              {held ? (
                <span className="flex items-center gap-1.5 text-wait">
                  its tools changed since you accepted them
                  <button className="btn btn-sm !h-6 text-[11px]" disabled={busy} onClick={() => void go(() => AgentsApi.acceptTools(agent.id, s.name))}
                    title="You looked at what it gives now: its runs use it again">Accept</button>
                </span>
              ) : unavailable[s.name] ? (
                <span className="text-[11px] text-wait">{unavailable[s.name]}</span>
              ) : st ? (
                <span className={`text-[11px] ${STATUS_TONE[st.status] ?? "text-muted"}`} title={st.error ?? undefined}>
                  {st.status}{st.tools != null ? `, ${st.tools} ${st.tools === 1 ? "tool" : "tools"}` : ""}
                </span>
              ) : <span className="text-[11px] text-faint">not run yet</span>}
            </li>
          );
        })}
      </ul>
      {agent.proposals.map((p) => (
        <div key={p.id} className="mt-1.5 rounded-md border border-wait/50 bg-wait/[0.06] px-2 py-1.5" role="note" aria-label={`Proposed: ${p.name}`}>
          <div><span className="text-wait">A run proposes </span><span className="font-mono">{p.name}</span>{p.why ? `: ${p.why}` : ""}</div>
          {p.entry.description && <div className="text-[11px] text-muted">{p.entry.description}</div>}
          <div className="mt-1 flex gap-1.5">
            <button className="btn btn-sm !h-6 text-[11px]" disabled={busy} onClick={() => offer(p)}>Add</button>
            <button className="btn btn-sm !h-6 text-[11px]" disabled={busy} onClick={() => void go(() => AgentsApi.settleProposal(p.id, "dismissed"))}>Dismiss</button>
          </div>
        </div>
      ))}
      {editing && (
        <ToolsDialog agent={agent} add={editing.add} onClose={() => setEditing(null)}
          onSaved={async () => {
            if (editing.proposal) await AgentsApi.settleProposal(editing.proposal.id, "added").catch(() => {});
            setEditing(null);
            await onChanged();
          }} />
      )}
    </div>
  );
}

type Adding = "registry" | "address" | "package" | "repository";

function lines(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const at = line.search(/[=:]/);
    if (at > 0) out[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return out;
}

/** An agent's servers, changed: its own list, whether it uses the claude.ai connectors, a server added from the
 *  registry (by search) or by hand, and the secrets its servers name, given to the workspace alone. */
export function ToolsDialog({ agent, add, onClose, onSaved }: { agent: Agent; add?: McpServer; onClose: () => void; onSaved: () => Promise<void> }) {
  const [servers, setServers] = useState<McpServer[]>(() => [...agent.mcp.servers, ...(add ? [add] : [])]);
  const [connectors, setConnectors] = useState(agent.mcp.connectors);
  const [adding, setAdding] = useState<Adding | null>(null);
  const [error, setLocalError] = useState("");
  const [saving, setSaving] = useState(false);
  const [secrets, setSecrets] = useState<Record<string, Record<string, string>>>({});
  const names = servers.map((s) => s.name);
  const secretNames = useMemo(() => servers.flatMap((s) => [...(s.secret_headers ?? []), ...(s.secret_env ?? [])].map((n) => [s.name, n] as const)), [servers]);

  const save = async () => {
    setSaving(true);
    setLocalError("");
    try {
      await AgentsApi.update(agent.id, { mcp_servers: servers, connectors });
      const given = Object.fromEntries(Object.entries(secrets).map(([k, v]) => [k, Object.fromEntries(Object.entries(v).filter(([, x]) => x))]).filter(([, v]) => Object.keys(v).length));
      if (Object.keys(given).length) await AgentsApi.putSecrets(agent.id, given);
      await onSaved();
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={`${agent.name}'s tools`} onClose={onClose} width={680}
      footer={
        <div className="flex items-center justify-end gap-2">
          {error && <span className="mr-auto text-[12px] text-fail">{error}</span>}
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" disabled={saving} onClick={() => void save()}>
            {saving ? <Loader2 size={13} className="animate-spin" /> : null} Save
          </button>
        </div>
      }>
      <div className="space-y-3 text-[12.5px]">
        <label className="flex items-start gap-2">
          <input type="checkbox" className="mt-0.5" checked={connectors} onChange={(e) => setConnectors(e.target.checked)} />
          <span>Use your claude.ai connectors (Gmail, Calendar, and the others you connected at claude.ai)
            <span className="block text-[11.5px] text-muted">On: its runs cannot be kept to its own servers alone, so the workspace's own servers are
              held back by name. Off: its runs have its own servers and nothing else. A connector needs Claude Code signed in with a claude.ai plan.</span></span>
        </label>
        <div>
          <div className="mb-1 text-[12px] text-muted">Its servers</div>
          {servers.length === 0 && <div className="text-[12px] text-faint">None.</div>}
          <ul className="space-y-1">
            {servers.map((s) => (
              <li key={s.name} className="flex items-center gap-2 rounded-md border border-line px-2 py-1">
                <span className="font-mono text-[12px]">{s.name}</span>
                <span className="min-w-0 flex-1 truncate text-[11.5px] text-faint">{serverLine(s)}</span>
                <button className="icon-btn !h-6 !w-6" aria-label={`Remove ${s.name}`} title="Remove"
                  onClick={() => setServers((xs) => xs.filter((x) => x.name !== s.name))}><Trash2 size={12} /></button>
              </li>
            ))}
          </ul>
        </div>
        {secretNames.length > 0 && (
          <div>
            <div className="mb-1 text-[12px] text-muted">Secrets: kept on the workspace alone, never in the app; empty leaves one as it is</div>
            <div className="space-y-1">
              {secretNames.map(([server, name]) => (
                <label key={`${server}/${name}`} className="flex items-center gap-2 text-[12px]">
                  <span className="w-[200px] flex-none truncate font-mono text-[11.5px]">{server} · {name}</span>
                  <input type="password" className="input !h-7 flex-1 text-[12px]" autoComplete="off" aria-label={`${server} ${name}`}
                    value={secrets[server]?.[name] ?? ""} onChange={(e) => setSecrets((x) => ({ ...x, [server]: { ...(x[server] ?? {}), [name]: e.target.value } }))} />
                </label>
              ))}
            </div>
          </div>
        )}
        <div>
          <div className="mb-1 flex flex-wrap gap-1.5">
            {(["registry", "address", "package", "repository"] as Adding[]).map((k) => (
              <button key={k} className={`btn btn-sm ${adding === k ? "btn-primary" : ""}`} aria-pressed={adding === k} onClick={() => setAdding(adding === k ? null : k)}>
                {k === "registry" ? "Find in the registry" : k === "address" ? "An address" : k === "package" ? "An npm package" : "One of the repository's"}
              </button>
            ))}
          </div>
          {adding === "registry" && <RegistrySearch taken={names} onPick={(s) => { setServers((xs) => [...xs, s]); setAdding(null); }} />}
          {adding && adding !== "registry" && <ByHand kind={adding} taken={names} onAdd={(s) => { setServers((xs) => [...xs, s]); setAdding(null); }} />}
        </div>
      </div>
    </Modal>
  );
}

function RegistrySearch({ taken, onPick }: { taken: string[]; onPick: (s: McpServer) => void }) {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<RegistryRow[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const find = async (cursor?: string | null) => {
    setBusy(true);
    setError("");
    try {
      const got = await AgentsApi.search(q, cursor);
      setRows((r) => (cursor && r ? [...r, ...got.servers] : got.servers));
      setNext(got.next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const pick = (row: RegistryRow, way: RegistryWay) => {
    const s = fromRegistry(way, serverName(row.name, taken));
    if (s) onPick(s);
  };
  return (
    <div className="space-y-2 rounded-md border border-line p-2">
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void find(); }}>
        <input aria-label="Search the MCP registry" className="input !h-8 flex-1 text-[12.5px]" placeholder="What it should do: notes, a database, a browser"
          value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn btn-sm" disabled={busy || !q.trim()}>{busy ? <Loader2 size={12} className="animate-spin" /> : <Search size={12} />} Search</button>
      </form>
      <div className="text-[11px] text-faint">The official MCP registry is unvetted: anyone can publish there. A package runs as the workspace's user,
        with its files; an address sees only what Claude sends it.</div>
      {error && <div className="text-[12px] text-fail">{error}</div>}
      {rows && rows.length === 0 && <div className="text-[12px] text-faint">Nothing matches.</div>}
      <ul className="space-y-1.5">
        {rows?.map((row) => (
          <li key={row.name + row.version} className="rounded-md border border-line px-2 py-1.5">
            <div className="flex flex-wrap items-baseline gap-x-2"><span className="font-mono text-[12px]">{row.name}</span>
              <span className="text-[11px] text-faint">{row.version}</span>
              {row.repository && <a className="text-[11px] text-accent underline-offset-2 hover:underline" href={row.repository} target="_blank" rel="noreferrer">repository</a>}
            </div>
            {row.description && <div className="text-[11.5px] text-muted">{row.description}</div>}
            <div className="mt-1 flex flex-wrap gap-1.5">
              {row.ways.map((w, i) => (
                w.kind === "other" ? <span key={i} className="text-[11px] text-faint">{w.registry} {w.identifier}: not offered</span> : (
                  <button key={i} className="btn btn-sm !h-6 text-[11px]" onClick={() => pick(row, w)}>
                    Add {w.kind === "package" ? `npm ${w.identifier}@${w.version}` : `${w.transport} ${w.url}`}
                  </button>
                )
              ))}
            </div>
          </li>
        ))}
      </ul>
      {next && <button className="btn btn-sm" disabled={busy} onClick={() => void find(next)}>More</button>}
    </div>
  );
}

function ByHand({ kind, taken, onAdd }: { kind: Exclude<Adding, "registry">; taken: string[]; onAdd: (s: McpServer) => void }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [transport, setTransport] = useState<"http" | "sse">("http");
  const [identifier, setIdentifier] = useState("");
  const [args, setArgs] = useState("");
  const [plain, setPlain] = useState("");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");
  const add = () => {
    const n = name.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(n) || taken.includes(n) || n === "proofpane") { setError("A name of its own: letters, digits, '.', '_' and '-'."); return; }
    const secrets = secret.split(/[\s,]+/).filter(Boolean);
    if (kind === "address") {
      if (!/^https:\/\/\S+$|^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?(\/\S*)?$/.test(url.trim())) { setError("An https address (or http on this machine)."); return; }
      onAdd({ name: n, kind, transport, url: url.trim(), headers: lines(plain), secret_headers: secrets });
    } else if (kind === "package") {
      const m = /^((?:@[a-z0-9][a-z0-9._-]*\/)?[a-z0-9][a-z0-9._-]*)@(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)$/.exec(identifier.trim());
      if (!m) { setError("The package's name and its exact version: @scope/name@1.2.3. A version is pinned, so the code that runs is the code you chose."); return; }
      onAdd({ name: n, kind, registry: "npm", identifier: m[1], version: m[2], args: args.split(/\s+/).filter(Boolean), env: lines(plain), secret_env: secrets });
    } else {
      onAdd({ name: n, kind });
    }
  };
  return (
    <div className="grid gap-2 rounded-md border border-line p-2 sm:grid-cols-2">
      <label className="flex flex-col gap-1 text-[12px] text-muted">Name
        <input className="input !h-8 text-[12.5px]" value={name} onChange={(e) => setName(e.target.value)}
          placeholder={kind === "repository" ? "as its .mcp.json names it" : "notes"} />
      </label>
      {kind === "address" && (<>
        <label className="flex flex-col gap-1 text-[12px] text-muted">Address
          <input className="input !h-8 font-mono text-[12px]" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://mcp.example.com/mcp" />
        </label>
        <label className="flex flex-col gap-1 text-[12px] text-muted">Transport
          <select className="input !h-8 text-[12.5px]" value={transport} onChange={(e) => setTransport(e.target.value as "http" | "sse")}>
            <option value="http">http</option><option value="sse">sse</option>
          </select>
        </label>
      </>)}
      {kind === "package" && (<>
        <label className="flex flex-col gap-1 text-[12px] text-muted">Package, at its version
          <input className="input !h-8 font-mono text-[12px]" value={identifier} onChange={(e) => setIdentifier(e.target.value)} placeholder="@scope/name@1.2.3" />
        </label>
        <label className="flex flex-col gap-1 text-[12px] text-muted">Arguments
          <input className="input !h-8 font-mono text-[12px]" value={args} onChange={(e) => setArgs(e.target.value)} />
        </label>
      </>)}
      {kind !== "repository" && (<>
        <label className="flex flex-col gap-1 text-[12px] text-muted">{kind === "address" ? "Headers" : "Environment"}, NAME=value a line (not secret)
          <textarea className="input min-h-[52px] !py-1.5 font-mono text-[12px]" value={plain} onChange={(e) => setPlain(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-[12px] text-muted">Secret {kind === "address" ? "headers" : "variables"}, their names (values after)
          <input className="input !h-8 font-mono text-[12px]" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder={kind === "address" ? "Authorization" : "API_KEY"} />
        </label>
      </>)}
      {kind === "repository" && (
        <div className="text-[11.5px] text-muted sm:col-span-2">One its repository's .mcp.json declares: approve it once in a chat on that repository
          (the chat's MCP servers), and its runs are given it as it was approved.</div>
      )}
      {error && <div className="text-[12px] text-fail sm:col-span-2">{error}</div>}
      <div className="sm:col-span-2"><button className="btn btn-sm btn-primary" onClick={add}>Add it</button></div>
    </div>
  );
}
