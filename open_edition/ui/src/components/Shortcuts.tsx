import { Modal } from "./Modal";
import { Kbd } from "./bits";

const MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const CMD = MAC ? "⌘" : "Ctrl+";
const ALT = MAC ? "⌥" : "Alt+";

const GROUPS: [string, [string, string][]][] = [
  ["Anywhere", [
    [`${CMD}K`, "Find a session, a file in it, or a command"],
    [`${CMD}N`, "New session"],
    [`${ALT}1 … ${ALT}9`, "Open the session in that row of the list (hold to see the numbers)"],
    [`${ALT}↑  ${ALT}↓`, "The session above or below"],
    ["Ctrl+`", "The session's shell, in its worktree, or back to its agent (split: from one side to the other)"],
    ["?", "This list"],
  ]],
  ["In a terminal", [
    [MAC ? "⌘F" : "Ctrl+Shift+F", "Find in what it printed"],
    ["Enter  Shift+Enter", "The next match, the one before"],
    ["Esc", "Close the search"],
    ["Paste or drop a file", "Put it in the worktree and type its path"],
  ]],
  ["In a chat", [
    ["Enter", "Send"],
    ["Shift+Enter", "A new line"],
    [`Paste or drop a file`, "Attach it to the message"],
  ]],
  ["Elsewhere", [
    [`${CMD}Enter`, "Start, in New session"],
    ["Click a line number", "Comment on a change, in Changes"],
    [`${CMD}S`, "Save the file, in the editor (Files)"],
    ["Double-click its name", "Rename a session (or Rename in the search)"],
    ["Esc", "Close a window"],
  ]],
];

/** The keyboard shortcuts, in one list. */
export function Shortcuts({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="Keyboard shortcuts" onClose={onClose} width={560}>
      <div className="space-y-5">
        {GROUPS.map(([title, rows]) => (
          <section key={title}>
            <h3 className="label mb-2">{title}</h3>
            <ul className="space-y-1.5">
              {rows.map(([keys, what]) => (
                <li key={keys} className="flex items-center gap-3 text-[12.5px]">
                  <span className="w-44 flex-none"><Kbd>{keys}</Kbd></span>
                  <span className="text-fg-dim">{what}</span>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Modal>
  );
}
