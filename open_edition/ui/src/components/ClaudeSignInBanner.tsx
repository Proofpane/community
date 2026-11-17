// The strip under the title bar while Claude Code is not signed in in the workspace: what that stops, naming the
// account new sessions start on when one other than the default is chosen, and the way to sign in. It is at least as
// tall as it was and grows with its words, so at a phone's width they wrap inside it, not over the bar above it and the
// row below (tests/test_phone_fit_layout.py).
export function ClaudeSignInBanner({ account, onSignIn }: { account?: string; onSignIn: () => void }) {
  return (
    <div className="flex min-h-9 flex-none items-center gap-3 border-b border-wait/30 bg-wait/[0.07] px-4 py-1 text-[12.5px]">
      <span className="dot" style={{ background: "var(--color-wait)" }} />
      <span className="text-fg-dim">{account
        ? `The Claude account new sessions start on, ${account}, is not signed in, so they cannot start on it.`
        : "Claude Code is not signed in in this workspace, so Claude sessions and chats cannot start."}</span>
      <button className="btn btn-sm ml-auto" onClick={onSignIn}>Sign in to Claude</button>
    </div>
  );
}
