import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Loader2, Unplug } from "lucide-react";
import { Api, type ConnectedApp, type ConnectorEvent, type ConnectorSetup } from "../api";
import { confirmWays, type ConfirmWays } from "../passkey";
import { ConfirmIt, CopyText } from "./AccountDialog";
import { AppName } from "./AppName";
import { Confirm } from "./bits";
import { waitFor } from "./ConnectApp";
import { dayTime } from "../when";

// Account, Connected apps: the Claude app connector (the server's oauth.py). The address an app is added with, the
// account's switch, and each connected app with where it stands, when it was last used, Revoke and its activity.

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

function Section({ title, children, note }: { title: string; children?: ReactNode; note?: ReactNode }) {
  return (
    <section className="mb-6 last:mb-0">
      <h3 className="mb-1 text-[13px] font-semibold">{title}</h3>
      {note && <p className="mb-3 text-[12px] text-muted">{note}</p>}
      {children}
    </section>
  );
}

/** What a connected app may do, in words. */
export const mayDo = (scopes: string[]) => (scopes.includes("ade.propose") ? "May read and propose" : "May read");

/** What a lost device's steps say of connected apps, as the server says it (oauth.KEEP_WORKING). */
export const KEEP_WORKING = "Connected apps keep working until you revoke them in Connected apps.";

/** What signing out everywhere leaves: it ends no app connection, since nothing says the device it ends allowed one, so
 *  the server answers with those allowed in the last days (oauth.recent_grants), each listed here with Revoke. */
