import { useEffect, useRef, useState } from "react";
import { KeyRound, Loader2 } from "lucide-react";
import { Api, server, type GrantRepository, type GrantRung, type GrantScope, type GrantWideningView, type GrantWithdrawn, type TaskView } from "../api";
import { confirmWays, prepareGrant, signPrepared, type GrantHead, type Prepared } from "../passkey";
import { hostLabel } from "../places";
import { visible } from "../textRules";
import { dayTime, full } from "../when";
import { useStore } from "../store";
import { CameBack, TakenLine, useAct } from "./Acting";
import { SIGN_OFFLINE, signsHere, useNeedsServer } from "./Channel";

// A task's grant (the app's ade/task_grants.py): the owner's statement of what the task
// may reach, kept per task, each version signed with one of the owner's passkeys over a payload composed here around a
// nonce, as a command is (passkey.ts, prepareGrant). It is shown on its task, and each ask for more in it (its agent's,
// or a linked session's) waits there and in the owner's list (Approvals) until the owner signs it as it was asked, or
// refuses it with the sign-in alone. Nothing here holds an attempt to it.

type GrantTask = Pick<TaskView, "id" | "title" | "state" | "grant" | "grant_widenings" | "grant_for">;
const ENDED: TaskView["state"][] = ["done", "stopped"];
export const RUNGS: GrantRung[] = ["L2", "L2-high", "L3"];

/** The rung an act may need, as the owner reads it. */
export const RUNG_WORDS: Record<GrantRung, string> = {
  L2: "a passkey (L2)", "L2-high": "a security key (L2 high)", L3: "the signing device (L3)",
};

/** Where a grant is signed: as a command is, with one of the account's passkeys, in a browser at the server's own
 *  address (Channel's signsHere), so never in the phone or desktop app. */
export const GRANT_ELSEWHERE = () => `A grant is signed with one of your passkeys, which work only in a browser at ${hostLabel(server())}: sign it from there.`;
/** What an account with no passkey is told before any prompt: it has nothing to sign with (the desktop's own account). */
export const NO_PASSKEY = "A grant is signed with one of this account's passkeys, and it has none: add one in Account first.";
export const NOT_HELD = "Nothing yet holds the task's attempts to their grant: it is kept, signed and shown.";
/** What an ask a revoked linked session left waiting says: the server signs none. */
export const ASKER_REVOKED = "It was revoked: this ask is not signed, and refusing it clears it.";
/** Why a version was withdrawn (the app's task_grants.passkeys_going): the passkey that
 *  signed it went. */
export const WITHDRAWN_WORDS: Record<GrantWithdrawn["why"], string> = {
  removed: "the passkey that signed it was removed",
  high_security: "high security was turned on, and no hardware key signed it",
};
/** What the owner's form says beside what it keeps as it is: it
 *  cannot take one back, and nothing else does yet. */
export const CANNOT_TAKE_BACK = "This form cannot take back a repository or a push branch an ask added: taking one back is not built yet.";
/** The server takes a nonce for five minutes: an older review would cost a prompt and then a refusal. */
const REVIEW_LIFE = 300;
const UNAVAILABLE = "aria-disabled:cursor-not-allowed aria-disabled:opacity-50";
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** A lease of so many seconds, in its largest whole unit: "8 hours", "90 seconds". */
export function leaseWords(seconds: number): string {
  for (const [n, one] of [[86400, "day"], [3600, "hour"], [60, "minute"]] as const) {
    if (seconds % n === 0) return `${seconds / n} ${one}${seconds === n ? "" : "s"}`;
  }
  return `${seconds} seconds`;
}

/** A lease as the form writes it: "90s", "30m", "8h", "7d", in its largest whole unit. */
export function leaseCode(seconds: number): string {
  for (const [n, unit] of [[86400, "d"], [3600, "h"], [60, "m"]] as const) if (seconds % n === 0) return `${seconds / n}${unit}`;
  return `${seconds}s`;
}

