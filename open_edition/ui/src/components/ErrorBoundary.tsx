import { Component, type ReactNode } from "react";

/** Whether an error is a part of the app that could not be loaded: after an update, the parts of the build the page
 *  came from are gone from the server, and only loading the page again gets the new ones. */
export function isChunkError(e: unknown): boolean {
  const m = e instanceof Error ? `${e.name} ${e.message}` : String(e);
  return /dynamically imported module|Importing a module script failed|error loading dynamically imported module|ChunkLoadError|Loading chunk/i.test(m);
}

/** A part of the page that failed, shown in its place instead of a blank page: what happened, a way to try again, and a
 *  reload, which is what a part gone after an update needs. `inline` is a line for a small place (a window, a toast).
 *  `onClose`, where given, closes the part that failed: a window that would fail again on Try again (one drawing the
 *  same data) can be put away and the rest of the page kept. */
export class ErrorBoundary extends Component<{ children: ReactNode; what?: string; inline?: boolean; onClose?: () => void },
  { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error(error);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const gone = isChunkError(error);
    const said = gone
      ? `${this.props.what || "This part of the page"} could not load: the app may have been updated since the page was opened.`
      : `${this.props.what || "This part of the page"} stopped on an error: ${error.message || String(error)}`;
    const actions = (
      <span className="flex flex-none gap-2">
        {!gone && <button className="btn btn-sm" onClick={() => this.setState({ error: null })}>Try again</button>}
        {this.props.onClose && (
          <button className="btn btn-sm" onClick={() => { this.setState({ error: null }); this.props.onClose?.(); }}>Close</button>
        )}
        <button className="btn btn-sm btn-primary" onClick={() => location.reload()}>Reload the page</button>
      </span>
    );
    if (this.props.inline) {
      return (
        <div role="alert" className="fixed bottom-4 left-1/2 z-50 flex max-w-[92vw] -translate-x-1/2 items-center gap-3 rounded-lg border border-fail/50 bg-raised px-4 py-2.5 text-[12.5px] text-fg shadow-xl">
          <span className="min-w-0">{said}</span>
          {actions}
        </div>
      );
    }
    return (
      <div role="alert" className="flex h-full min-h-[160px] flex-col items-center justify-center gap-3 px-6 text-center text-[12.5px] text-muted">
        <span className="max-w-[560px]">{said}</span>
        {actions}
      </div>
    );
  }
}