export function StillConnected({ apps, words }: { apps: ConnectedApp[]; words: string }) {
  const [gone, setGone] = useState<string[]>([]);
  const [error, setError] = useState("");
  if (!words && apps.length === 0) return null;
  return (
    <div className="mt-3 text-[12.5px]" role="status" aria-label="Connected apps still connected">
      {words && <p className="text-muted">{words}</p>}
      {apps.length > 0 && (
        <ul className="mt-2 space-y-2">
          {apps.map((a) => (
            <li key={a.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-line p-2" aria-label={`Allowed lately: ${a.client_name}`}>
              <span>&ldquo;<AppName name={a.client_name} className="font-medium" />&rdquo; <span className="text-muted">(its own word)</span></span>
              <span className="text-fg-dim">Its sign-in returns to {a.returns_to}; allowed {dayTime(a.allowed_at)}.</span>
              {gone.includes(a.id) ? <span className="ml-auto text-done">Revoked.</span> : (
                <Confirm className="ml-auto" label="Revoke" again="Click again to revoke it" icon={<Unplug size={12} />}
                  onConfirm={async () => { try { await Api.revokeApp(a.id); setGone((g) => [...g, a.id]); } catch (e) { setError(message(e)); } }} />
              )}
            </li>
          ))}
        </ul>
      )}
      {error && <div role="alert" className="mt-1 text-fail">{error}</div>}
    </div>
  );
}

/** Where a connected app stands, in words. */
export function standing(a: ConnectedApp): string {
  switch (a.state) {
    case "connecting": return `Allowed ${dayTime(a.allowed_at)}; the app has not collected its sign-in yet.`;
    case "waiting": return `Takes effect ${dayTime(a.takes_effect_at)}, unless you cancel it first.`;
    case "active": return `Connected ${dayTime(a.connected_at)}.${a.last_used_at ? ` Last used ${dayTime(a.last_used_at)}${a.last_net ? ` from ${a.last_net}` : ""}.` : ""} Ends ${dayTime(a.ends_at)}.`;
    case "revoked": return `Revoked ${dayTime(a.revoked_at)}: ${a.revoked_why ?? "revoked"}.`;
    default: return `Ended ${dayTime(a.ends_at)}.`;
  }
}

/** One of the account's connector events, in words. */
export function eventWords(e: ConnectorEvent): string {
  const what = e.what ? ` ${e.what}` : "";
  const subject = e.subject ? ` (${e.subject})` : "";
  const outcome = e.outcome === "ok" ? "" : `: ${e.outcome}`;
  return `${e.kind}${what}${subject}${outcome}`;
}

export function ConnectedAppsPane() {
  const [setup, setSetup] = useState<ConnectorSetup | null>(null);
  const [apps, setApps] = useState<ConnectedApp[]>([]);
  const [error, setError] = useState("");
  const [turningOn, setTurningOn] = useState<ConfirmWays | null>(null);
  const load = useCallback(async () => {
    try {
      const s = await Api.connector();
      setSetup(s);
      if (s.on) setApps((await Api.connectedApps()).grants);
    } catch (e) { setError(message(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  if (!setup) return error ? <p role="alert" className="text-[12.5px] text-fail">{error}</p> : <Loader2 size={16} className="animate-spin text-muted" />;
  if (!setup.on) {
    return <Section title="Connected apps" note="The Claude app connector is off on this deployment: its operator turns it on with OPEN_EDITION_CONNECTOR=on." />;
  }
  const url = setup.url ?? "";
  const hold = setup.hold_seconds ?? 0;
  return (
    <>
      <Section title="Use the ADE from the Claude app" note="Claude reads your sessions and what waits for you, and proposes; what it proposes waits here, in Approvals, for you to sign or accept.">
        <div className="flex flex-wrap items-center gap-2">
          <code aria-label="The connector's address" className="rounded bg-bg px-2 py-1 font-mono text-[12px]">{url}</code>
          <CopyText label="Copy" text={url} />
        </div>
        <ol className="mt-3 list-decimal space-y-1 pl-5 text-[12.5px]">
          <li>In Claude (claude.ai, the desktop or the phone app): Customize, Connectors, Add custom connector.</li>
          <li>Paste this address as the server&apos;s URL. Where Claude asks how it identifies itself, choose Claude&apos;s published identity, or Register automatically.</li>
          <li>Connect: this ADE opens. Sign in, read what asks and what it may do, and Allow with one of your passkeys or your password.</li>
          <li>{hold > 0
            ? `The connection takes effect ${waitFor(hold)} later, and each device you turned notifications on in is told at once; until then you can cancel it here.`
            : "The connection takes effect at once, and each device you turned notifications on in is told."}</li>
        </ol>
        <p className="mt-2 text-[12px] text-muted">
          On a Team or Enterprise plan an Owner adds the connector, and each member connects with their own ADE account. Claude&apos;s servers
          reach this ADE over the internet, so the desktop app&apos;s own server cannot be added.
        </p>
      </Section>
      <Section title="This account">
        {setup.account_on ? (
          <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
            <span>Apps may connect to this account.</span>
            <Confirm label="Turn off" again="Click again: every connected app is revoked"
              onConfirm={async () => { try { await Api.setConnectorAccount(false); await load(); } catch (e) { setError(message(e)); } }} />
          </div>
        ) : (
          <div className="text-[12.5px]">
            <div className="flex flex-wrap items-center gap-2">
              <span>Apps may not connect to this account.</span>
              {!turningOn && (
                <button className="btn btn-sm" onClick={async () => { try { setTurningOn(await confirmWays()); } catch (e) { setError(message(e)); } }}>Let apps connect</button>
              )}
            </div>
            {turningOn && (
              <ConfirmIt change={{ what: "connector", label: "Letting apps connect", action: "Turn on", subject: "connector-on", ways: turningOn }}
                onConfirmed={async (confirmation) => { await Api.setConnectorAccount(true, confirmation); setTurningOn(null); await load(); }}
                onCancel={() => setTurningOn(null)} />
            )}
          </div>
        )}
      </Section>
      <Section title="Connected apps">
        {apps.length === 0 ? <p className="text-[12.5px] text-muted">No app is connected.</p> : (
          <div className="space-y-2">{apps.map((a) => <AppCard key={a.id} app={a} onChanged={load} />)}</div>
        )}
      </Section>
      {error && <p role="alert" className="text-[12.5px] text-fail">{error}</p>}
    </>
  );
}

function AppCard({ app, onChanged }: { app: ConnectedApp; onChanged: () => Promise<void> }) {
  const [events, setEvents] = useState<ConnectorEvent[] | null>(null);
  const [before, setBefore] = useState<number | null>(null);
  const [error, setError] = useState("");
  const live = app.state === "connecting" || app.state === "waiting" || app.state === "active";
  const more = async (from: number | null) => {
    try {
      const r = await Api.appEvents(app.id, from);
      setEvents((cur) => [...(from ? cur ?? [] : []), ...r.events]);
      setBefore(r.before);
    } catch (e) { setError(message(e)); }
  };
  return (
    <div className="rounded-lg border border-line p-3 text-[12.5px]" aria-label={`Connected app ${app.client_name}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span>&ldquo;<AppName name={app.client_name} className="font-medium" />&rdquo; <span className="text-muted">(its own word)</span></span>
        <span className="text-muted">{mayDo(app.scopes)}</span>
      </div>
      <div className="mt-1 text-fg-dim">
        Its sign-in returns to {app.returns_to}{app.described_at ? `; it is described at ${app.described_at}` : ""}.
      </div>
      <div className={`mt-1 ${app.state === "waiting" ? "text-wait" : "text-fg-dim"}`}>{standing(app)}</div>
      {app.proposals_waiting > 0 && <div className="mt-1 text-wait">{app.proposals_waiting} of its proposals wait for you in Approvals.</div>}
      <div className="mt-2 flex flex-wrap gap-2">
        {live && (
          <Confirm label={app.state === "waiting" ? "Cancel" : "Revoke"} again={app.state === "waiting" ? "Click again to cancel it" : "Click again to revoke it"}
            icon={<Unplug size={12} />}
            onConfirm={async () => { try { await Api.revokeApp(app.id); await onChanged(); } catch (e) { setError(message(e)); } }} />
        )}
        <button className="btn btn-sm" onClick={() => (events ? setEvents(null) : void more(null))}>{events ? "Hide its activity" : "Its activity"}</button>
      </div>
      {events && (
        <ul aria-label="Its activity" className="mt-2 space-y-0.5 font-mono text-[11.5px] text-fg-dim">
          {events.length === 0 && <li>Nothing yet.</li>}
          {events.map((e) => <li key={e.id}>{dayTime(e.at)} {eventWords(e)}{e.net ? ` from ${e.net}` : ""}</li>)}
          {before && <li><button className="btn btn-sm mt-1" onClick={() => void more(before)}>More</button></li>}
        </ul>
      )}
      {error && <div role="alert" className="mt-1 text-fail">{error}</div>}
    </div>
  );
}
