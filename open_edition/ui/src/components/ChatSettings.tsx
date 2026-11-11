import { useState } from "react";
import { Bot, Loader2, Plug } from "lucide-react";
import { Api, type SessionRecord } from "../api";
import { choices, EFFORT_NOTES, MODE_NOTES, type ChatChosen, type Chosen } from "../chatSettings";
import { useStore } from "../store";

/** The three settings as compact choices: what NewSession and a chat's own bar share. */
// `narrow`: each picker cut to a short width below the small breakpoint, its full name on its menu (the chat's row);
// a form whose row wraps leaves it out
export function SettingsFields({ chosen, onChange, disabled, narrow }: {
  chosen: Chosen; onChange: (key: keyof Chosen, value: string) => void; disabled?: boolean; narrow?: boolean;
}) {
  const caps = useStore((s) => s.caps);
  return (
    <>
      {choices(caps, chosen).map(({ key, label, list }) => (
        <select
          key={key}
          aria-label={label}
          title={label}
          disabled={disabled}
          className={`input h-7 w-auto !py-0 text-[12px] ${
            narrow ? "min-w-0 max-w-[76px] sm:max-w-[180px]" : "max-w-[180px]"} ${
            key === "permission_mode" && chosen.permission_mode === "bypassPermissions"
              ? "!border-wait/60 text-wait" : ""}`}
          value={chosen[key] || ""}
          onChange={(e) => onChange(key, e.target.value)}
        >
          {list.map(([v, name]) => <option key={v} value={v}>{name}</option>)}
        </select>
      ))}
    </>
  );
}

/** What the chosen permission mode and effort mean, in a sentence, when it is more than the default. */
export function settingsNote(chosen: Chosen): string {
  return [chosen.permission_mode ? MODE_NOTES[chosen.permission_mode] || "" : "", chosen.effort ? EFFORT_NOTES[chosen.effort] || "" : ""].filter(Boolean).join(" ");
}

/** A chat's bar above its input: its model, effort and permission mode, changed as it runs (a new effort from the
 *  next message), whether it may start the agents' runs (from the next message), and its connectors, with how many
 *  wait for approval. */
export function ChatSettingsBar({ record, held, onConnectors }: { record: SessionRecord; held: number; onConnectors: () => void }) {
  const refresh = useStore((s) => s.refresh);
  const setError = useStore((s) => s.setError);
  const chosen = (record.chat?.chosen || {}) as ChatChosen;
  const [busy, setBusy] = useState(false);
  const change = async (key: keyof ChatChosen, value: string | boolean) => {
    setBusy(true);
    try { await Api.chatSettings(record.id, { [key]: value }); await refresh(); } catch (e) { setError(e); } finally { setBusy(false); }
  };
  const dispatching = !!chosen.may_dispatch;
  const note = [settingsNote(chosen), dispatching ? "This chat may start your agents' runs and wait for them (from its next message): each run's message reaches it as information unless the agent takes instructions from other runs." : ""]
    .filter(Boolean).join(" ");
  return (
    <>
    {/* one line at a phone's width too: each picker cut to a short width there, its full name on
        its menu, and the two buttons their icons alone; from the small breakpoint up the row wraps, as a
        pane beside another may be narrower than a phone */}
    <div className="mx-auto mb-2 flex w-full max-w-[860px] items-center gap-1.5 sm:flex-wrap">
      <SettingsFields chosen={chosen} onChange={change} disabled={busy} narrow />
      {busy && <Loader2 size={13} className="animate-spin text-muted" />}
      <button className={`btn btn-sm ml-auto h-7 ${dispatching ? "btn-primary" : ""}`} aria-pressed={dispatching} disabled={busy}
        aria-label="Agents"
        onClick={() => void change("may_dispatch", !dispatching)}
        title={dispatching ? "It may start your agents' runs: tap to stop that" : "Let this chat start your agents' runs, wait for them and write to them"}>
        <Bot size={12} /> <span className="hidden sm:inline">Agents</span>
      </button>
      <button className={`btn btn-sm h-7 ${held ? "!border-wait/60 text-wait" : ""}`} onClick={onConnectors}
        title="The MCP servers this chat may use" aria-label={`Connectors${held ? `, ${held} to approve` : ""}`}>
        <Plug size={12} /> <span className="hidden sm:inline">Connectors</span>
        {held ? <>{" \u00b7 "}{held}<span className="hidden sm:inline"> to approve</span></> : null}
      </button>
    </div>
    {note && <div className="mx-auto mb-2 w-full max-w-[860px] text-[11.5px] text-muted">{note}</div>}
    </>
  );
}