const SECONDS: Record<string, number> = { s: 1, m: 60, h: 3600, d: 86400 };
/** A lease the form was given ("90s", "8h"), in seconds; null where it is not written so. */
function parseLease(code: string): number | null {
  const m = /^(\d{1,9})([smhd])$/.exec(code);
  return m ? Number(m[1]) * SECONDS[m[2]] : null;
}

const repoWords = (r: GrantRepository) => `${r.repo}: pushes ${r.push.join(", ") || "no branch"}; merges into ${r.merge.join(", ") || "no branch"}`;
const environmentsWords = (es: GrantScope["environments"]) => `Deploys to ${es.map((e) => `${e.name} at ${RUNG_WORDS[e.rung]}`).join(", ")}`;
const secretsWords = (ss: GrantScope["secrets"]) => `Secrets by name: ${ss.map((s) => `${s.name} for ${leaseWords(s.lease_seconds)}`).join(", ")}`;
const egressWords = (hs: string[]) => `Reaches beyond its programs' own hosts: ${hs.join(", ")}`;
const rungsWords = (r: GrantScope["rungs"]) => `Merging into a protected branch takes ${RUNG_WORDS[r.merge]}; a deploy takes ${RUNG_WORDS[r.deploy]}`;

/** A grant's scope, a line each: its repositories with their branches, where it deploys and at which rung, its secrets
 *  by name with their leases, the hosts it may reach, when its lease ends, and the rung each act takes. */
export function scopeWords(scope: GrantScope): string[] {
  return [
    ...(scope.repositories.length ? scope.repositories.map(repoWords) : ["No repository"]),
    scope.environments.length ? environmentsWords(scope.environments) : "Deploys nowhere",
    scope.secrets.length ? secretsWords(scope.secrets) : "No secrets",
    scope.egress.length ? egressWords(scope.egress) : "Reaches no host beyond its programs' own",
    `Its lease ends ${dayTime(scope.lease_end)}`,
    rungsWords(scope.rungs),
  ];
}

/** What an ask adds, a line each, and only what it names. */
export function addsWords(adds: GrantWideningView["adds"]): string[] {
  const out = adds.repositories.map((r) => {
    const parts = [r.push.length ? `pushes ${r.push.join(", ")}` : "", r.merge.length ? `merges into ${r.merge.join(", ")}` : ""].filter(Boolean);
    return parts.length ? `${r.repo}: ${parts.join("; ")}` : r.repo;
  });
  if (adds.environments.length) out.push(environmentsWords(adds.environments));
  if (adds.secrets.length) out.push(secretsWords(adds.secrets));
  if (adds.egress.length) out.push(egressWords(adds.egress));
  if (adds.lease_end !== null) out.push(`A lease until ${dayTime(adds.lease_end)}`);
  if (adds.rungs !== null) out.push(rungsWords(adds.rungs));
  return out;
}

/** The rungs from the weakest to the strongest: a passkey, a security key, the signing device. */
const RUNG_ORDER: Record<GrantRung, number> = { L2: 0, "L2-high": 1, L3: 2 };
/** A rung as it changes: what it takes now, and what it took, a weaker one said as a lowering. */
const rungChange = (from: GrantRung, to: GrantRung) =>
  `takes ${RUNG_WORDS[to]}, ${RUNG_ORDER[to] < RUNG_ORDER[from] ? "lowered" : "raised"} from ${RUNG_WORDS[from]}`;
const listed = (xs: string[]) => (xs.length ? xs.join(", ") : "none");

/** What an ask changes of the grant as it stands (`stands`, null where none does), a line each and only what it
 *  changes: what it adds as new, each value it moves from what to what, a rung it makes
 *  weaker as a lowering. Of a task with no grant standing, the first grant it makes. */
