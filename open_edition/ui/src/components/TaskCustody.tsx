// The Custody section of the account dialog, rendered
// by name by AccountDialog.tsx. The owner's custody controls open risk point 12's text over them, and a custodian's
// acceptance point 13's (its cases "custody" and "other_group", the latter at every custody acceptance, since whether a
// task handed while custody stands goes into another group is not known when it is accepted), every time and holding
// nothing, through RiskTerms. It reads the track first, and
// offers nothing until the read answers. While the track is off on the deployment the read says why (its `off`): the
// section shows it, in the server's words, in place of every control that asks for, accepts, changes or keeps custody,
// and beside it End, Decline and Give back on what stands, whose routes still answer. Any other
// failure keeps what was last read, says the error, and offers Try again.

import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { api, ApiError } from "../api";
import { confirmWays, type ConfirmWays } from "../passkey";
import { TaskOffersApi, type CustodyOverview, type CustodyView, type HandOffTask } from "../taskOffersApi";
import { visible } from "../textRules";
import { ConfirmIt } from "./AccountDialog";
import { RiskTerms } from "./RiskTerms";

/** The few fields of the user's own agents that the custodian's choice reads, from GET /api/ade/agents (its groups,
 *  each with its agents, the retired among them), so that this part reads no type of agents.ts. `account`: the account
 *  the agent names; null where it names none, which is not the user's default account: the workspace runs such an agent
 *  on whichever account the user's new sessions start on, which the app cannot name, so the server takes no such agent
 *  as a receiving agent. */
interface OwnAgent { id: string; name: string; repo: string | null; retired: boolean; account: string | null }
interface AgentsRead { groups?: { agents?: OwnAgent[] }[] }
/** The few fields of the user's accounts that say an agent's group, from GET /api/ade/agents/accounts
 *  (AccountOwnerView): the account (`default` or the workspace's 12 hex),
 *  whether the user confirmed it as their own, and its group. */
interface OwnAccount { account: string; state: string; grp: string | null }
interface AccountsRead { accounts?: OwnAccount[] }

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** A request's confirmation refused for good: the request is gone (404), or out of date or confirmed already (409),
 *  so the same again cannot succeed. A refused confirmation (403), or a server not reached, can, and ConfirmIt offers
 *  it again. */
const deadEnd = (e: unknown) => e instanceof ApiError && (e.status === 404 || e.status === 409);

/** What the owner reads at once after naming a teammate, whatever the app answers: the same words whether or not the
 *  address is someone's here, so the page tells nobody who has an account. */
const SEEN = "If that address is someone's here, they will see your request.";

/** An account's group as the views give it: the personal plans, or one organisation's seats. */
function groupWords(grp: string): string {
  if (grp === "personal") return "personal plans";
  return grp.startsWith("org:") ? `an organisation's seats (${visible(grp.slice(4))})` : visible(grp);
}

/** The owner's custodian, or their request for one, in words: the same whether or not the address is someone's while
 *  nobody has answered. A request ended without acceptance names nobody, since the server cleared the custodian, the
 *  typed address and the agents with it. */
function ownerWords(c: CustodyView): string {
  const who = c.custodian_email ? visible(c.custodian_email) : "your teammate";
  switch (c.state) {
    case "asked": return c.confirmed_at ? `You asked ${who} to be your custodian. It waits for an answer.`
      : `Your request to ${who} is not confirmed, so nobody sees it.`;
    case "accepted": return `Your custodian is ${who}.`;
    case "suspended": return `Custody with ${who} is suspended.`;
    // custody accepted has its receiving agents until 90 days after it ended, when the server reduces it; a request
    // ended before an answer has none: without them, the words are true of both
    case "ended": return c.agents.length ? `Custody with ${who} ended.` : `Your request to ${who}, or the custody it became, ended.`;
    case "declined": return `Your request to ${who} was declined.`;
    case "expired": return `Your request to ${who} expired before it was answered.`;
    default: return `Custody with ${who}: ${visible(String(c.state))}.`;
  }
}

