"""A live run's anomaly rules, evaluated by the workspace as the run's events
come, each with a warning and a critical level:

- tool_repetition: the same tool called with the same input: a warning at 5 such calls in a run, critical at 8;
- no_progress: calls in a row that change nothing and use no tool not used before in the run: a warning at 8, critical
  at 15. A call of a tool that writes (Write, Edit, MultiEdit, NotebookEdit) or of Bash, which may, counts as a change:
  an approximation from what a call names, not from the worktree;
- context_budget: the run's context past 80% of its compaction point: a warning only;
- spend: the run's estimated cost so far against its agent's median run cost over 7 days: a warning at 3 times, critical
  at 6; nothing before the agent has a median;
- time_budget: 80% of the run's time limit gone: a warning only, since the limit itself ends the run.

Each rule fires once a level a run, with its evidence. A warning is pushed to the owner; a critical one, for an agent
whose anomaly_pause is on, pauses the run for the owner's word (agents.py)."""

import hashlib
import json
import time

REPEAT = (5, 8)
NO_PROGRESS = (8, 15)
CONTEXT_WARN = 0.8
SPEND = (3, 6)
TIME_WARN = 0.8
CHANGES = ("Write", "Edit", "MultiEdit", "NotebookEdit", "Bash")
LEVELS = ("warning", "critical")


def input_hash(given):
    return hashlib.sha256(json.dumps(given, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


class Watch:
    """One run's rules as it goes."""

    def __init__(self, started_at=None, max_run_seconds=None, compaction_window=None, median_usd=None):
        self.started_at = started_at if started_at is not None else time.time()
        self.max_run_seconds = max_run_seconds
        self.compaction_window = compaction_window
        self.median_usd = median_usd if isinstance(median_usd, (int, float)) and median_usd > 0 else None
        self.repeats = {}
        self.tools_seen = set()
        self.still = 0
        self.fired = {}

    def _fire(self, rule, level, evidence, now=None):
        """The anomaly, when it is a level higher than this rule fired before in the run; else None."""
        before = self.fired.get(rule)
        if before is not None and LEVELS.index(level) <= LEVELS.index(before):
            return None
        self.fired[rule] = level
        return {"rule": rule, "level": level, "at": int(now if now is not None else time.time()), "evidence": evidence}

    def tool(self, name, given, now=None):
        """A tool call: the anomalies it makes."""
        out = []
        key = (str(name), input_hash(given))
        count = self.repeats[key] = self.repeats.get(key, 0) + 1
        for level, at in zip(LEVELS, REPEAT):
            if count == at:
                got = self._fire("tool_repetition", level, f"{name} called {count} times with the same input", now)
                out += [got] if got else []
        if str(name) in CHANGES or str(name) not in self.tools_seen:
            self.still = 0
        else:
            self.still += 1
        self.tools_seen.add(str(name))
        for level, at in zip(LEVELS, NO_PROGRESS):
            if self.still == at:
                got = self._fire("no_progress", level, f"{self.still} calls in a row changed nothing and used no "
                                                        "tool new to the run", now)
                out += [got] if got else []
        return out

    def turn(self, context_tokens=None, cost_usd=None, now=None):
        """A turn ended, with the run's context and its estimated cost so far: the anomalies it makes."""
        out = []
        if self.compaction_window and isinstance(context_tokens, (int, float)) and \
                context_tokens >= CONTEXT_WARN * self.compaction_window:
            share = round(100 * context_tokens / self.compaction_window)
            got = self._fire("context_budget", "warning", f"context at {share}% of its compaction point", now)
            out += [got] if got else []
        if self.median_usd and isinstance(cost_usd, (int, float)):
            ratio = round(cost_usd / self.median_usd, 6)          # 0.30 over 0.10 is 3, not 2.9999...
            for level, times in zip(LEVELS, SPEND):
                if ratio >= times:
                    got = self._fire("spend", level, f"est. ${cost_usd:.2f} so far, {ratio:.1f} times the agent's "
                                                     f"median run of ${self.median_usd:.2f}", now)
                    out += [got] if got else []
        return out + self.tick(now)

    def tick(self, now=None):
        """The time gone: the anomaly it makes, if any."""
        now = now if now is not None else time.time()
        if self.max_run_seconds and now - self.started_at >= TIME_WARN * self.max_run_seconds:
            got = self._fire("time_budget", "warning", f"{int(now - self.started_at)} s of its "
                                                       f"{self.max_run_seconds} s gone", now)
            return [got] if got else []
        return []