export function changeWords(stands: GrantScope | null, scope: GrantScope): string[] {
  const out: string[] = [];
  const repos = new Map((stands?.repositories ?? []).map((r) => [r.repo, r]));
  for (const r of scope.repositories) {
    const was = repos.get(r.repo);
    if (!was) { out.push(`${r.repo}, new: pushes ${listed(r.push)}; merges into ${listed(r.merge)}`); continue; }
    if (was.push.join("\n") !== r.push.join("\n")) out.push(`${r.repo}, the branches it pushes: from ${listed(was.push)} to ${listed(r.push)}`);
    if (was.merge.join("\n") !== r.merge.join("\n")) out.push(`${r.repo}, the branches it merges into: from ${listed(was.merge)} to ${listed(r.merge)}`);
  }
  const environments = new Map((stands?.environments ?? []).map((e) => [e.name, e.rung]));
  for (const e of scope.environments) {
    const was = environments.get(e.name);
    if (was === undefined) out.push(`Deploys to ${e.name}, new, at ${RUNG_WORDS[e.rung]}`);
    else if (was !== e.rung) out.push(`Deploying to ${e.name} ${rungChange(was, e.rung)}`);
  }
  const leases = new Map((stands?.secrets ?? []).map((x) => [x.name, x.lease_seconds]));
  for (const x of scope.secrets) {
    const was = leases.get(x.name);
    if (was === undefined) out.push(`Secret ${x.name}, new, for ${leaseWords(x.lease_seconds)}`);
    else if (was !== x.lease_seconds) out.push(`Secret ${x.name}'s lease: from ${leaseWords(was)} to ${leaseWords(x.lease_seconds)}`);
  }
  const hosts = new Set(stands?.egress ?? []);
  const added = scope.egress.filter((h) => !hosts.has(h));
  if (added.length) out.push(`Reaches beyond its programs' own hosts, new: ${added.join(", ")}`);
  if (!stands) out.push(`Its lease ends ${dayTime(scope.lease_end)}`);
  else if (stands.lease_end !== scope.lease_end) out.push(`Its lease ends ${dayTime(scope.lease_end)}, moved from ${dayTime(stands.lease_end)}`);
  if (!stands) out.push(rungsWords(scope.rungs));
  else {
    if (stands.rungs.merge !== scope.rungs.merge) out.push(`Merging into a protected branch ${rungChange(stands.rungs.merge, scope.rungs.merge)}`);
    if (stands.rungs.deploy !== scope.rungs.deploy) out.push(`A deploy ${rungChange(stands.rungs.deploy, scope.rungs.deploy)}`);
  }
  return out;
}

/** Who asked, as the owner reads it: the agent or the linked session by its name, each character that is not shown as
 *  itself written out, and a linked session revoked since said so. */
function Asker({ w }: { w: Pick<GrantWideningView, "requester"> }) {
  const name = w.requester.name ? visible(w.requester.name) : "";
  const who = w.requester.kind === "link" ? "A linked session" : "Its agent";
  const since = w.requester.revoked ? ", since revoked," : "";
  return name ? <>{who} {"\u201c"}<bdi>{name}</bdi>{"\u201d"}{since}</> : <>{who}{since}</>;
}

/** A scope as a list the owner reads. */
function ScopeList({ lines }: { lines: string[] }) {
  return <ul className="mt-0.5 list-disc pl-4 text-[11.5px] text-fg-dim">{lines.map((l, i) => <li key={i} className="break-words">{l}</li>)}</ul>;
}

/** A version reviewed, then signed: `prepare` composes it (a nonce asked for once the account is known to have a
 *  passkey), its scope is said where the passkey's prompt does not show it, and Sign reaches the prompt first thing in
 *  its click. `lead` says which version it is and who asked. */
