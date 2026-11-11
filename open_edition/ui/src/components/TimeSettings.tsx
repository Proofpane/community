import { useTimeChoice, LOCALES, localeName, zones } from "../timeChoice";
import { browserLocales, deviceZone, localFull, localTime } from "../when";
import { Modal } from "./Modal";

/** The progress board's times, the page's setting kept on this device: the zone
 *  they are in, the device's or one chosen, and the language whose format writes them, the browser's or one chosen. */
export function TimeSettings({ onClose, now = new Date() }: { onClose: () => void; now?: Date }) {
  const { zone, locale, set } = useTimeChoice();
  const device = deviceZone();
  const browser = browserLocales();
  const sample = { zone, locale };
  return (
    <Modal title="Times on the board" onClose={onClose} width={480}
      footer={<button className="btn btn-sm btn-primary" onClick={onClose}>Done</button>}>
      <div className="flex flex-col gap-3 text-[12.5px]">
        <label className="flex flex-col gap-1">
          <span className="text-fg-dim">Time zone</span>
          <select className="input" aria-label="Time zone" value={zone ?? ""} onChange={(e) => set({ zone: e.target.value || null })}>
            <option value="">This device's ({device})</option>
            {zones(device).map((z) => <option key={z} value={z}>{z}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-fg-dim">Format</span>
          <select className="input" aria-label="Format" value={locale ?? ""} onChange={(e) => set({ locale: e.target.value || null })}>
            <option value="">This browser's languages ({browser.length ? browser.join(", ") : "its own"})</option>
            {LOCALES.map((tag) => <option key={tag} value={tag}>{localeName(tag)}</option>)}
          </select>
        </label>
        <p className="text-fg-dim">
          Now, as the board writes it: <time dateTime={now.toISOString()} title={localFull(now, sample)} className="text-fg">{localTime(now, sample, now)}</time>
        </p>
        <p className="text-[11.5px] text-muted">
          Kept on this device. A time's whole date, its zone and the same moment in UTC are in its tooltip. The rest of
          the page keeps its own times, day first on a 24-hour clock.
        </p>
      </div>
    </Modal>
  );
}
