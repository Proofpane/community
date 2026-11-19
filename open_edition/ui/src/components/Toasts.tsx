import { useStore } from "../store";

/** The app's two toasts at the foot of the screen: an error, or, where there is none, a notice, each as the store has
 *  it (setError, say). Its own component so that the layout cases' page draws it as the app does (layoutHarness.tsx,
 *  tests/test_phone_fit_layout.py). */
export function Toasts() {
  const error = useStore((s) => s.error);
  const notice = useStore((s) => s.notice);
  return (
    <>
      {error && (
        <div role="alert" className="fixed bottom-[calc(1rem_+_var(--safe-bottom))] left-1/2 z-50 -translate-x-1/2 rounded-lg border border-fail/50 bg-raised px-4 py-2.5 text-[12.5px] text-fg shadow-xl">{error}</div>
      )}
      {notice && !error && (
        <div role="status" className={`fixed bottom-[calc(1rem_+_var(--safe-bottom))] left-1/2 z-50 -translate-x-1/2 rounded-lg border bg-raised px-4 py-2.5 text-[12.5px] text-fg shadow-xl ${
          notice.tone === "done" ? "border-done/50" : notice.tone === "wait" ? "border-wait/50" : "border-fail/50"}`}>{notice.text}</div>
      )}
    </>
  );
}