function SignStep({ taskId, prepare, lead, review, onSigned, onBack, back = "Cancel" }: {
  taskId: string; prepare: () => Promise<Prepared>; lead: string; review: string; onSigned: () => Promise<void> | void; onBack?: () => void;
  back?: string;
}) {
  const signing = useNeedsServer(SIGN_OFFLINE);
  const [prepared, setPrepared] = useState<Prepared | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const reviewedAt = useRef(0);
  const start = async () => {
    if (signing.off || busy) return;
    setBusy(true); setError("");
    try {
      if (!(await confirmWays()).passkeys) { setError(NO_PASSKEY); return; }
      const p = await prepare();
      reviewedAt.current = Date.now();
      setPrepared(p);
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };
  const sign = async () => {
    if (!prepared || signing.off || busy) return;
    if (Date.now() - reviewedAt.current > REVIEW_LIFE * 1000) {
      setPrepared(null);
      setError("This review is more than five minutes old: review it again.");
      return;
    }
    setBusy(true); setError("");
    try {
      const signed = await signPrepared(prepared);          // the prompt first, nothing awaited before it
      await Api.signTaskGrant(taskId, signed);
      setPrepared(null);
      await onSigned();
    } catch (e) { setError(message(e)); setPrepared(null); } finally { setBusy(false); }
  };
  return (
    <div className="mt-1">
      {!prepared && (
        <div className="flex flex-wrap gap-1.5">
          <button className={`btn btn-sm btn-primary ${UNAVAILABLE}`} disabled={busy} {...signing.attrs} onClick={() => void start()}>
            {busy ? <Loader2 size={12} className="animate-spin" /> : null} {review}
          </button>
          {onBack && <button className="btn btn-sm" disabled={busy} onClick={onBack}>{back}</button>}
        </div>
      )}
      {prepared && (
        <div className="rounded border border-line p-1.5">
          <div className="text-[11.5px] text-fg-dim">{lead}</div>
          <ScopeList lines={scopeWords(prepared.payload as unknown as GrantScope)} />
          <div className="mt-1 text-[11px] text-faint">Your passkey's prompt does not show this: this is the only place you see it before you sign.</div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            <button className={`btn btn-sm btn-primary ${UNAVAILABLE}`} disabled={busy} {...signing.attrs} onClick={() => void sign()}>
              <KeyRound size={12} /> {prepared.allow.length ? "Sign with your hardware key" : "Sign with your passkey"}
            </button>
            <button className="btn btn-sm" disabled={busy} onClick={() => { setPrepared(null); onBack?.(); }}>{back}</button>
          </div>
        </div>
      )}
      {signing.note}
      {error && <div role="alert" className="mt-1 text-[11.5px] text-fail">{error}</div>}
    </div>
  );
}

/** The scope a first grant starts from: the task's own repository with its own branch, nothing else, the default
 *  rungs. Its lease is the form's. */
function firstScope(task: GrantTask): Omit<GrantScope, "lease_end"> {
  const f = task.grant_for!;
  return { repositories: f.repo ? [{ repo: f.repo, push: [f.branch], merge: [] }] : [], environments: [], secrets: [], egress: [],
           rungs: { merge: "L2", deploy: "L2-high" } };
}

const words = (s: string) => s.split(/[\s,]+/).filter(Boolean);
const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);
const sortedOnce = (xs: string[]) => [...new Set(xs)].sort();
const byName = <T extends { name: string }>(xs: Map<string, T>) => [...xs.values()].sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));

/** The owner's own version of a task's grant: its first, or a change of the one it has, in one form. A list's entries
 *  are each written once and sorted, as the server holds them, so one scope has one form; every repository it names
 *  pushes the task's own branch (its own pp/task-<id> always, for each repository); the repositories and push branches
 *  asks added are kept as they are, and the form says it cannot take one back. A line it cannot read is said before the
 *  review; the server checks the rest, and refuses what is out of shape before anything is kept. */
