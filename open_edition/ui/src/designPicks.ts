import { useEffect, useState } from "react";
import { DESKTOP } from "./api";
import type { PickEvent } from "./components/Design";

/** Picks from the preview windows, newest first, while the desktop app runs. Apart from Design.tsx, whose preview
 *  button and pick dialog load only in the desktop app (App.tsx), so a browser's first chunk carries none of them. */
export function useDesignPicks(): [PickEvent | null, () => void] {
  const [pick, setPick] = useState<PickEvent | null>(null);
  useEffect(() => {
    if (!DESKTOP) return;
    let off: (() => void) | undefined;
    let gone = false;
    void import("@tauri-apps/api/event").then(({ listen }) =>
      listen<PickEvent>("design-pick", (e) => setPick(e.payload)).then((u) => { if (gone) u(); else off = u; }));
    return () => { gone = true; off?.(); };
  }, []);
  return [pick, () => setPick(null)];
}
