import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// What a signed-in page's first chunk (Vite's index-*.js) carries: the shell imports the sign-in
// screen, a session's pull request buttons and the agents' API only as they are needed, so their code is not in it; and
// it fetches the code of what is opened first ahead, once the page has settled, so the first open does not wait for it.
const source = (name: string) => readFileSync(join(__dirname, name), "utf8");
const statically = (code: string, from: string) => new RegExp(`^import [^;]*from "${from.replace(/[./]/g, "\\$&")}";`, "m").test(code);

describe("the page's first chunk", () => {
  it("leaves out the sign-in screen, the pull request buttons, design mode and the agents' API, loaded when wanted", () => {
    const app = source("App.tsx");
    for (const from of ["./components/SignIn", "./components/PullRequest", "./components/Design", "./agents", "./components/ContinueSession"]) {
      expect(statically(app, from), from).toBe(false);
    }
    expect(app).toContain('lazy(() => import("./components/SignIn")');
    expect(app).toContain('lazy(() => import("./components/PullRequest")');
    expect(app).toContain('lazy(() => import("./components/Design")');
    expect(app).toContain('lazy(() => import("./components/ContinueSession")');            // the dialog that continues a session in a new one
    expect(app).toContain("onContinue={continuable(rec) ? () => setContinuing(rec.id) : undefined}");   // offered as said
    // design mode is the desktop app's: a browser never asks for its code
    expect(app).toContain("{rec.repo && DESKTOP && <Suspense fallback={null}><PreviewButton rec={rec} /></Suspense>}");
    expect(statically(source("shellPoll.ts"), "./agents")).toBe(false);
    expect(statically(source("store.ts"), "./agents")).toBe(false);
  });

  it("leaves out the control view and the pop-out page, whose map draws calls with RunTree's rows and the agents' API", () => {
    const app = source("App.tsx");
    const main = source("main.tsx");
    const root = source("root.tsx");
    expect(statically(app, "./components/ControlView")).toBe(false);
    expect(statically(app, "./sessionMap")).toBe(false);
    expect(app).toContain('lazy(() => import("./components/ControlView")');
    // the page chooses between the app and a pop-out in root.tsx, which main.tsx renders
    expect(statically(main, "./root")).toBe(true);
    for (const code of [main, root]) {
      expect(statically(code, "./components/PopOut")).toBe(false);
      expect(statically(code, "./sessionMap")).toBe(false);
    }
    expect(root).toContain('lazy(() => import("./components/PopOut")');
    // what the first chunk reads to know a pop-out is the address's own module, which imports nothing of the map's
    expect(statically(root, "./popOut")).toBe(true);
    expect(/^import /m.test(source("popOut.ts"))).toBe(false);
    expect(source("components/RunTree.tsx")).toMatch(/^import [^;]*from "\.\.\/agents";/m);   // why they are left out
  });

  it("fetches ahead the panels opened first: the palette, New session, Linked sessions, Approvals, the control view", () => {
    const app = source("App.tsx");
    const ahead = /const AHEAD = \[([^\]]*)\]/.exec(app)?.[1] ?? "";
    for (const p of ["CommandPalette", "NewSession", "Channel", "Approvals", "PullRequest", "ControlView"]) {
      expect(ahead, p).toContain(`import("./components/${p}")`);
    }
    expect(app).toMatch(/for \(const load of AHEAD\) void load\(\)/);
  });
});