function GrantForm({ task, onCancel, onDone, back }: { task: GrantTask; onCancel: () => void; onDone: () => Promise<void> | void; back?: string }) {
  const shown = task.grant ?? null;
  const g = shown && shown.version !== null ? shown : null;          // the version that stands, where one does
  const next = shown ? shown.next : { version: 1, prev: "" };        // after the newest kept, withdrawn or not
  const f = task.grant_for!;
  const base = g ?? firstScope(task);
  const own = f.repo ? base.repositories.find((r) => r.repo === f.repo) : undefined;
  const [merge, setMerge] = useState((own?.merge ?? []).join(", "));
  const [environments, setEnvironments] = useState(base.environments.map((e) => `${e.name} ${e.rung}`).join("\n"));
  const [secrets, setSecrets] = useState(base.secrets.map((s) => `${s.name} ${leaseCode(s.lease_seconds)}`).join("\n"));
  const [hosts, setHosts] = useState(base.egress.join("\n"));
  const [lease, setLease] = useState(g && !g.lease_ended ? "keep" : "7");
  const [mergeRung, setMergeRung] = useState<GrantRung>(base.rungs.merge);
  const [deployRung, setDeployRung] = useState<GrantRung>(base.rungs.deploy);

  /** The scope as the form says it; what is wrong with it is thrown, and said where the review would be. */
  const compose = (): Omit<GrantScope, "lease_end"> & { lease_end: number | null } => {
    // the task's own repository is in every version (the server holds it there) and in the first's scope, and every
    // repository pushes the task's own branch
    const repos = base.repositories.map((r) => ({ ...r, push: sortedOnce([...r.push, f.branch]),
                                                  merge: r.repo === f.repo ? sortedOnce(words(merge)) : r.merge }));
    const envs = new Map<string, { name: string; rung: GrantRung }>();
    for (const line of lines(environments)) {
      const [name, rung = "L2-high", ...more] = line.split(/\s+/);
      if (more.length || !RUNGS.includes(rung as GrantRung)) throw new Error(`An environment is its name and a rung (${RUNGS.join(", ")}): ${line}`);
      envs.set(name, { name, rung: rung as GrantRung });
    }
    const leases = new Map<string, { name: string; lease_seconds: number }>();
    for (const line of lines(secrets)) {
      const [name, code = "1d", ...more] = line.split(/\s+/);
      const seconds = parseLease(code);
      if (more.length || seconds === null) throw new Error(`A secret is its name and a lease written as 90s, 30m, 8h or 7d: ${line}`);
      leases.set(name, { name, lease_seconds: seconds });
    }
    return {
      repositories: repos.sort((a, b) => (a.repo < b.repo ? -1 : a.repo > b.repo ? 1 : 0)),
      environments: byName(envs), secrets: byName(leases), egress: sortedOnce(lines(hosts).map((h) => h.toLowerCase())),
      lease_end: lease === "keep" && g ? g.lease_end : null, rungs: { merge: mergeRung, deploy: deployRung },
    };
  };
  const prepare = async () => {
    const scope = compose();
    const head: GrantHead = { task: task.id, version: next.version, prev: next.prev, ask: f.ask,
                              requester: { kind: "owner" }, widening: "" };
    return prepareGrant(head, scope, lease === "keep" ? 7 : Number(lease));
  };
  const field = "input w-full font-mono text-[11.5px]";
  // what the form keeps as it is: every repository but the task's own, and the push branches of its own beside its own
  // branch, each as it will be signed
  const kept = base.repositories.map((r) => ({ ...r, push: sortedOnce([...r.push, f.branch]) }))
    .filter((r) => r.repo !== f.repo || r.push.some((b) => b !== f.branch))
    .map((r) => (r.repo === f.repo ? `${r.repo}: pushes ${r.push.join(", ")}` : repoWords(r)));
  return (
    <div className="mt-1 grid gap-1 rounded border border-line p-1.5 text-[11.5px]">
      {f.repo && (
        <label className="grid gap-0.5">
          <span className="text-muted">The branches of {f.repo} a pull request of it may merge into (it always pushes {f.branch})</span>
          <input aria-label="Branches it may merge into" className={field} value={merge} onChange={(e) => setMerge(e.target.value)} />
        </label>
      )}
      {kept.length > 0 && (
        <div role="note" aria-label="Kept as they are" className="text-muted">
          Kept as they are: {kept.join("; ")}. {CANNOT_TAKE_BACK}
        </div>
      )}
      <label className="grid gap-0.5">
        <span className="text-muted">Where it may deploy, one a line: its name, and the rung a deploy takes (L2, L2-high or L3; L2-high if none)</span>
        <textarea aria-label="Environments it may deploy to" className={`${field} min-h-[36px]`} value={environments} onChange={(e) => setEnvironments(e.target.value)} />
      </label>
      <label className="grid gap-0.5">
        <span className="text-muted">Its secrets by name, never their values, one a line: the name, and its lease as 90s, 30m, 8h or 7d (a day if none)</span>
        <textarea aria-label="Secrets by name" className={`${field} min-h-[36px]`} value={secrets} onChange={(e) => setSecrets(e.target.value)} />
      </label>
      <label className="grid gap-0.5">
        <span className="text-muted">The hosts it may reach beyond those its programs need, one a line</span>
        <textarea aria-label="Hosts it may reach" className={`${field} min-h-[36px]`} value={hosts} onChange={(e) => setHosts(e.target.value)} />
      </label>
      <div className="flex flex-wrap items-center gap-1.5">
        <select aria-label="Its lease" className="input h-7 w-auto text-[12px]" value={lease} onChange={(e) => setLease(e.target.value)}>
          {g && !g.lease_ended && <option value="keep">its lease as it is, to {dayTime(g.lease_end)}</option>}
          {[1, 7, 30, 90].map((d) => <option key={d} value={String(d)}>a lease of {d === 1 ? "a day" : `${d} days`} from now</option>)}
        </select>
        <select aria-label="Merging into a protected branch takes" className="input h-7 w-auto text-[12px]" value={mergeRung} onChange={(e) => setMergeRung(e.target.value as GrantRung)}>
          {RUNGS.map((r) => <option key={r} value={r}>a merge takes {RUNG_WORDS[r]}</option>)}
        </select>
        <select aria-label="A deploy takes" className="input h-7 w-auto text-[12px]" value={deployRung} onChange={(e) => setDeployRung(e.target.value as GrantRung)}>
          {RUNGS.map((r) => <option key={r} value={r}>a deploy takes {RUNG_WORDS[r]}</option>)}
        </select>
      </div>
      <SignStep taskId={task.id} prepare={prepare} review="Review" lead={`You are signing version ${next.version} of this task's grant:`}
        onSigned={onDone} onBack={onCancel} back={back} />
    </div>
  );
}