/** A teammate's request to this user, or the custody it became, in words. */
function custodianWords(c: CustodyView): string {
  const who = c.owner_email ? visible(c.owner_email) : "a teammate";
  switch (c.state) {
    case "asked": return `${c.owner_email ? who : "A teammate"} asks you to be their custodian.`;
    case "accepted": return `You are the custodian of ${who}'s tasks. These agents of yours may take them, tried in this order:`;
    case "suspended": return `Your custody of ${who}'s tasks is suspended.`;
    case "ended": return `Your custody of ${who}'s tasks ended.`;
    case "declined": return `You declined the request from ${who}.`;
    case "expired": return `The request from ${who} expired before you answered it.`;
    default: return `Custody of ${who}'s tasks: ${visible(String(c.state))}.`;
  }
}

/** A request or a custody still standing, which its side may answer, change or end. */
const standing = (c: CustodyView) => c.state === "asked" || c.state === "accepted" || c.state === "suspended";

/** The account dialog's Custody section: the owner's custodian (ask, confirm, end, and why it is suspended when it
 *  is); for a teammate, the requests to accept (choosing the receiving agents, in order) or decline, and the custody
 *  tasks they hold, each with Keep and Give back. It reads /api/ade/teammate/custody itself, so it takes no props. */
export function TaskCustody() {
  const [view, setView] = useState<CustodyOverview | null>(null);
  // the track off on the deployment: the read's `off`, in the server's words, shown in place of every control that
  // asks for, accepts, changes or keeps custody, beside what ends custody that stands or gives a custody task back
  const [off, setOff] = useState<string | null>(null);
  // any other failure of the read, said with Try again, over what was last read
  const [failed, setFailed] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [said, setSaid] = useState("");                // what the last change answered, said once
  const [busy, setBusy] = useState(false);
  // the owner's request: open from Ask until it is confirmed or closed (point 12's text over it all that time); `id`
  // the request its confirmation names, once the address is sent
  const [asking, setAsking] = useState<{ id: string | null; ways: ConfirmWays | null } | null>(null);
  const [address, setAddress] = useState("");
  // the custodian's acceptance, or a change of its agents: the request, its agents chosen in order, then the account's
  // ways to confirm once Accept is pressed; and the user's own agents and accounts, read each time an acceptance opens
  // (the accounts null where that read failed: no group is shown then)
  const [accepting, setAccepting] = useState<{ id: string; chosen: string[]; ways: ConfirmWays | null } | null>(null);
  const [agents, setAgents] = useState<OwnAgent[] | null>(null);
  const [accounts, setAccounts] = useState<OwnAccount[] | null>(null);

  const load = useCallback(async () => {
    try {
      const got = await TaskOffersApi.custody();
      setView({ as_owner: got?.as_owner ?? null, as_custodian: Array.isArray(got?.as_custodian) ? got.as_custodian : [],
                tasks: Array.isArray(got?.tasks) ? got.tasks : [] });
      setOff(typeof got?.off === "string" && got.off ? got.off : null);
      setFailed(null);
    } catch (e) {
      setFailed(message(e));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  /** A change made with the sign-in alone (End, Decline, Keep, Give back): its refusal's detail said, and custody read
   *  again either way, since a refusal can say the track went off or the custody moved on. */
  const act = async (fn: () => Promise<unknown>, words: string) => {
    setBusy(true); setError(""); setSaid("");
    try { await fn(); setSaid(words); } catch (e) { setError(message(e)); }
    finally { setBusy(false); await load(); }
  };
  const keep = (t: HandOffTask) =>
    act(() => TaskOffersApi.keep(t.id, t.state_version), "Kept: it goes back when it ends.");

  const openAsk = () => { setError(""); setSaid(""); setAddress(""); setAsking({ id: null, ways: null }); };
  const closeAsk = () => { setAsking(null); void load(); };
  const ask = async () => {
    setBusy(true); setError("");
    try {
      // the answer is the same whether or not the address is someone's: the page reads the request's id alone
      const { custody } = await TaskOffersApi.askCustody(address.trim());
      setAsking({ id: custody.id, ways: null });
      setAsking({ id: custody.id, ways: await confirmWays() });
    } catch (e) { setError(message(e)); void load(); } finally { setBusy(false); }
  };
  // a refusal of the confirmation itself shows in ConfirmIt, which stays open to try again; one that can never succeed
  // (the request gone, or confirmed already) goes back to the address form, the address as typed, saying why
  const askConfirmed = async (confirmation?: string) => {
    if (!asking?.id) return;
    try {
      await TaskOffersApi.confirmCustody(asking.id, confirmation ?? "");
    } catch (e) {
      void load();
      if (!deadEnd(e)) throw e;
      setAsking({ id: null, ways: null });
      setError(message(e));
      return;
    }
    setAsking(null);
    await load();
  };

  const openAccept = async (c: CustodyView) => {
    setError(""); setSaid("");
    // a change of the agents, or their confirmation again after a suspension, starts from the agents as they stand
    setAccepting({ id: c.id, chosen: c.agents.map((a) => a.agent_id), ways: null });
    const [gotAgents, gotAccounts] = await Promise.allSettled([api<AgentsRead>("/api/ade/agents"), api<AccountsRead>("/api/ade/agents/accounts")]);
    // each agent shown with its account's group where the accounts could be read, and with none, saying nothing, where not
    setAccounts(gotAccounts.status === "fulfilled" && Array.isArray(gotAccounts.value?.accounts) ? gotAccounts.value.accounts : null);
    if (gotAgents.status === "fulfilled") {
      setAgents((gotAgents.value?.groups ?? []).flatMap((g) => g.agents ?? []));
    } else {
      // nothing to choose from: the acceptance closes, saying why, and opens again with a new read
      setError(message(gotAgents.reason));
      setAccepting(null);
    }
  };
  const choose = (id: string) => setAccepting((a) => a && { ...a, chosen: a.chosen.includes(id) ? a.chosen.filter((x) => x !== id) : [...a.chosen, id] });
  const askAccept = async () => {
    setError("");
    try {
      const ways = await confirmWays();
      setAccepting((a) => a && { ...a, ways });
    } catch (e) { setError(message(e)); }
  };
  const acceptConfirmed = async (confirmation?: string) => {
    if (!accepting) return;
    try { await TaskOffersApi.acceptCustody(accepting.id, accepting.chosen, confirmation ?? ""); } catch (e) { void load(); throw e; }
    setAccepting(null);
    await load();
  };

  const heading = <h3 className="mb-1 text-[13px] font-semibold">Custody</h3>;
  const retry = failed !== null && (
    <div role="alert" className="mt-2 flex flex-wrap items-center gap-2 text-fail">
      <span>{failed}</span>
      <button className="btn btn-sm" onClick={() => void load()}>Try again</button>
    </div>
  );
  if (view === null) {
    return (
      <section aria-label="Custody" className="mb-6 text-[12.5px] last:mb-0">
        {heading}
        {retry || <div className="text-muted"><Loader2 size={13} className="mr-1 inline animate-spin" /> Loading…</div>}
      </section>
    );
  }

  const own = view.as_owner;
  // the agents that may receive custody tasks: a retired agent takes none, nor one with no repository
  const usable = (agents ?? []).filter((a) => !a.retired && !!a.repo);
  /** An agent's group, from the accounts read: its account's group where the user confirmed that account as their own,
   *  and that it is not otherwise, which the server refuses in its own words. An agent that names no account is said
   *  so whatever the accounts read, the server refusing it as well. Where the accounts were not read, no group: the
   *  group a custody recorded may no longer be the account's, a change of it being what suspends custody. */
  const groupOf = (a: OwnAgent): { words: string; confirmed: boolean } | null => {
    if (!a.account) return { words: "it names no account of its own, so no task goes to it", confirmed: false };
    if (accounts === null) return null;
    const row = accounts.find((r) => r.account === a.account);
    if (!row || row.state !== "confirmed") return { words: "its account is not confirmed as yours", confirmed: false };
    return row.grp ? { words: groupWords(row.grp), confirmed: true } : null;
  };

  const request = (c: CustodyView) => {
    const who = c.owner_email ? visible(c.owner_email) : null;
    // an acceptance, or a change of the agents, is not offered while the track is off: its route refuses then
    const open = off === null && accepting?.id === c.id ? accepting : null;
    const nameOf = (id: string) => visible((agents ?? []).find((a) => a.id === id)?.name || c.agents.find((a) => a.agent_id === id)?.name || id);
    // a receiving agent chosen before that can no longer receive tasks: listed, so it can be taken out, with why
    const gone = open ? open.chosen.filter((id) => !usable.some((a) => a.id === id)) : [];
    const goneWhy = (id: string) => {
      const known = (agents ?? []).find((a) => a.id === id);
      return !known ? "no longer one of your agents" : known.retired ? "retired: the app takes no retired agent" : "it has no repository, so no task goes to it";
    };
    return (
      <div key={c.id} role="group" aria-label={`Custody for ${who ?? "a teammate"}`} className="rounded-lg border border-line p-3">
        <div>{custodianWords(c)}{c.state === "suspended" && c.why ? ` ${visible(c.why)}` : ""}</div>
        {(c.state === "accepted" || c.state === "suspended") && c.agents.length > 0 && !open && (
          <ol className="ml-5 mt-1 list-decimal">
            {c.agents.map((a) => <li key={a.agent_id}>{visible(a.name || a.agent_id)}{a.grp ? <span className="text-muted">, {groupWords(a.grp)}</span> : null}</li>)}
          </ol>
        )}
        {standing(c) && off === null && (
          <RiskTerms point={13} kind="control" open={open !== null} owner={who ?? "your teammate"}
            cases={["custody", "other_group"]} />
        )}
        {open ? (
          <div className="mt-2 space-y-1.5">
            <div>Choose the agents of yours that may take {who ?? "your teammate"}'s tasks, in the order they are tried.</div>
            {agents === null ? <div className="text-muted"><Loader2 size={13} className="mr-1 inline animate-spin" /> Reading your agents…</div> : (
              <>
                {usable.length === 0 && <div className="text-muted">No agent of yours can take tasks: each is retired or has no repository.</div>}
                {usable.map((a) => {
                  const grp = groupOf(a);
                  return (
                    <label key={a.id} className="flex items-center gap-2">
                      <input type="checkbox" checked={open.chosen.includes(a.id)} disabled={open.ways !== null} onChange={() => choose(a.id)} />
                      <span>{visible(a.name)}{grp && <span className={`text-[12px] ${grp.confirmed ? "text-muted" : "text-wait"}`}>, {grp.words}</span>}</span>
                    </label>
                  );
                })}
                {gone.map((id) => (
                  <label key={id} className="flex items-center gap-2">
                    <input type="checkbox" checked disabled={open.ways !== null} onChange={() => choose(id)} />
                    <span>{nameOf(id)}<span className="text-[12px] text-wait">, {goneWhy(id)}</span></span>
                  </label>
                ))}
              </>
            )}
            {open.chosen.length > 0 && <div className="text-muted">In order: {open.chosen.map(nameOf).join(", ")}.</div>}
            {open.ways ? (
              <ConfirmIt key={`accept-${c.id}`}
                change={c.state === "asked"
                  ? { what: "task-custody-accept", label: "Accepting custody", action: "Accept", subject: `task-custody-accept:${c.id}`, ways: open.ways }
                  : { what: "task-custody-accept", label: "Confirming your receiving agents", action: "Confirm", subject: `task-custody-accept:${c.id}`, ways: open.ways }}
                onConfirmed={acceptConfirmed} onCancel={() => setAccepting((a) => a && { ...a, ways: null })} />
            ) : (
              <div className="flex flex-wrap gap-2">
                <button className="btn btn-sm btn-primary" disabled={busy || open.chosen.length === 0} onClick={() => void askAccept()}>
                  {c.state === "asked" ? "Accept" : "Confirm these agents"}
                </button>
                <button className="btn btn-sm" onClick={() => setAccepting(null)}>Cancel</button>
              </div>
            )}
          </div>
        ) : standing(c) && (
          <div className="mt-2 flex flex-wrap gap-2">
            {off === null && c.state === "asked" && (
              <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void openAccept(c)}>
                Accept...
              </button>
            )}
            {off === null && c.state === "accepted" && (
              <button className="btn btn-sm" disabled={busy} onClick={() => void openAccept(c)}>
                Change the agents
              </button>
            )}
            {off === null && c.state === "suspended" && (
              <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void openAccept(c)}>
                Confirm the agents again
              </button>
            )}
            {c.state === "asked"
              ? <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => void act(() => TaskOffersApi.declineCustody(c.id), "Declined.")}>Decline</button>
              : <button className="btn btn-sm" disabled={busy} onClick={() => void act(() => TaskOffersApi.endCustody(c.id), "Ended.")}>End</button>}
          </div>
        )}
      </div>
    );
  };

  return (
    <section aria-label="Custody" className="mb-6 text-[12.5px] last:mb-0">
      {heading}
      {off !== null && <p role="status" className="mb-1 text-[12px] text-muted">{off}</p>}
      <p className="mb-3 text-[12px] text-muted">
        A custodian is a teammate whose agents may take over your tasks confirmed for custody while your own accounts
        are out of room, as the relay's second step: only where this deployment has the relay on, and the task's own
        Relay switch is on.
      </p>
      <div role="group" aria-label="Your custodian" className="space-y-1.5">
        {own ? <div>{ownerWords(own)}{own.state === "suspended" && own.why ? ` ${visible(own.why)}` : ""}</div>
          : <div className="text-muted">You have no custodian.</div>}
        {/* End answers while the track is off; asking for a custodian does not */}
        {(asking === null || off !== null) && (own && standing(own) ? (
          <button className="btn btn-sm" disabled={busy} onClick={() => void act(() => TaskOffersApi.endCustody(own.id), "Ended.")}>End</button>
        ) : off === null && (
          <button className="btn btn-sm" disabled={busy} onClick={openAsk}>Ask a teammate to be your custodian</button>
        ))}
        {off === null && <RiskTerms point={12} kind="control" open={asking !== null} />}
        {off === null && asking && (
          <div className="rounded-lg border border-line p-3">
            {asking.id === null ? (
              <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); void ask(); }}>
                <input type="email" aria-label="Your teammate's email address" autoComplete="off" className="input h-7 w-64"
                  placeholder="Your teammate's email address" value={address} onChange={(e) => setAddress(e.target.value)} />
                <button className="btn btn-sm btn-primary" disabled={busy || !address.trim()}>
                  {busy ? <Loader2 size={12} className="animate-spin" /> : null} Ask
                </button>
                <button type="button" className="btn btn-sm" disabled={busy} onClick={closeAsk}>Close</button>
              </form>
            ) : (
              <>
                <div role="status">{SEEN}</div>
                {asking.ways ? (
                  <ConfirmIt key={asking.id} change={{ what: "task-custody", label: "Asking a teammate to be your custodian", action: "Ask", subject: `task-custody:${asking.id}`, ways: asking.ways }}
                    onConfirmed={askConfirmed} onCancel={closeAsk} />
                ) : <button type="button" className="btn btn-sm mt-2" disabled={busy} onClick={closeAsk}>Close</button>}
              </>
            )}
          </div>
        )}
      </div>
      {view.as_custodian.length > 0 && (
        <div role="group" aria-label="Asked of you" className="mt-4 space-y-2">
          {view.as_custodian.map(request)}
        </div>
      )}
      {view.tasks.length > 0 && (
        <div role="group" aria-label="Custody tasks you hold" className="mt-4 space-y-1">
          <div className="font-medium">Custody tasks you hold</div>
          {/* Keep keeps a hand-off going, so it is not offered while the track is off; Give back is */}
          <div className="text-[12px] text-muted">
            {off === null ? "Keep finishes a task here before it goes back. " : ""}Give back sends it back now.
          </div>
          {view.tasks.map((t) => (
            <div key={t.id} role="group" aria-label={visible(t.title)} className="flex flex-wrap items-center gap-2">
              <span>{visible(t.title)}</span>
              <span className="text-[11.5px] text-muted">{visible(t.state.replace(/_/g, " "))}</span>
              {off === null && (
                <button className="btn btn-sm" disabled={busy} onClick={() => void keep(t)}>Keep</button>
              )}
              <button className="btn btn-sm" disabled={busy} onClick={() => void act(() => TaskOffersApi.giveBack(t.id, t.state_version), "Given back.")}>Give back</button>
            </div>
          ))}
        </div>
      )}
      {said && <div role="status" className="mt-2 text-muted">{said}</div>}
      {/* a refusal that only repeats why the track is off is not said again beneath it */}
      {error && error !== off && <div role="alert" className="mt-2 text-fail">{error}</div>}
      {retry}
    </section>
  );
}
