import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Columns2, Loader2, Play, Plus, Square, SquareStack, Terminal, X } from "lucide-react";
import { Group, Panel, Separator } from "react-resizable-panels";
import { Api, terminalLink, uploadFiles, type SessionRecord, type ShellView } from "../api";
import { useStore } from "../store";
import { TerminalPane } from "./TerminalPane";
import { endedHow } from "../ended";

const MAX_SHELLS = 4;     // the workspace's own limit
const TOGETHER_KEY = "pp-shells-together";

/** Shells shown together, as rows of panes: two in a row, three or four in two rows. */
export function shellRows<T>(shells: T[]): T[][] {
  if (shells.length <= 2) return [shells];
  return [shells.slice(0, 2), shells.slice(2)];
}

/** The person's own shells in the session's worktree, beside its agent: to run the tests, start the app, or look at
 *  what the agent did. Up to four, a tab each, or all of them at once side by side (two in a row, three or four in two
 *  rows, each pane resizable); the first visit opens one, as a terminal window opens with a shell, and one that has
 *  ended stays, with what it printed last, until it is opened again or closed. */
export function ShellPane({ rec }: { rec: SessionRecord }) {
  const refresh = useStore((s) => s.refresh);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [active, setActive] = useState<number | null>(null);
  const [together, setTogether] = useState(() => {
    try { return localStorage.getItem(TOGETHER_KEY) === "1"; } catch { return false; }
  });
  const showTogether = (on: boolean) => {
    setTogether(on);
    try { localStorage.setItem(TOGETHER_KEY, on ? "1" : "0"); } catch { /* for this page only */ }
  };
  const shells = useMemo(() => rec.shells || [], [rec.shells]);
  const shown: ShellView | undefined = shells.find((x) => x.n === active) || shells[0];

  const act = useCallback(async (what: string, fn: () => Promise<unknown>) => {
    setBusy(what); setError("");
    try { await fn(); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(""); }
  }, [refresh]);

  const open = useCallback((n?: number) => act(`open-${n ?? "new"}`, async () => {
    const got = await Api.openShell(rec.id, n);
    setActive(got.opened);
  }), [act, rec.id]);

  // the first visit to a session's shells opens one
  const asked = useRef<string | null>(null);
  useEffect(() => {
    if (shells.length || asked.current === rec.id) return;
    asked.current = rec.id;
    void open();
  }, [shells.length, rec.id, open]);

  if (!shown) {
    // opening (the first visit, or asked for); else no shell, by the person's own choice or an error: a way to open one
    const opening = !!busy || asked.current !== rec.id;
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center text-[12.5px] text-muted">
        {opening ? (
          <span><Loader2 size={13} className="mr-1.5 inline animate-spin" /> Opening a shell in the session&apos;s worktree…</span>
        ) : (
          <>
            {error && <span className="text-fail">{error}</span>}
            <button className="btn btn-sm btn-outline" onClick={() => void open()}><Terminal size={13} /> Open a shell</button>
          </>
        )}
      </div>
    );
  }

  const running = shells.filter((x) => x.running).length;
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-8 flex-none items-center gap-1 border-b border-line bg-panel px-2 text-[11.5px]">
        {shells.map((x) => {
          const on = x.n === shown.n;
          return (
            <span key={x.n} className={`group flex h-6 items-center rounded ${on ? "bg-active text-fg" : "text-muted hover:text-fg-dim"}`}>
              <button className="flex h-6 items-center gap-1.5 pl-2 pr-1" onClick={() => setActive(x.n)} title={x.running ? "Running" : endedHow(x.exit_code, "It")}>
                <Terminal size={12} />
                <span className="font-mono">{x.n}</span>
                <span className={`h-1.5 w-1.5 rounded-full ${x.running ? "bg-done" : "bg-faint"}`} />
              </button>
              <button
                className={`mr-0.5 flex h-5 w-5 items-center justify-center rounded hover:bg-hover ${on ? "" : "opacity-0 group-hover:opacity-100 focus-visible:opacity-100"}`}
                title={x.running ? "Hang this shell up and close it" : "Close it"}
                disabled={!!busy}
                onClick={() => act(`close-${x.n}`, () => Api.closeShell(rec.id, x.n))}
              >
                {busy === `close-${x.n}` ? <Loader2 size={11} className="animate-spin" /> : <X size={11} />}
              </button>
            </span>
          );
        })}
        <button
          className="icon-btn !h-6 !w-6"
          title={running >= MAX_SHELLS ? `A session has at most ${MAX_SHELLS} shells` : "Another shell in this worktree"}
          disabled={!!busy || running >= MAX_SHELLS}
          onClick={() => void open()}
        >
          {busy === "open-new" ? <Loader2 size={12} className="animate-spin" /> : <Plus size={13} />}
        </button>
        <span className="ml-2 hidden truncate text-muted @2xl:inline">
          in the worktree{rec.branch && <> on <span className="font-mono text-fg-dim">{rec.branch}</span></>}
        </span>
        {error && <span className="ml-2 min-w-0 truncate text-fail" title={error}>{error}</span>}
        <span className="ml-auto" />
        {shells.length > 1 && (
          <button className="icon-btn !h-6 !w-6" onClick={() => showTogether(!together)} aria-pressed={together}
            title={together ? "One shell at a time" : "All the shells at once, side by side"}>
            {together ? <SquareStack size={13} /> : <Columns2 size={13} />}
          </button>
        )}
        {together && shells.length > 1 ? null : shown.running ? (
          <button className="btn btn-sm" disabled={!!busy} onClick={() => act(`stop-${shown.n}`, () => Api.stopShell(rec.id, shown.n))} title="Hang this shell up">
            {busy === `stop-${shown.n}` ? <Loader2 size={12} className="animate-spin" /> : <Square size={12} />} End
          </button>
        ) : (
          <button className="btn btn-sm btn-outline" disabled={!!busy} onClick={() => void open(shown.n)}>
            {busy === `open-${shown.n}` ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />} Open it again
          </button>
        )}
      </div>
      {together && shells.length > 1 ? (
        <Group orientation="vertical" className="min-h-0 flex-1">
          {shellRows(shells).map((row, r) => (
            <Fragment key={r}>
              {r > 0 && <Separator aria-label="Resize the rows of shells" className="h-px bg-line hover:bg-accent data-[resize-handle-active]:bg-accent" />}
              <Panel minSize="15%">
                <Group orientation="horizontal" className="h-full">
                  {row.map((x, i) => (
                    <Fragment key={x.n}>
                      {i > 0 && <Separator aria-label="Resize the shells side by side" className="w-px bg-line hover:bg-accent data-[resize-handle-active]:bg-accent" />}
                      <Panel minSize="15%">
                        <div className="flex h-full flex-col" onMouseDown={() => setActive(x.n)}>
                          <div className={`flex h-6 flex-none items-center gap-1.5 border-b border-line px-2 text-[11px] ${x.n === shown.n ? "bg-active text-fg" : "bg-panel text-muted"}`}>
                            <Terminal size={11} /> <span className="font-mono">{x.n}</span>
                            <span className={`h-1.5 w-1.5 rounded-full ${x.running ? "bg-done" : "bg-faint"}`} />
                            {!x.running && <span className="truncate">{endedHow(x.exit_code, "It")}</span>}
                            <span className="ml-auto" />
                            {x.running ? (
                              <button className="rounded px-1 hover:bg-hover" disabled={!!busy} title="Hang this shell up"
                                onClick={() => act(`stop-${x.n}`, () => Api.stopShell(rec.id, x.n))}>End</button>
                            ) : (
                              <button className="rounded px-1 hover:bg-hover" disabled={!!busy} onClick={() => void open(x.n)}>Open it again</button>
                            )}
                          </div>
                          <ShellTerminal key={`${rec.id}-${x.n}-${x.started_at}`} session={rec.id} shell={x} focus={x.n === shown.n} />
                        </div>
                      </Panel>
                    </Fragment>
                  ))}
                </Group>
              </Panel>
            </Fragment>
          ))}
        </Group>
      ) : (
        <>
          <ShellTerminal key={`${rec.id}-${shown.n}-${shown.started_at}`} session={rec.id} shell={shown} />
          {!shown.running && (
            <div className="flex-none border-t border-line bg-panel px-4 py-2 text-[12.5px] text-fg-dim">
              {endedHow(shown.exit_code, "The shell")} <span className="text-faint">What it printed last, above.</span>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ShellTerminal({ session, shell, focus = true }: { session: string; shell: ShellView; focus?: boolean }) {
  const refresh = useStore((s) => s.refresh);
  // a link for this shell; each shell that runs in its place is its own terminal (the key above)
  const link = useMemo(() => terminalLink(session, () => void refresh(), shell.n), [session, shell.n, refresh]);
  return (
    <div className={`min-h-0 flex-1 ${shell.running ? "" : "opacity-75"}`}>
      <TerminalPane link={link} focus={focus && shell.running} readOnly={!shell.running} session={session}
        onFiles={shell.running ? (files) => uploadFiles(session, files) : undefined} />
    </div>
  );
}