/** What a new task's first grant says where it is not signed as the task is made: the task is
 *  made all the same, and a grant is given in a browser at the server's own address, from the task (in the phone and
 *  desktop apps, whose server is set), or once the account has a passkey (in that browser already). */
export const MADE_ELSEWHERE = () => `The task is made without a grant: a grant is signed with one of your passkeys, in a browser at ${hostLabel(server())}. Give it one there, from the task.`;
export const MADE_NO_PASSKEY = "The task is made without a grant: a grant is signed with one of this account's passkeys, and it has none. Add one in Account, then give it one from the task.";

/** A new task's first grant, offered as the task is made: where this
 *  page signs (a browser at the server's own address, signsHere) and the account has a passkey, its first version
 *  composed as the task's own Give it a grant composes it, reviewed and signed with a passkey, or left for later with
 *  Not now; elsewhere (the phone and desktop apps, or an account with no passkey) the task is made without a grant and
 *  this says where one is given. The task is made before any of it, and nothing here holds its Start or decides
 *  what an attempt of a task with no grant may hold. */
export function FirstGrant({ task, onDone }: { task: GrantTask; onDone: () => Promise<void> | void }) {
  const here = signsHere();
  const [ways, setWays] = useState<"reading" | "passkey" | "none">(here ? "reading" : "none");
  useEffect(() => {
    if (!here) return;
    let live = true;
    // the account's passkeys, read once: none, and the form would only meet NO_PASSKEY at its review; a read that fails
    // leaves the review to say it
    confirmWays().then((w) => { if (live) setWays(w.passkeys ? "passkey" : "none"); }, () => { if (live) setWays("passkey"); });
    return () => { live = false; };
  }, [here]);
  const f = task.grant_for;
  return (
    <section role="region" aria-label="Its first grant" className="mt-1 rounded-md border border-line px-2 py-1 text-[11.5px]">
      <div className="font-medium text-fg-dim">Its first grant</div>
      {ways === "reading" && <Loader2 size={12} className="animate-spin text-muted" aria-label="Reading this account's passkeys" />}
      {ways === "passkey" && f && (
        <>
          <div className="text-muted">The task is made. Sign its first grant now: it names {f.repo} with the task's own branch, {f.branch}, which its attempts push.</div>
          <GrantForm task={task} onCancel={() => void onDone()} onDone={onDone} back="Not now" />
        </>
      )}
      {ways === "none" && (
        <>
          <div className="text-muted">{here ? MADE_NO_PASSKEY : MADE_ELSEWHERE()}</div>
          <div className="mt-1"><button className="btn btn-sm" onClick={() => void onDone()}>Done</button></div>
        </>
      )}
    </section>
  );
}

