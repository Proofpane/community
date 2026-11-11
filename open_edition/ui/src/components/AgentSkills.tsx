import { useState } from "react";
import { BookOpen } from "lucide-react";
import { AgentsApi, type Agent } from "../agents";
import { Confirm } from "./bits";

/** An agent's skills, each a SKILL.md the workspace writes into its worktree (never committed): made by the improve
 *  loop's approved changes; the owner reads each and may take one away. */
export function AgentSkills({ agent, onChanged }: { agent: Agent; onChanged: () => Promise<void> }) {
  const [error, setError] = useState("");
  const skills = agent.skills ?? [];
  if (!skills.length) return null;
  const remove = async (name: string) => {
    setError("");
    try { await AgentsApi.removeSkill(agent.id, name); await onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <div className="mt-2 rounded-md border border-line px-2.5 py-2 text-[12px]" role="group" aria-label={`${agent.name}'s skills`}>
      <div className="flex items-center gap-2"><BookOpen size={12} className="text-muted" /><span className="font-medium">Skills</span></div>
      {error && <div className="mt-1 text-[11.5px] text-fail">{error}</div>}
      <ul className="mt-1 space-y-1">
        {skills.map((s) => (
          <li key={s.name}>
            <details>
              <summary className="flex cursor-pointer items-center gap-2 font-mono text-[11.5px]">
                <span className="min-w-0 flex-1 truncate">pp-{s.name}</span>
                <Confirm label="Take away" again="Take it away?" onConfirm={() => remove(s.name)} className="!h-6 text-[11px]" />
              </summary>
              <pre className="mt-1 max-h-[180px] overflow-auto whitespace-pre-wrap rounded bg-black/20 px-2 py-1 font-mono text-[11px]">{s.content}</pre>
            </details>
          </li>
        ))}
      </ul>
    </div>
  );
}
