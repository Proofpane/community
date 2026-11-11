"""Agents, under /api/ade/agents (agents.py): a chat kept under a name with a brief, run again, and watched in one
tree. Every route needs a signed-in account the provider gives a workspace; the list and every record are read from
the app's store, so none of them starts a machine. Run now does, when the machine is stopped, as the console's Start.

    GET    /api/ade/agents                       the tree: groups, each agent with its totals and last runs, and the
                                                 runs waiting for the owner
    POST   /api/ade/agents                       {name, brief, repo?, base_ref?, group?, chat_settings?, allow?,
                                                 limits?, keep_worktree?, account?, mcp_servers?, connectors?,
                                                 from_session?}: a new agent
    GET    /api/ade/agents/draft?session=ID      what Keep as an agent carries from a chat (its machine running)
    GET    /api/ade/agents/{id}?before=T         one agent with its runs, newest first, a page at a time
    PUT    /api/ade/agents/{id}                  {any of the fields}: change it (a new brief is a new version)
    POST   /api/ade/agents/{id}/pause            no new run starts; a run working goes on
    POST   /api/ade/agents/{id}/resume
    POST   /api/ade/agents/{id}/retire           no more runs; its queued ones stop; its worktree goes
    POST   /api/ade/agents/{id}/runs             {prompt?}: run it now (the machine started first when stopped)
    POST   /api/ade/agents/runs/{run}/stop       stop a run
    POST   /api/ade/agents/runs/{run}/thumbs     {value: up | down | null, note?}: the owner's label on a run
    GET    /api/ade/agents/runs/{run}/tree       its turns, tool calls and sub-agents: the app's copy, else the workspace's
    GET    /api/ade/agents/{id}/metrics?days=7   its numbers over the last 7 or 30 days (agent_metrics.py)
    GET    /api/ade/agents/metrics.csv?days=30   every agent's numbers by day, as CSV
    GET|POST /api/ade/agents/{id}/suites         its suites; a new one ({name, items: [{question, sources, group?,
                                                 held_out?}]}) (suites.py)
    PUT|DELETE /api/ade/agents/suites/{suite}    {name?, items?}: renamed, or its items all given again; or removed
    POST   /api/ade/agents/suites/{suite}/evals  {held_out?}: each question a fresh run of the agent, then scored
    POST   /api/ade/agents/runs/{run}/replay     a replayable run started again as it was, to measure it
    GET    /api/ade/agents/judge-trust           the judge's agreement with the owner's labels, per judge version
    GET    /api/ade/agents/improvements          the improve loop's proposals, its week's budget, its review prompt
    POST   /api/ade/agents/improvements/{p}/approve   the owner's tap: the change made, and a version of it
    POST   /api/ade/agents/improvements/{p}/reject    {reason?}: rejected, with why
    DELETE /api/ade/agents/{id}/skills/{name}    one of its skills taken away
    GET    /api/ade/agents/{id}/facts            its facts: current, contradicting each other, retired (facts.py)
    POST   /api/ade/agents/{id}/facts/{fact}/retract   {reason?}: the owner retires one
    GET    /api/ade/agents/{id}/memory           its memory versions, and its folder now when the machine runs
    GET    /api/ade/agents/{id}/memory/{n}       a version's files and what changed from the one before
    POST   /api/ade/agents/{id}/memory/{n}/rollback    the folder made that version's files again (machine running)
    POST   /api/ade/agents/{id}/memory/unarchive {path}: an archived memory file back
    GET    /api/ade/agents/mcp-search?q=&cursor= the official MCP registry's servers matching q (unvetted)
    POST   /api/ade/agents/{id}/tools/{name}/accept   its server's tools as the last run found them: used again
    GET|PUT /api/ade/agents/{id}/mcp-secrets     {secrets: {server: {name: value}}}: set at the workspace (running),
                                                 the only place they are kept; which names have a value comes back
    POST   /api/ade/agents/proposals/{id}        {state: added | dismissed}: a server a run proposed, settled
    GET|POST /api/ade/agents/{id}/triggers       its schedules and hooks; a new one ({kind: schedule, cron, tz,
                                                 prompt?, grace_minutes?} or {kind: hook, hook_type: github | bearer,
                                                 prompt?}); a hook's secret comes back this once
    PUT|DELETE /api/ade/agents/{id}/triggers/{t} change one (its cron, timezone, prompt, grace, enabled) or remove it
    POST   /api/ade/agents/{id}/triggers/{t}/run run a schedule once now, as its scheduler would: unattended, held as a
                                                 firing is held; its next time does not move
    GET    /api/ade/agents/calendar?from=&to=    the account's triggers in a window of unix seconds, at most 62 days:
                                                 each schedule's coming times, the firings the store records, the next
                                                 20 due (triggers.calendar; read from the store, starting nothing)
    POST   /api/ade/hooks/{hook}                 a delivery to a hook (no account: its signature is the hook's)

An agent's tasks (tasks.py), declared before the routes above that take an agent's id:

    GET    /api/ade/agents/tasks?agent=ID        the tasks, each as the page shows it, and the deployment's switches
    POST   /api/ade/agents/tasks                 {agent_id, title, goal, steps, protected, external, backend,
                                                 between_steps?}: a new task, ready
    GET|PUT|DELETE /api/ade/agents/tasks/{id}    one task; a change {state_version, ...}; a done or stopped task deleted
    GET    /api/ade/agents/tasks/{id}/journal    its journal, newest first (before, limit)
    POST   /api/ade/agents/tasks/{id}/start | continue | continue-on | next-on | stop | switches | resolve | end |
           answer                                the owner's controls, each with the state_version the page read but
                                                 Stop and the switches
    GET    /api/ade/agents/account-records       the tasks' account records, deleted tasks' among them (agent=ID)
    GET|DELETE /api/ade/agents/tasks/{id}/account-record   one task's record; its Delete, with a confirmation
    GET    /api/ade/agents/accounts              the workspace's accounts as their owner reported and confirmed them
    POST   /api/ade/agents/accounts/report       {account}: its identity read again in the workspace; `kept` where it
                                                 gave no report and the account is confirmed or held (nothing written)
    POST   /api/ade/agents/accounts/{id}/confirm {confirmation, report, org_id}: confirmed as the owner's own, after a
                                                 hold, refused (409) where the report shown is not the account's now
    PUT    /api/ade/agents/accounts/{id}         {in_relays?, reserve_lifted?}
    POST   /api/ade/agents/accounts/order        {ids}: Connections' order
    GET    /api/ade/agents/backends              the workspace's backends and each one's windows

A task's grant (task_grants.py), with the sign-in alone (the workspace not asked):

    GET    /api/ade/agents/tasks/grant-widenings the asks for more in the tasks' grants that wait for the owner
    POST   /api/ade/agents/tasks/grant-widenings/{id}/refuse   one refused, with the sign-in alone
    GET    /api/ade/agents/tasks/{id}/grant      the task's grant, each version as its passkey signed it, its asks
    POST   /api/ade/agents/tasks/{id}/grant      {payload, credential}: a version the owner signed with a passkey, at
                                                 the task's creation, to widen it, or to sign an ask
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ..deps import current_user
from . import agents, facts, suites, task_grants, tasks, triggers, workspace_client
from .workspace_routes import workspace_user

router = APIRouter(prefix="/api/ade/agents")
hooks = APIRouter(prefix="/api/ade/hooks")


class AgentIn(BaseModel):
    name: str = Field(max_length=400)
    brief: str = Field(max_length=20000)
    group: Optional[str] = Field(default=None, max_length=400)
    repo: Optional[str] = Field(default=None, max_length=400)
    base_ref: Optional[str] = Field(default=None, max_length=400)
    keep_worktree: bool = True
    account: Optional[str] = Field(default=None, max_length=40)
    chat_settings: Optional[dict] = None
    allow: Optional[list] = None
    limits: Optional[dict] = None
    mcp_servers: Optional[list] = None
    connectors: bool = False
    unattended_mode: str = "dontAsk"
    precheck: Optional[str] = Field(default=None, max_length=8000)
    max_budget_usd: Optional[float] = None
    est_usd_per_day: Optional[float] = None
    autocompact_window: Optional[int] = None
    max_compactions: int = 2
    may_dispatch: bool = False
    accepts_dispatch: bool = False
    max_dispatch_per_run: int = 5
    relations: Optional[dict] = None
    anomaly_pause: bool = False
    judge_runs: bool = False
    improve: bool = False
    acceptance: Optional[dict] = None
    decay_runs: int = 30
    reverify: bool = False
    from_session: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{12}$")


class SuiteIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=400)
    items: Optional[list] = None


class EvalIn(BaseModel):
    held_out: bool = False


class PathIn(BaseModel):
    path: str = Field(max_length=400)


class RetractIn(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=2000)


class RunIn(BaseModel):
    prompt: Optional[str] = Field(default=None, max_length=20000)


class SecretsIn(BaseModel):
    secrets: dict[str, dict[str, str]]


class ProposalIn(BaseModel):
    state: str = Field(pattern="^(?:added|dismissed)$")


class DecideIn(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=2000)


class ThumbsIn(BaseModel):
    value: Optional[str] = Field(default=None, pattern="^(?:up|down)$")
    note: Optional[str] = Field(default=None, max_length=2000)


# ---- tasks (tasks.py): declared before the /{agent_id} routes, which would take a
# literal path's first part as an agent's id --------------------------------------------------------------------------

class TaskIn(BaseModel):
    agent_id: str = Field(max_length=40)
    title: str = Field(max_length=1000)
    goal: str = Field(max_length=100000)
    steps: list = Field(max_length=100)
    protected: Optional[list] = Field(default=None, max_length=100)
    external: Optional[list] = Field(default=None, max_length=100)
    backend: Optional[dict] = None
    between_steps: Optional[str] = Field(default=None, max_length=20)


class VersionIn(BaseModel):
    state_version: int


class ContinueOnIn(BaseModel):
    state_version: int
    backend: dict
    cap_usd: Optional[float] = None


class NextOnIn(BaseModel):
    state_version: int
    backend: dict


class SwitchesIn(BaseModel):
    relay: Optional[bool] = None
    auto_resume: Optional[bool] = None
    cross_groups: Optional[bool] = None
    confirmation: Optional[str] = Field(default=None, max_length=400)


class ResolveIn(BaseModel):
    state_version: int
    what: str = Field(max_length=20)
    effect_id: Optional[str] = Field(default=None, max_length=40)
    outcome: str = Field(max_length=20)
    note: Optional[str] = Field(default=None, max_length=8000)


class EndIn(BaseModel):
    state_version: int
    outcome: str = Field(max_length=20)


class AnswerIn(BaseModel):
    state_version: int
    text: str = Field(max_length=8000)
    for_custody: bool = False
    confirmation: Optional[str] = Field(default=None, max_length=400)


class ConfirmationIn(BaseModel):
    confirmation: Optional[str] = Field(default=None, max_length=400)


class ShownReportIn(BaseModel):
    """An IdentityReport as the page showed it, each field at most what
    tasks._clean_report keeps; other keys are ignored."""
    email: Optional[str] = Field(default=None, max_length=300)
    organization: Optional[str] = Field(default=None, max_length=300)
    plan: Optional[str] = Field(default=None, max_length=300)
    token_source: Optional[str] = Field(default=None, max_length=300)
    api_key_source: Optional[str] = Field(default=None, max_length=300)
    api_provider: Optional[str] = Field(default=None, max_length=300)


class AccountConfirmIn(BaseModel):
    """The confirmation, and what the page showed when the owner pressed: the account's report and its orgId
    (tasks.confirm_account)."""
    confirmation: Optional[str] = Field(default=None, max_length=400)
    report: Optional[ShownReportIn] = None
    org_id: Optional[str] = Field(default=None, max_length=80)


class AccountIn(BaseModel):
    account: str = Field(max_length=40)


class AccountPutIn(BaseModel):
    in_relays: Optional[bool] = None
    reserve_lifted: Optional[bool] = None


class OrderIn(BaseModel):
    ids: list = Field(max_length=100)


@router.get("/tasks")
def task_listing(agent: Optional[str] = None, user=Depends(workspace_user)):
    return tasks.listing(user, agent)


@router.post("/tasks")
def task_new(body: TaskIn, user=Depends(workspace_user)):
    return tasks.new_task(user, body.model_dump(exclude_none=True))


# ---- a task's grant (task_grants.py): the owner's list of asks first, before /tasks/{task_id} would take its
# literal part as a task's id. A grant is read, signed and refused by its owner with the sign-in (current_user), the
# signature checked where it is kept; the workspace is not asked

@router.get("/tasks/grant-widenings")
def task_grant_widenings(user=Depends(current_user)):
    return task_grants.listing(user)


@router.post("/tasks/grant-widenings/{widening_id}/refuse")
def task_grant_widening_refuse(widening_id: str, user=Depends(current_user)):
    return task_grants.refuse(user, widening_id)


@router.get("/tasks/{task_id}/grant")
def task_grant(task_id: str, user=Depends(current_user)):
    return task_grants.of_task(user, task_id)


@router.post("/tasks/{task_id}/grant")
def task_grant_sign(task_id: str, body: dict[str, Any], user=Depends(current_user)):
    return task_grants.sign(user, task_id, body)


@router.get("/tasks/{task_id}")
def task_one(task_id: str, user=Depends(workspace_user)):
    return tasks.one(user, task_id)


@router.get("/tasks/{task_id}/journal")
def task_journal(task_id: str, before: Optional[int] = None, limit: int = 100, user=Depends(workspace_user)):
    return tasks.journal_of(user, task_id, before, limit)


@router.get("/tasks/{task_id}/packets/{run_id}")
def task_packet(task_id: str, run_id: str, user=Depends(workspace_user)):
    return tasks.packet_of(user, task_id, run_id)


@router.put("/tasks/{task_id}")
def task_change(task_id: str, body: dict[str, Any], user=Depends(workspace_user)):
    return tasks.update_task(user, task_id, body)


@router.delete("/tasks/{task_id}")
def task_delete(task_id: str, body: VersionIn, user=Depends(workspace_user)):
    return tasks.delete_task(user, task_id, body.state_version)


@router.post("/tasks/{task_id}/start")
def task_start(task_id: str, body: VersionIn, user=Depends(workspace_user)):
    return tasks.start(user, task_id, body.state_version)


@router.post("/tasks/{task_id}/continue")
def task_continue(task_id: str, body: VersionIn, user=Depends(workspace_user)):
    return tasks.continue_(user, task_id, body.state_version)


@router.post("/tasks/{task_id}/continue-on")
def task_continue_on(task_id: str, body: ContinueOnIn, user=Depends(workspace_user)):
    return tasks.continue_on(user, task_id, body.state_version, body.backend, body.cap_usd)


@router.post("/tasks/{task_id}/next-on")
def task_next_on(task_id: str, body: NextOnIn, user=Depends(workspace_user)):
    return tasks.next_on(user, task_id, body.state_version, body.backend)


@router.post("/tasks/{task_id}/stop")
def task_stop(task_id: str, user=Depends(workspace_user)):
    return tasks.stop(user, task_id)


@router.post("/tasks/{task_id}/switches")
def task_switches(task_id: str, body: SwitchesIn, user=Depends(workspace_user)):
    return tasks.switches(user, task_id, body.model_dump())


@router.post("/tasks/{task_id}/resolve")
def task_resolve(task_id: str, body: ResolveIn, user=Depends(workspace_user)):
    return tasks.resolve(user, task_id, body.model_dump())


@router.post("/tasks/{task_id}/end")
def task_end(task_id: str, body: EndIn, user=Depends(workspace_user)):
    return tasks.end(user, task_id, body.state_version, body.outcome)


@router.post("/tasks/{task_id}/answer")
def task_answer(task_id: str, body: AnswerIn, user=Depends(workspace_user)):
    return tasks.answer(user, task_id, body.state_version, body.text, body.for_custody, body.confirmation)


@router.get("/account-records")
def account_records(agent: Optional[str] = None, user=Depends(workspace_user)):
    return tasks.records(user, agent)


@router.get("/tasks/{task_id}/account-record")
def task_account_record(task_id: str, user=Depends(workspace_user)):
    return tasks.task_record(user, task_id)


@router.delete("/tasks/{task_id}/account-record")
def task_account_record_delete(task_id: str, body: ConfirmationIn, user=Depends(workspace_user)):
    return tasks.delete_record(user, task_id, body.confirmation)


@router.get("/accounts")
def task_accounts(user=Depends(workspace_user)):
    return tasks.accounts(user)


@router.post("/accounts/report")
def task_account_report(body: AccountIn, user=Depends(workspace_user)):
    return tasks.report_account(user, body.account)


@router.post("/accounts/order")
def task_accounts_order(body: OrderIn, user=Depends(workspace_user)):
    return tasks.order_accounts(user, body.ids)


@router.post("/accounts/{owner_id}/confirm")
def task_account_confirm(owner_id: str, body: AccountConfirmIn, user=Depends(workspace_user)):
    # a body that names no report says nothing of what was shown (None); one that names it, null included, does
    shown = None
    if "report" in body.model_fields_set:
        shown = {"report": body.report.model_dump() if body.report else None, "org_id": body.org_id}
    return tasks.confirm_account(user, owner_id, body.confirmation, shown)


@router.put("/accounts/{owner_id}")
def task_account_put(owner_id: str, body: AccountPutIn, user=Depends(workspace_user)):
    return tasks.put_account(user, owner_id, body.model_dump())


@router.get("/backends")
def task_backends(user=Depends(workspace_user)):
    return tasks.backends(user)


# ---- agents -----------------------------------------------------------------------------------------------------------

@router.get("")
def listing(user=Depends(workspace_user)):
    return agents.listing(user)


@router.post("")
def create(body: AgentIn, user=Depends(workspace_user)):
    given = body.model_dump(exclude={"from_session"})
    return agents.create(user, given, from_session=body.from_session)


@router.get("/calendar")
def calendar(start: int = Query(alias="from"), end: int = Query(alias="to"), user=Depends(workspace_user)):
    return triggers.calendar(user, start, end)


@router.get("/mcp-search")
def mcp_search(q: str = "", cursor: Optional[str] = None, user=Depends(workspace_user)):
    return agents.search(q, cursor)


@router.post("/proposals/{proposal_id}")
def settle_proposal(proposal_id: str, body: ProposalIn, user=Depends(workspace_user)):
    return agents.settle_proposal(user, proposal_id, body.state)


@router.get("/draft")
def draft(session: str, user=Depends(workspace_user)):
    return agents.draft(user, session)


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: str, user=Depends(workspace_user)):
    # a task's attempt: the owner's Stop of its task, kept on the attempt before the workspace is asked
    # (tasks.stop_attempt); once kept, whatever the stop then meets (an attempt ended already, a workspace not running
    # or not answering) is answered as the task's own Stop answers it, 200 (tasks.attempt_stop_kept)
    marked = tasks.stop_attempt(user, run_id)
    try:
        return agents.stop_run(user, run_id)
    except (agents.AgentError, workspace_client.WorkspaceCallError) as e:
        if not marked:
            raise
        return tasks.attempt_stop_kept(user, run_id, e)


@router.get("/runs/{run_id}/tree")
def run_tree(run_id: str, user=Depends(workspace_user)):
    return agents.tree(user, run_id)


@router.post("/runs/{run_id}/replay")
def replay(run_id: str, user=Depends(workspace_user)):
    return agents.replay(user, run_id)


@router.get("/judge-trust")
def judge_trust(user=Depends(workspace_user)):
    return agents.judge_trust(user)


@router.get("/improvements")
def improvements(user=Depends(workspace_user)):
    return agents.improvements(user)


@router.post("/improvements/{proposal_id}/approve")
def approve_improvement(proposal_id: str, user=Depends(workspace_user)):
    return agents.decide_improvement(user, proposal_id, True)


@router.post("/improvements/{proposal_id}/reject")
def reject_improvement(proposal_id: str, body: DecideIn = DecideIn(), user=Depends(workspace_user)):
    return agents.decide_improvement(user, proposal_id, False, body.reason)


@router.delete("/{agent_id}/skills/{name}")
def remove_skill(agent_id: str, name: str, user=Depends(workspace_user)):
    return agents.remove_skill(user, agent_id, name)


@router.post("/runs/{run_id}/thumbs")
def thumbs(run_id: str, body: ThumbsIn, user=Depends(workspace_user)):
    return agents.thumbs(user, run_id, body.value, body.note)


@router.put("/suites/{suite_id}")
def change_suite(suite_id: str, body: SuiteIn, user=Depends(workspace_user)):
    return suites.update(user, suite_id, body.name, body.items)


@router.delete("/suites/{suite_id}")
def remove_suite(suite_id: str, user=Depends(workspace_user)):
    return suites.delete(user, suite_id)


@router.post("/suites/{suite_id}/evals")
def run_eval(suite_id: str, body: EvalIn, user=Depends(workspace_user)):
    return suites.start(user, suite_id, body.held_out)


@router.get("/{agent_id}/facts")
def agent_facts(agent_id: str, user=Depends(workspace_user)):
    return agents.facts_of(user, agent_id)


@router.post("/{agent_id}/facts/{fact_id}/retract")
def retract_fact(agent_id: str, fact_id: str, body: RetractIn, user=Depends(workspace_user)):
    return facts.retract(user, agent_id, fact_id, body.reason)


@router.get("/{agent_id}/memory")
def agent_memory(agent_id: str, user=Depends(workspace_user)):
    return agents.memory_of(user, agent_id)


@router.get("/{agent_id}/memory/{version}")
def memory_version(agent_id: str, version: int, user=Depends(workspace_user)):
    return agents.memory_version(user, agent_id, version)


@router.post("/{agent_id}/memory/{version}/rollback")
def memory_rollback(agent_id: str, version: int, user=Depends(workspace_user)):
    return agents.memory_rollback(user, agent_id, version)


@router.post("/{agent_id}/memory/unarchive")
def memory_unarchive(agent_id: str, body: PathIn, user=Depends(workspace_user)):
    return agents.memory_unarchive(user, agent_id, body.path)


@router.get("/{agent_id}/suites")
def agent_suites(agent_id: str, user=Depends(workspace_user)):
    return suites.of_agent(user, agent_id)


@router.post("/{agent_id}/suites")
def new_suite(agent_id: str, body: SuiteIn, user=Depends(workspace_user)):
    return suites.create(user, agent_id, body.name, body.items or [])


@router.get("/metrics.csv")
def numbers_csv(days: int = 30, user=Depends(workspace_user)):
    return Response(agents.numbers_csv(user, days), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="agents-numbers.csv"'})


@router.get("/{agent_id}/metrics")
def numbers(agent_id: str, days: int = 7, user=Depends(workspace_user)):
    return agents.numbers(user, agent_id, days)


@router.get("/{agent_id}")
def one(agent_id: str, before: Optional[int] = None, user=Depends(workspace_user)):
    return agents.one(user, agent_id, before)


@router.put("/{agent_id}")
def update(agent_id: str, body: dict[str, Any], user=Depends(workspace_user)):
    return agents.update(user, agent_id, body)


@router.post("/{agent_id}/pause")
def pause(agent_id: str, user=Depends(workspace_user)):
    return agents.set_paused(user, agent_id, True)


@router.post("/{agent_id}/resume")
def resume(agent_id: str, user=Depends(workspace_user)):
    return agents.set_paused(user, agent_id, False)


@router.post("/{agent_id}/retire")
def retire(agent_id: str, user=Depends(workspace_user)):
    return agents.retire(user, agent_id)


@router.post("/{agent_id}/runs")
def run_now(agent_id: str, body: RunIn = RunIn(), user=Depends(workspace_user)):
    return agents.run_now(user, agent_id, body.prompt)


@router.post("/{agent_id}/tools/{name}/accept")
def accept_tools(agent_id: str, name: str, user=Depends(workspace_user)):
    return agents.accept_tools(user, agent_id, name)


@router.get("/{agent_id}/triggers")
def trigger_list(agent_id: str, user=Depends(workspace_user)):
    from .. import store
    with store.connect() as db:
        agents._row(db, user["id"], agent_id)
        return {"triggers": triggers.of_agent(db, user["id"], agent_id)}


@router.post("/{agent_id}/triggers")
def trigger_create(agent_id: str, body: dict[str, Any], user=Depends(workspace_user)):
    return triggers.create(user, agent_id, body)


@router.put("/{agent_id}/triggers/{trigger_id}")
def trigger_update(agent_id: str, trigger_id: str, body: dict[str, Any], user=Depends(workspace_user)):
    return triggers.update(user, agent_id, trigger_id, body)


@router.delete("/{agent_id}/triggers/{trigger_id}")
def trigger_delete(agent_id: str, trigger_id: str, user=Depends(workspace_user)):
    return triggers.delete(user, agent_id, trigger_id)


@router.post("/{agent_id}/triggers/{trigger_id}/run")
def trigger_run(agent_id: str, trigger_id: str, user=Depends(workspace_user)):
    return triggers.run_now(user, agent_id, trigger_id)


@hooks.post("/{hook_id}")
async def hook(hook_id: str, request: Request):
    # read to one byte past the most a delivery may be, so a larger one is told 413, not cut
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > triggers.MAX_DELIVERY:
            break
    headers = {k.lower(): v for k, v in request.headers.items()}
    from fastapi.concurrency import run_in_threadpool
    status, answer = await run_in_threadpool(triggers.deliver, hook_id, headers, body)
    return JSONResponse(answer, status_code=status)


@router.get("/{agent_id}/mcp-secrets")
def secrets_set(agent_id: str, user=Depends(workspace_user)):
    return agents.secrets_of(user, agent_id)


@router.put("/{agent_id}/mcp-secrets")
def secrets_put(agent_id: str, body: SecretsIn, user=Depends(workspace_user)):
    return agents.secrets_of(user, agent_id, body.secrets)