/** An ask for more in a task's grant, as it waits for the owner: who asked, what it changes of the grant as it stands
 *  (changeWords; what it adds, from a server before that), until when it waits, and in the owner's list the goal the
 *  version that signs it pins. Signed as it was asked (the version after the one it widens, that version's digest, who
 *  asked, and the whole scope it makes, each as the server gave them), or refused with the sign-in alone, which only
 *  keeps the grant as it is; one a linked session since revoked left waiting is refused only, never signed. In the
 *  owner's list (`withTask`) it names its task. */
export function GrantWidening({ w, onChanged, withTask = false }: { w: GrantWideningView; onChanged: () => Promise<void> | void; withTask?: boolean }) {
  const signing = useNeedsServer(SIGN_OFFLINE);
  // Refuse shows itself taken at once, its signing step put away while it goes, and comes back with the reason if the
  // server does not take it
  const refusal = useAct(`grant:${w.id}`, useStore((s) => s.setError));
  const asked = w.requester.kind === "link" ? "as a linked session asked" : "as its agent asked";
  const revoked = w.requester.revoked === true;      // its linked session was revoked: never signed
  const refuse = () => {
    if (signing.off) return;
    void refusal.act("Refused", () => Api.refuseGrantWidening(w.id), async () => { await onChanged(); });
  };
  const prepare = () => prepareGrant({
    task: w.task_id, version: w.base_version + 1, prev: w.base_digest, ask: w.ask ?? "",
    requester: { kind: w.requester.kind === "link" ? "link" : "agent", id: w.requester.id ?? "" }, widening: w.id,
  }, w.scope);
  return (
    <div role="group" aria-label="An ask for more in its grant" className="mt-1.5 rounded-md border border-wait/50 bg-wait/[0.05] p-2 text-[12px]">
      {withTask && <div className="mb-0.5 truncate text-[11.5px] text-muted">{w.task_title ?? "A task"}</div>}
      <div><Asker w={w} /> asks for more in its grant:</div>
      <ScopeList lines={w.stands !== undefined ? changeWords(w.stands, w.scope) : addsWords(w.adds)} />
      {withTask && typeof w.goal === "string" && (
        <div role="note" aria-label="The goal it pins" className="mt-0.5 whitespace-pre-wrap break-words text-[11px] text-fg-dim">
          The version it makes pins the task's goal as it is now: {"\u201c"}{visible(w.goal)}{"\u201d"}
        </div>
      )}
      <div className="mt-0.5 text-[11px] text-faint" title={full(w.expires_at)}>It changes nothing until you sign it, and waits until {dayTime(w.expires_at)}.</div>
      {revoked && <div className="mt-0.5 text-[11px] text-wait">{ASKER_REVOKED}</div>}
      {revoked || refusal.taken ? null : signsHere() && w.ask
        ? <SignStep taskId={w.task_id} prepare={prepare} review="Review and sign" lead={`You are signing version ${w.base_version + 1} of this task's grant, ${asked}:`}
            onSigned={onChanged} />
        : withTask ? <div className="mt-0.5 text-[11px] text-muted">{GRANT_ELSEWHERE()}</div> : null}
      <div className="mt-1">
        <button className={`btn btn-sm btn-danger ${UNAVAILABLE}`} disabled={!!refusal.taken} {...signing.attrs} onClick={refuse}>Refuse</button>
      </div>
      {refusal.taken && <TakenLine said={refusal.taken} sure={refusal.sure} />}
      <CameBack why={refusal.failed} />
    </div>
  );
}

