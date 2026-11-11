import { readFileSync } from "node:fs";
import { join } from "node:path";
import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";

// The service worker (web/sw.js) as a tapped notice reaches it: it opens the ADE in a window of the app's own, never in a
// pop-out of the control view's map, whose window is one of its clients too. The
// worker runs here in a scope of the test's own, its clients listed as the browser lists them, most recently focused first.
const WORKER = readFileSync(join(__dirname, "..", "..", "web", "sw.js"), "utf8");

type Client = { url: string; navigate: ReturnType<typeof vi.fn>; focus: ReturnType<typeof vi.fn> };
const client = (url: string): Client => ({ url, navigate: vi.fn(), focus: vi.fn(async () => undefined) });

async function tap(clients: Client[], link = "/app/#approvals") {
  const handlers: Record<string, (e: unknown) => void> = {};
  const openWindow = vi.fn(async () => null);
  const scope = {
    location: { origin: "https://pp.example" },
    addEventListener: (type: string, fn: (e: unknown) => void) => { handlers[type] = fn; },
    clients: { matchAll: async () => clients, openWindow },
    registration: { showNotification: vi.fn() },
  };
  // The worker's own file, run in a context of its own that holds only `self` and `URL`, the two names it reads, so
  // that none of the test's globals reaches it.
  runInNewContext(WORKER, { self: scope, URL });
  let done: Promise<unknown> = Promise.resolve();
  handlers.notificationclick({ notification: { close() {}, data: { url: link } }, waitUntil: (p: Promise<unknown>) => { done = p; } });
  await done;
  return openWindow;
}

describe("a tapped notice", () => {
  it("opens in the app's own window, passing over a pop-out that had the focus last", async () => {
    const pop = client("https://pp.example/app/?pop=agent&session=0123456789ab&agent=a1");
    const main = client("https://pp.example/app/");
    const openWindow = await tap([pop, main]);
    expect(pop.navigate).not.toHaveBeenCalled();
    expect(main.navigate).toHaveBeenCalledWith("/app/#approvals");
    expect(main.focus).toHaveBeenCalled();
    expect(openWindow).not.toHaveBeenCalled();
  });

  it("opens a window of its own where only pop-outs are open", async () => {
    const pops = [client("https://pp.example/app/?pop=map"), client("https://pp.example/app/?pop=session&session=0123456789ab")];
    const openWindow = await tap(pops);
    for (const p of pops) expect(p.navigate).not.toHaveBeenCalled();
    expect(openWindow).toHaveBeenCalledWith("/app/#approvals");
  });

  it("opens in the first window as before where none is a pop-out", async () => {
    const a = client("https://pp.example/app/");
    const b = client("https://pp.example/console.html");
    await tap([a, b]);
    expect(a.navigate).toHaveBeenCalledWith("/app/#approvals");
    expect(b.navigate).not.toHaveBeenCalled();
  });
});
