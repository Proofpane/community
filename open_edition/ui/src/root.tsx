import { lazy, Suspense } from "react";
import { App } from "./App";
import { E2EProvider } from "./e2e";
import { popOutOf } from "./popOut";

// a pop-out's page draws the control view's map, whose code (with the agents' API it carries) is not in the first chunk
const PopOutPage = lazy(() => import("./components/PopOut").then((m) => ({ default: m.PopOutPage })));

/** The page the address asks for: a pop-out (`?pop=map`, `?pop=session&session=ID`, `?pop=agent&session=ID&agent=ID`:
 *  popOut.ts) draws one panel of the control view's map in a window of its own, with the page's sign-in; any other
 *  address, one that names what is not a session or an agent among them, is the app. The end-to-end encryption keys
 *  the tab unlocks live around the app, so every place in it that shows a sealed text shares them; a pop-out shows
 *  none, and reads none. */
export function Root() {
  const pop = popOutOf(location.search);
  return pop ? <Suspense fallback={null}><PopOutPage pop={pop} /></Suspense> : <E2EProvider><App /></E2EProvider>;
}