/** A task's grant on its task: the version that stands (its digest, the passkey that signed it, who asked, its
 *  scope), or that it has none; each version newer than it withdrawn as the passkey that signed it went, and why, and
 *  that none stands where each was; whether it still pins the task's goal; and, while the task is open, the owner's own
 *  change of it and each ask for more that waits. Nothing for a server from before grants, which says nothing of
 *  them. */
export function TaskGrant({ task, onChanged }: { task: GrantTask; onChanged: () => Promise<void> | void }) {
  const [composing, setComposing] = useState(false);
  if (!task.grant_for) return null;
  const shown = task.grant ?? null;
  const g = shown && shown.version !== null ? shown : null;          // the version that stands, where one does
  const withdrawn = shown?.withdrawn ?? [];
  const ended = ENDED.includes(task.state);
  const here = signsHere();
  const asks = ended ? [] : task.grant_widenings ?? [];
  const asker = g?.requester.kind === "agent" ? ", as its agent asked" : g?.requester.kind === "link" ? ", as a linked session asked" : "";
  return (
    <section role="region" aria-label="Its grant" className="mt-1 rounded-md border border-line px-2 py-1 text-[11.5px]">
      <div className="font-medium text-fg-dim">Its grant</div>
      {g ? (
        <>
          <div className="text-muted">
            Version {g.version}, signed <span title={full(g.signed_at)}>{dayTime(g.signed_at)}</span>
            {g.passkey ? <> with your passkey {"\u201c"}<bdi>{visible(g.passkey)}</bdi>{"\u201d"}</> : null}{asker}: <code className="font-mono" title={g.digest}>{g.digest.slice(0, 12)}</code>
          </div>
          <ScopeList lines={scopeWords(g)} />
          {g.lease_ended && <div className="text-wait">Its lease has ended.</div>}
          {!g.ask_current && <div className="text-wait">It pins the task's goal as it was when it was signed, and the goal has changed since.</div>}
        </>
      ) : shown ? (
        <div className="text-wait">No version of its grant stands.</div>
      ) : (
        <div className="text-muted">
          {task.grant_for.repo
            ? `No grant yet. Its first names ${task.grant_for.repo} with the task's own branch, ${task.grant_for.branch}, which its attempts push.`
            : "No grant yet: a task with no repository starts without one, as before."}
        </div>
      )}
      {withdrawn.map((w) => (
        <div key={w.version} className="text-wait">Version {w.version} was withdrawn: {WITHDRAWN_WORDS[w.why] ?? "the passkey that signed it went"}.</div>
      ))}
      {g && withdrawn.length > 0 && <div className="text-muted">It stands at version {g.version}, the newest version not withdrawn.</div>}
      <div className="text-faint">{NOT_HELD}</div>
      {ended && <div className="text-muted">The task has ended: its grant does not change.</div>}
      {!ended && !here && <div className="text-muted">{GRANT_ELSEWHERE()}</div>}
      {!ended && here && !composing && (
        <div className="mt-1 flex flex-wrap gap-1.5">
          {!g && <button className="btn btn-sm" onClick={() => setComposing(true)}>Give it a grant</button>}
          {g && !g.ask_current && <button className="btn btn-sm" onClick={() => setComposing(true)}>Sign it again</button>}
          {g && <button className="btn btn-sm" onClick={() => setComposing(true)}>Change it</button>}
        </div>
      )}
      {!ended && here && composing && (
        <GrantForm task={task} onCancel={() => setComposing(false)} onDone={async () => { setComposing(false); await onChanged(); }} />
      )}
      {asks.map((w) => <GrantWidening key={w.id} w={w} onChanged={onChanged} />)}
    </section>
  );
}
