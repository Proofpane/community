import { describe, expect, it } from "vitest";
import {
  barHeight, beforeCounting, candidatesOf, countingSince, countingWords, dayTotal, detailOf, detailPath, endedWords, groupsOf,
  loopOf, missedWords, notCandidate, proposalCountWords, proposalNotes, proposalOf, readNotes, readResultOf, recordStateOf,
  rowKey, versionWords, viewOf, whereWords, type Reads, type SkillProposal, type SkillRow,
} from "./skills";

// The Skills view's parsers, its grouping and its words (skills.ts), on answers shaped as the app's routes give
// them (proofpane_open/ade/skill_counts.py view and detail). Each answer is kept to the fields it names, each of its type.

const ENDED = { done: 1, failed: 0, stopped: 0, open: 0, unknown: 0 };
const row = (over: Partial<SkillRow> = {}): SkillRow => ({
  kind: "core", skill: "proofpane-core:verify", repo: null, calls: 3, failed: 1, typed: 1, sessions: 2, repos: 2, tasks: 1,
  requirements: 1, versions: 2, latest_version: "1.0.0+" + "a".repeat(64), first_at: 100, last_at: 200, ended: ENDED, ...over,
});

describe("the view's parser", () => {
  const answer = () => ({
    days: 30, from_day: "2026-09-21", to_day: "2026-10-20", now: 1_790_000_000,
    read: { at: 5, behind_at: null, error: null, error_at: null, unserved_at: null, missed: 1, dropped: 2, refused: 3, trimmed: 6, since_at: 4 },
    totals: { calls: 7, failed: 1, typed: 1, skills: 3, sessions: 3, first_at: 10, last_at: 20 },
    by_day: [{ day: "2026-10-19", core: 1, business: 2, other: 3 }],
    skills: [{ ...row(), extra: "never kept" }], more: 4, surprise: true,
  });

  it("keeps each field of its type and none the route does not name", () => {
    const v = viewOf(answer());
    expect(v.days).toBe(30);
    expect(v.skills[0]).toEqual(row());
    expect(v.by_day).toEqual([{ day: "2026-10-19", core: 1, business: 2, other: 3 }]);
    expect(v.read).toEqual({ at: 5, behind_at: null, error: null, error_at: null, unserved_at: null, missed: 1, dropped: 2, refused: 3, trimmed: 6, since_at: 4 });
    expect(v.totals.calls).toBe(7);
    expect(v.more).toBe(4);
    expect(Object.keys(v).sort()).toEqual(["by_day", "days", "from_day", "more", "now", "read", "skills", "to_day", "totals"]);
  });

  it("reads an answer of another shape as an empty view, not a break", () => {
    for (const bad of [null, undefined, "x", 5, [], {}, { skills: "no", by_day: 3, totals: 4, read: [] }]) {
      const v = viewOf(bad);
      expect(v.skills).toEqual([]);
      expect(v.by_day).toEqual([]);
      expect(v.read).toBeNull();
      expect(v.totals.calls).toBe(0);
      expect(v.days).toBe(30);
    }
  });

  it("takes a window the route does not give as the default, and a count that is not a number as none", () => {
    expect(viewOf({ days: 14 }).days).toBe(30);
    expect(viewOf({ days: 7 }).days).toBe(7);
    expect(viewOf({ days: 90 }).days).toBe(90);
    const v = viewOf({ ...answer(), skills: [{ ...row(), calls: "3", failed: -1, sessions: NaN, tasks: Infinity }] });
    expect(v.skills[0]).toMatchObject({ calls: 0, failed: 0, sessions: 0, tasks: 0 });
  });

  it("drops a row of no kind or no name, and a business row's repository alone is kept", () => {
    const v = viewOf({ skills: [{ ...row(), kind: "mystery" }, { ...row(), skill: 5 }, { ...row(), skill: null },
      { ...row({ kind: "business", repo: "alpha" }) }, { ...row({ kind: "core", repo: "alpha" }) },
      { ...row({ kind: "other", repo: "alpha" }) }, "x", null] });
    expect(v.skills.map((r) => [r.kind, r.repo])).toEqual([["business", "alpha"], ["core", null], ["other", null]]);
  });

  it("keeps a read that never happened as none, and a field of another type as none", () => {
    expect(viewOf({ read: null }).read).toBeNull();
    const reads = viewOf({ read: { at: "5", behind_at: {}, error: 5, error_at: "x", unserved_at: null, missed: "1", dropped: -2, refused: 1.5, trimmed: "6", since_at: [] } }).read as Reads;
    expect(reads).toEqual({ at: null, behind_at: null, error: null, error_at: null, unserved_at: null, missed: 0, dropped: 0, refused: 1.5, trimmed: 0, since_at: null });
  });

  it("keeps the tasks' ends as numbers, each of its own name", () => {
    const v = viewOf({ skills: [{ ...row(), ended: { done: 2, failed: 3, stopped: 4, open: 5, unknown: 6, extra: 7 } }] });
    expect(v.skills[0].ended).toEqual({ done: 2, failed: 3, stopped: 4, open: 5, unknown: 6 });
    expect(viewOf({ skills: [{ ...row(), ended: "x" }] }).skills[0].ended).toEqual({ done: 0, failed: 0, stopped: 0, open: 0, unknown: 0 });
  });
});

describe("the detail's parser", () => {
  const answer = () => ({
    kind: "business", skill: "deploy", repo: "alpha", days: 7, from_day: "a", to_day: "b", calls: 4, failed: 1, typed: 0,
    first_at: 1, last_at: 2, by_day: [{ day: "2026-10-19", calls: 2, failed: 1, extra: 1 }],
    versions: [{ version: "1".repeat(40), calls: 2, first_at: 1, last_at: 2 }, { version: null, calls: 1, first_at: null, last_at: null }],
    repos: [{ repo: "alpha", calls: 2 }, { repo: null, calls: 1 }],
    sessions: [{ session: "a".repeat(12), calls: 1, first_at: 1, last_at: 2 }],
    tasks: [{ task: "t1", calls: 1, last_at: 2, state: "done", requirement: "r1", ended_at: 3 }, { task: "t2", calls: 1, last_at: 2, state: null, requirement: null, ended_at: null }],
    requirements: [{ requirement: "r1", title: "Fix the login", calls: 2, tasks: 1 }, { requirement: "r2", title: null, calls: 1, tasks: 1 }],
    ended: ENDED,
  });

  it("keeps each list's fields of their types", () => {
    const d = detailOf(answer());
    expect(d.kind).toBe("business");
    expect(d.by_day).toEqual([{ day: "2026-10-19", calls: 2, failed: 1 }]);
    expect(d.versions[1]).toEqual({ version: null, calls: 1, first_at: null, last_at: null });
    expect(d.repos).toEqual([{ repo: "alpha", calls: 2 }, { repo: null, calls: 1 }]);
    expect(d.tasks[1]).toEqual({ task: "t2", calls: 1, last_at: 2, state: null, requirement: null, ended_at: null });
    expect(d.requirements[1].title).toBeNull();
    expect(d.ended).toEqual(ENDED);
  });

  it("reads an answer of another shape as an empty detail", () => {
    for (const bad of [null, "x", [], {}, { versions: "no", tasks: 3, ended: [] }]) {
      const d = detailOf(bad);
      expect(d.calls).toBe(0);
      expect(d.versions).toEqual([]);
      expect(d.tasks).toEqual([]);
      expect(d.ended).toEqual({ done: 0, failed: 0, stopped: 0, open: 0, unknown: 0 });
      expect(d.more).toEqual({ sessions: false, tasks: false, requirements: false, versions: false, repos: false });
    }
  });

  it("keeps which lists are cut as true only where the route says true", () => {
    const flags = (more: unknown) => detailOf({ ...answer(), more }).more;
    expect(flags({ sessions: true, tasks: true, requirements: true, versions: true, repos: true }))
      .toEqual({ sessions: true, tasks: true, requirements: true, versions: true, repos: true });
    expect(flags({ sessions: true, repos: true })).toEqual({ sessions: true, tasks: false, requirements: false, versions: false, repos: true });
    // anything but true itself is false, each flag read for itself: a number, a string, an array, an object, null
    expect(flags({ sessions: 1, tasks: "true", requirements: [], versions: "yes", repos: {} })).toEqual(
      { sessions: false, tasks: false, requirements: false, versions: false, repos: false });
    expect(flags({ sessions: null, tasks: 0, requirements: "", versions: false, repos: null })).toEqual(
      { sessions: false, tasks: false, requirements: false, versions: false, repos: false });
    expect(flags(undefined)).toEqual({ sessions: false, tasks: false, requirements: false, versions: false, repos: false });   // an older app
    expect(detailOf(answer()).more.sessions).toBe(false);
  });
});

describe("a read's result", () => {
  it("keeps one of the states the route gives, else a failure, and the lines taken", () => {
    for (const s of ["read", "behind", "unserved", "failed", "busy", "not_running"]) expect(readResultOf({ state: s, taken: 2 })).toEqual({ state: s, taken: 2 });
    expect(readResultOf({ state: "mystery", taken: 2 })).toEqual({ state: "failed", taken: 2 });
    expect(readResultOf(null)).toEqual({ state: "failed", taken: 0 });
    expect(readResultOf({ state: "read", taken: "2" })).toEqual({ state: "read", taken: 0 });
  });
});

describe("the detail's address", () => {
  it("names the kind, the skill with its colon encoded, the window and, for a business skill, its repository", () => {
    expect(detailPath({ kind: "core", skill: "proofpane-core:verify", repo: null }, 7))
      .toBe("/api/ade/skills/detail?kind=core&skill=proofpane-core%3Averify&days=7");
    expect(detailPath({ kind: "business", skill: "deploy", repo: "alpha" }, 30))
      .toBe("/api/ade/skills/detail?kind=business&skill=deploy&repo=alpha&days=30");
    // calls made in no repository: an empty repository, which the route reads as none
    expect(detailPath({ kind: "business", skill: "deploy", repo: null }, 90))
      .toBe("/api/ade/skills/detail?kind=business&skill=deploy&repo=&days=90");
    // a repository is of a business skill alone
    expect(detailPath({ kind: "other", skill: "plain", repo: "alpha" }, 7)).toBe("/api/ade/skills/detail?kind=other&skill=plain&days=7");
  });

  it("keeps two rows of one skill's name apart by kind and by repository", () => {
    const keys = new Set([rowKey(row()), rowKey(row({ kind: "other" })), rowKey(row({ kind: "business", repo: "a" })),
      rowKey(row({ kind: "business", repo: "b" })), rowKey(row({ kind: "business", repo: null }))]);
    expect(keys.size).toBe(5);
  });
});

describe("the grouping", () => {
  it("groups core, business by repository and other, a business group of the repository with the most calls first", () => {
    const rows = [
      row({ kind: "business", skill: "a", repo: "small", calls: 1 }), row({ kind: "core", skill: "c" }),
      row({ kind: "business", skill: "b", repo: "big", calls: 5 }), row({ kind: "other", skill: "o" }),
      row({ kind: "business", skill: "c", repo: "big", calls: 4 }), row({ kind: "business", skill: "d", repo: null, calls: 99 }),
      row({ kind: "business", skill: "e", repo: "mid", calls: 5 }),
    ];
    const g = groupsOf(rows);
    expect(g.core.map((r) => r.skill)).toEqual(["c"]);
    expect(g.other.map((r) => r.skill)).toEqual(["o"]);
    // big has 9, mid 5, small 1; the calls made in no repository come last whatever their number
    expect(g.business.map((b) => [b.repo, b.calls, b.rows.map((r) => r.skill)])).toEqual([
      ["big", 9, ["b", "c"]], ["mid", 5, ["e"]], ["small", 1, ["a"]], [null, 99, ["d"]]]);
  });

  it("orders repositories of as many calls by name, and keeps the rows' own order inside a group", () => {
    const g = groupsOf([row({ kind: "business", skill: "x", repo: "b", calls: 2 }), row({ kind: "business", skill: "y", repo: "a", calls: 2 }),
      row({ kind: "business", skill: "z", repo: "a", calls: 1 })]);
    expect(g.business.map((b) => b.repo)).toEqual(["a", "b"]);
    expect(g.business[0].rows.map((r) => r.skill)).toEqual(["y", "z"]);
  });

  it("is empty for no rows", () => {
    expect(groupsOf([])).toEqual({ core: [], business: [], other: [] });
  });
});

describe("a version in words", () => {
  const hex40 = "0123456789abcdef0123456789abcdef01234567";
  it("shows a commit by its first 12 characters and the words for one that is not a commit", () => {
    expect(versionWords("business", hex40)).toBe("0123456789ab");
    expect(versionWords("business", hex40 + hex40.slice(0, 24))).toBe("0123456789ab");
    expect(versionWords("business", "uncommitted")).toBe("uncommitted changes in its folder");
    expect(versionWords("business", "unknown")).toBe("not known");
    expect(versionWords("business", null)).toBe("none recorded");
  });
  it("shows a core skill's release with the first 12 characters of its folder's digest", () => {
    expect(versionWords("core", "1.0.0+" + "ab".repeat(32))).toBe("1.0.0+abababababab");
    expect(versionWords("core", "1.0.0+cut")).toBe("1.0.0 (its folder was too large to digest)");
    expect(versionWords("core", "1.0.0+unknown")).toBe("1.0.0 (its folder could not be read)");
    expect(versionWords("core", null)).toBe("none recorded");
  });
  it("says another skill has none, and shows anything else cut and as it is", () => {
    expect(versionWords("other", null)).toBe("none: only a repository's own skills and the core skills have one");
    const odd = "<b>" + "x".repeat(80);
    const shown = versionWords("core", odd);
    expect(shown.length).toBe(40);
    expect(shown.startsWith("<b>")).toBe(true);
    expect(versionWords("business", "not-hex-" + "y".repeat(60)).endsWith("...")).toBe(true);
  });
});

describe("the words for how tasks ended and where calls were made", () => {
  it("names the parts that are not none, in the order done, failed, stopped, open, not known", () => {
    expect(endedWords({ done: 2, failed: 1, stopped: 3, open: 4, unknown: 5 }))
      .toBe("2 done, 1 failed, 3 stopped, 4 still open, 5 not known to the app");
    expect(endedWords({ done: 0, failed: 0, stopped: 1, open: 0, unknown: 0 })).toBe("1 stopped");
    expect(endedWords({ done: 0, failed: 0, stopped: 0, open: 0, unknown: 0 })).toBe("none: no Manager task called it");
  });
  it("names the sessions, repositories, tasks and requirements, a business skill's row not its repositories", () => {
    expect(whereWords(row())).toBe("2 sessions · 2 repositories · 1 task · 1 requirement");
    expect(whereWords(row({ sessions: 1, repos: 1, tasks: 0, requirements: 0, kind: "other" }))).toBe("1 session · 1 repository · 0 tasks · 0 requirements");
    expect(whereWords(row({ kind: "business", repo: "alpha" }))).toBe("2 sessions · 1 task · 1 requirement");
  });
});

describe("what is known of the reads", () => {
  const reads = (over: Partial<Reads> = {}): Reads => ({ at: null, behind_at: null, error: null, error_at: null, unserved_at: null, missed: 0, dropped: 0, refused: 0, trimmed: 0, since_at: null, ...over });

  it("says the app has not read yet before it has", () => {
    expect(readNotes(null, null)).toEqual(["The app has not read the workspace's record of skill calls yet."]);
  });
  it("says when it last reached the end of the record", () => {
    const notes = readNotes(reads({ at: 1_790_000_000 }), null);
    expect(notes).toHaveLength(1);
    expect(notes[0]).toMatch(/^The app last read to the end of the workspace's record at /);
  });
  it("says a read failed, with the app's own words and since when", () => {
    const notes = readNotes(reads({ error: "The workspace answered 500.", error_at: 1_790_000_000 }), null);
    expect(notes[0]).toMatch(/^Reading the workspace's record failed \(since .+\): The workspace answered 500\.$/);
    expect(readNotes(reads({ error: "X" }), null)[0]).toBe("Reading the workspace's record failed: X");
  });
  it("says a workspace that does not count skill calls does not", () => {
    expect(readNotes(reads({ unserved_at: 5 }), null)[0]).toMatch(/^This workspace does not count skill calls yet/);
  });
  it("says the app is behind only while no read has reached the end since", () => {
    expect(readNotes(reads({ behind_at: 10, at: 5 }), null).join("\n")).toContain("has not yet read to the end");
    expect(readNotes(reads({ behind_at: 10 }), null).join("\n")).toContain("has not yet read to the end");
    expect(readNotes(reads({ behind_at: 10, at: 20 }), null).join("\n")).not.toContain("has not yet read to the end");
  });
  it("says what the read the owner asked for found: the machine not running, or a read already under way", () => {
    expect(readNotes(reads({ at: 1 }), { state: "not_running", taken: 0 }).join("\n")).toContain("The workspace is not running");
    expect(readNotes(reads({ at: 1 }), { state: "busy", taken: 0 }).join("\n")).toContain("A read is already under way");
    expect(readNotes(reads({ at: 1 }), { state: "read", taken: 3 }).join("\n")).not.toContain("not running");
  });

  it("says what is known to be missed, and that some misses leave no trace", () => {
    expect(missedWords(null)).toBe("None known to be missed so far, but a line lost to a cut, or a session removed before its file was read, leaves no trace.");
    expect(missedWords(reads())).toContain("None known to be missed so far");
    expect(missedWords(reads({ missed: 1 }))).toBe("Known to be missed so far: 1 call the workspace dropped before the app read it. Others leave no trace.");
    expect(missedWords(reads({ missed: 4, dropped: 1, refused: 2 })))
      .toBe("Known to be missed so far: 4 calls the workspace dropped before the app read them; 1 line the workspace held out of shape; 2 lines the app refused as out of shape. Others leave no trace.");
    expect(missedWords(reads({ trimmed: 1 }))).toBe("Known to be missed so far: 1 call the app dropped past the most it keeps, the oldest first. Others leave no trace.");
    expect(missedWords(reads({ missed: 1, dropped: 1, refused: 1, trimmed: 7 })))
      .toBe("Known to be missed so far: 1 call the workspace dropped before the app read it; 1 line the workspace held out of shape; 1 line the app refused as out of shape; 7 calls the app dropped past the most it keeps, the oldest first. Others leave no trace.");
  });
});

describe("a day's bar", () => {
  it("is a fraction of the tallest day, and never none for a day with calls", () => {
    expect(barHeight({ day: "d", core: 5, business: 0, other: 0 }, 10)).toBe(0.5);
    expect(barHeight({ day: "d", core: 1, business: 1, other: 1 }, 3)).toBe(1);
    expect(barHeight({ day: "d", core: 1, business: 0, other: 0 }, 1000)).toBe(0.04);
    expect(barHeight({ day: "d", core: 0, business: 0, other: 0 }, 10)).toBe(0);
    expect(barHeight({ day: "d", core: 0, business: 0, other: 0 }, 0)).toBe(0);
    expect(dayTotal({ day: "d", core: 1, business: 2, other: 3 })).toBe(6);
  });
});

describe("the days before the app began reading", () => {
  const reads = (over: Partial<Reads> = {}): Reads => ({
    at: null, behind_at: null, error: null, error_at: null, unserved_at: null, missed: 0, dropped: 0, refused: 0,
    trimmed: 0, since_at: null, ...over,
  });
  const NOON_OCT_4 = 1_791_115_200;                                        // 2026-10-04 12:00:00 UTC

  it("is the UTC day of the app's first read, none before it, and the last second of a day is that day's", () => {
    expect(countingSince(null)).toBeNull();
    expect(countingSince(reads())).toBeNull();
    expect(countingSince(reads({ since_at: NOON_OCT_4 }))).toBe("2026-10-04");
    expect(countingSince(reads({ since_at: 1_790_812_799 }))).toBe("2026-09-30");     // 2026-09-30 23:59:59
    expect(countingSince(reads({ since_at: 1_790_812_800 }))).toBe("2026-10-01");     // 2026-10-01 00:00:00
  });

  it("is none where the time is a number no date can show, and then marks no day and says nothing", () => {
    for (const since_at of [1e20, -1e20]) {
      const r = reads({ since_at });
      expect(countingSince(r)).toBeNull();
      expect(beforeCounting("2026-10-03", r)).toBe(false);
      expect(countingWords(r, "2026-10-01")).toBeNull();
    }
  });

  it("marks the days before it, and neither that day nor any after, nor any where the app has not read", () => {
    const r = reads({ since_at: NOON_OCT_4 });
    expect(beforeCounting("2026-10-03", r)).toBe(true);
    expect(beforeCounting("2026-09-01", r)).toBe(true);
    expect(beforeCounting("2026-10-04", r)).toBe(false);
    expect(beforeCounting("2026-10-05", r)).toBe(false);
    expect(beforeCounting("2026-10-03", reads())).toBe(false);
    expect(beforeCounting("2026-10-03", null)).toBe(false);
  });

  it("says so under the chart only where the window holds days before it", () => {
    const r = reads({ since_at: NOON_OCT_4 });
    const words = countingWords(r, "2026-10-01") ?? "";
    expect(words).toContain("The app began reading the workspace's record on 2026-10-04 (UTC).");
    expect(words).toContain("they hold only the calls the workspace still held at that first read (it keeps 30 days)");
    expect(countingWords(r, "2026-10-04")).toBeNull();                      // the window begins on that day
    expect(countingWords(r, "2026-10-05")).toBeNull();
    expect(countingWords(reads(), "2026-10-01")).toBeNull();
    expect(countingWords(null, "2026-10-01")).toBeNull();
  });
});

// The loop's answers, read as the counts are, and the words beside a
// proposal.
const PROPOSAL = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  id: "p1p2p3p4p5p6", repo: "alpha", folder: "deploy", path: ".claude/skills/deploy/SKILL.md", kind: "change",
  branch: "refs/heads/main", base: "a".repeat(40), head: "b".repeat(40), heads: [{ base: "c".repeat(40), head: "d".repeat(40), at: 5 }],
  current_version: "e".repeat(40), before_version: null, fresh: true, hypothesis: "Name the target first.",
  counts: { whole: { calls: 12, failed: 3, typed: 1, tasks_done: 2, tasks_failed: 1, tasks_stopped: 0, tasks_open: 0, tasks_unknown: 0,
    tasks_checks_failed: 1 }, current: null, before: null },
  state: "open", reason: null, pr_url: null, found: null, rolls_back: null, ended: null, created_at: 1, decided_at: 2,
  merged_at: null, expires_at: 3,
  notes: { keys: [], builds: [{ claude: "/usr/bin/claude", version: "2.1.0", newer: false, why: null }], newer: false, linked: true,
    url: null, critic: { specific: true, why: null }, builds_read: ["2.1.42", "2.1.310"],
    reads: [{ when: "review", at: 1, builds: [{ claude: "/usr/bin/claude", version: "2.1.0", newer: false, why: null }] }], merge: null },
  diff: ["@@ -1 +1 @@", "-a", "+b"], ways: ["merge", "remake", "reject"], bars_now: null, ...over,
});
const parsed = (over: Record<string, unknown> = {}) => proposalOf(PROPOSAL(over)) as SkillProposal;

describe("the loop's answers", () => {
  it("keeps a proposal's fields of their types, and draws none of a state or a way it does not know", () => {
    const p = parsed({ ways: ["merge", "push it", 3, "go_back"], extra: "never kept" });
    expect(p.ways).toEqual(["merge", "go_back"]);
    expect(p.heads).toEqual([{ base: "c".repeat(40), head: "d".repeat(40), at: 5 }]);
    expect(p.counts?.whole?.calls).toBe(12);
    expect(p.counts?.current).toBeNull();
    expect(p.notes.builds).toEqual([{ claude: "/usr/bin/claude", version: "2.1.0", newer: false, why: null }]);
    expect(p.notes.builds_read).toEqual(["2.1.42", "2.1.310"]);
    expect(p.notes.reads).toEqual([{ when: "review", at: 1, builds: [{ claude: "/usr/bin/claude", version: "2.1.0", newer: false,
      why: null }] }]);
    expect(p.notes.merge).toBeNull();
    expect(Object.keys(p)).not.toContain("extra");
    expect(proposalOf(PROPOSAL({ state: "applied" }))).toBeNull();
    expect(proposalOf(PROPOSAL({ id: "" }))).toBeNull();
    expect(parsed({ ended: "kept" }).ended).toBeNull();
    expect(parsed({ diff: ["+a", 4, null, "-b"] }).diff).toEqual(["+a", "-b"]);
  });

  it("keeps each read of the builds of a kind it knows, and a merge's words of their types", () => {
    const base = PROPOSAL().notes as Record<string, unknown>;
    const p = parsed({ notes: { ...base, builds_read: ["2.1.42", 7],
      reads: [{ when: "view", at: 9, builds: [{ claude: null, version: null, newer: "yes", why: "it did not answer" }] },
        { when: "later", at: 3, builds: [] }, "nonsense"],
      merge: { brought_besides: ["README.md", 4], checked: false, why: "the fetch failed" } } });
    expect(p.notes.builds_read).toEqual(["2.1.42"]);
    expect(p.notes.reads).toEqual([{ when: "view", at: 9, builds: [{ claude: null, version: null, newer: false, why: "it did not answer" }] }]);
    expect(p.notes.merge).toEqual({ brought_besides: ["README.md"], checked: false, why: "the fetch failed",
      first_parent_was_head: null, changed_to_blob: null });
    expect(parsed({ notes: { ...base, merge: { brought_besides: [] } } }).notes.merge).toEqual({ brought_besides: [], checked: true, why: null,
      first_parent_was_head: null, changed_to_blob: null });
    expect(parsed({ notes: { ...base, merge: { brought_besides: [], first_parent_was_head: false, changed_to_blob: "yes" } } }).notes.merge)
      .toEqual({ brought_besides: [], checked: true, why: null, first_parent_was_head: false, changed_to_blob: null });
  });

  it("reads the loop's list, its budget by kind, its switches and the copy, a proposal of no known state left out", () => {
    const loop = loopOf({
      proposals: [PROPOSAL(), PROPOSAL({ id: "q1q2q3q4q5q6", state: "nonsense" })],
      budget: { sessions: 3, usd: 1.5, max_sessions: 40, max_usd: 20, spent: false, by_kind: { review: 1, skill_review: 2, odd: "x" } },
      switches: [{ repo: "alpha", on: true, account: "acc1", model: "opus", url: "https://example.test/r.git", branch: "refs/heads/main",
        changed_at: 7 }],
      copy: { generation: 3, read_at: 9 },
    });
    expect(loop.proposals.map((p) => p.id)).toEqual(["p1p2p3p4p5p6"]);
    expect(loop.budget.by_kind).toEqual({ review: 1, skill_review: 2, odd: 0 });
    expect(loop.switches).toEqual([{ repo: "alpha", on: true, account: "acc1", model: "opus", url: "https://example.test/r.git",
      branch: "refs/heads/main", changed_at: 7 }]);
    expect(loop.copy).toEqual({ generation: 3, read_at: 9 });
    expect(loopOf("not an object")).toEqual({ proposals: [], budget: { sessions: 0, usd: 0, max_sessions: 0, max_usd: 0, spent: false,
      by_kind: {} }, switches: [], copy: null });
  });

  it("reads the record as the workspace holds it, and the candidates", () => {
    const r = recordStateOf({ running: true, record: { state: "held", held: "gone", word: true, generation: 2, error: null,
      refused: [{ at: 1, kind: "attempt", repo: "alpha", proposal: null, why: "held" }] }, copy: null });
    expect(r.record?.state).toBe("held");
    expect(r.record?.refused).toEqual([{ at: 1, kind: "attempt", repo: "alpha", proposal: null, why: "held" }]);
    expect(recordStateOf({ running: false, record: null, copy: null })).toEqual({ running: false, record: null, copy: null });
    const c = candidatesOf({ ok: true, why: null, candidates: [{ folder: "deploy", names: ["deploy", 3] }], not: { release: "why", odd: 4 } });
    expect(c).toEqual({ ok: true, why: null, candidates: [{ folder: "deploy", names: ["deploy"] }], not: { release: "why" } });
  });
});

describe("why a skill's row may not be proposed for", () => {
  const cands = candidatesOf({ ok: true, why: null, candidates: [{ folder: "deploy", names: ["deploy", "ship"] }],
    not: { release: "its SKILL.md names allowed-tools", ship: "a name of another folder's" } });

  it("says the workspace's reason for a folder that is no candidate, and none for one that is, by its folder or its names", () => {
    expect(notCandidate("release", cands)).toBe("its SKILL.md names allowed-tools");
    expect(notCandidate("deploy", cands)).toBeNull();
    expect(notCandidate("ship", cands)).toBeNull();                  // a name the candidate is called by
    expect(notCandidate("lonely", cands)).toBeNull();                // nothing said of it
    expect(notCandidate("toString", cands)).toBeNull();              // only the answer's own keys
  });

  it("says nothing on a row where nothing was read, or where no skill of the repository may be proposed for", () => {
    expect(notCandidate("release", null)).toBeNull();
    expect(notCandidate("release", { ...cands, ok: false, why: "its branch could not be read" })).toBeNull();
  });
});

describe("the words beside a proposal", () => {
  it("counts in words the parts that are not none", () => {
    expect(proposalCountWords(parsed().counts!.whole)).toBe("12 calls, 3 failed, 1 typed by a person, 1 task whose last checks did not pass, 1 task failed");
    expect(proposalCountWords(null)).toBe("no such version");
  });

  it("says the notes every proposal carries, and those that apply to this one", () => {
    const notes = proposalNotes(parsed());
    expect(notes.slice(0, 6)).toEqual([
      "Its counts are the sessions' word, for display; a task that called both versions is counted at each.",
      "Its hypothesis is a model's words.",
      "It was not tried: no sandbox replays a business skill's change, and nothing calls it tested.",
      "Its SKILL.md is read as the project's text, whoever wrote it: a session's work can have written it.",
      "Until you merge it, no start the workspace makes unattended in alpha, under any name the workspace's repositories give "
        + "it, begins from a base or a worktree holding its commit or, at its path, its text, byte for byte (the same "
        + "instructions written otherwise, with other line ends say, are not barred). A worktree is read just before its start "
        + "begins: a program that writes the text into it after that read is not seen. Your own New session, a session already "
        + "running, a copy of the text at another path, a copy of the repository and another repository are not covered.",
      "A skill with no construct still runs a command and reads a file its arguments name, and the gate never sees a command the "
        + "command scan runs.",
    ]);
    expect(notes).toContain("Its checks were read on the builds 2.1.42, 2.1.310; a session of another build may read the skill otherwise.");
    expect(notes).toContain("Its current version has no calls.");
    expect(notes.some((n) => n.startsWith("The skill holds"))).toBe(false);
    expect(notes.some((n) => n.includes("brought up to date"))).toBe(false);          // his own copy: linked
  });

  it("names the keys a skill holds, a newer build, a clone, a found proposal's bar now, and why one is outdated", () => {
    const p = parsed({ notes: { keys: ["hooks", "paths"], builds: [], newer: true, linked: false, url: "https://example.test/r.git",
      critic: null, builds_read: ["2.1.310"], reads: [{ when: "review", at: 1, builds: [{ claude: "/c", version: "2.1.311", newer: true }] }] } });
    const notes = proposalNotes(p);
    expect(notes).toContain("The skill holds hooks, paths: the change alters when they apply.");
    expect(notes).toContain("A newer build of Claude Code than those, 2.1.311, was found at the review: it may read the skill otherwise.");
    expect(notes).toContain("For a clone, sessions call a merged version only once brought up to date.");
    // the bar's sentence names what the shared repository has checked out, where the workspace read it, not the
    // proposal's branch (refs/heads/main here)
    const found = proposalNotes(parsed({ state: "found",
      bars_now: { refused: "a proposed change to its path", head: "f".repeat(40), branch: "refs/heads/side" } }));
    expect(found).toContain("It reached the branch without your tap: it is not your acceptance.");
    expect(found).toContain("Unattended starts from refs/heads/side in alpha are refused now: a proposed change to its path");
    expect(proposalNotes(parsed({ state: "found", bars_now: { refused: null, head: "f".repeat(40), branch: "refs/heads/side" } })))
      .toContain("Unattended starts from refs/heads/side in alpha are not refused now.");
    expect(proposalNotes(parsed({ state: "found", bars_now: { refused: null, head: "f".repeat(40), branch: null } })))
      .toContain("Unattended starts from the commit checked out in alpha are not refused now.");
    // the list views a repository once while it shows, so the note says when it read
    const at = Date.UTC(2026, 9, 27, 8, 5) / 1000;
    expect(proposalNotes(parsed({ state: "found", bars_now: { refused: "a proposed change to its path", head: "f".repeat(40),
      branch: "refs/heads/side", at } })))
      .toContain("Unattended starts from refs/heads/side in alpha are refused as read at 2026-10-27 08:05 UTC: a proposed change to its path");
    expect(proposalNotes(parsed({ state: "found", bars_now: { refused: null, head: "f".repeat(40), branch: "refs/heads/side", at } })))
      .toContain("Unattended starts from refs/heads/side in alpha are not refused as read at 2026-10-27 08:05 UTC.");
    expect(proposalNotes(parsed({ state: "outdated", reason: "its skill changed on the branch" }))).toContain("Why: its skill changed on the branch");
    expect(proposalNotes(parsed({ state: "merged" }))).toContain("Your hooks did not run for this merge.");
  });

  // what each note of a proposal says, and of what alone
  it("says the hypothesis is a model's words only for a change, a proposal to go back having none of a model's", () => {
    expect(proposalNotes(parsed())).toContain("Its hypothesis is a model's words.");
    expect(proposalNotes(parsed({ kind: "back", hypothesis: "It failed more often since." }))).not.toContain("Its hypothesis is a model's words.");
  });

  it("names the commit alone as what bars a proposal whose text the branch held before", () => {
    const bar = (p: SkillProposal) => proposalNotes(p).find((n) => n.startsWith("Until you merge it")) ?? "";
    expect(bar(parsed({ fresh: false }))).toContain("holding its commit (its text is one the branch held before, so the text alone bars nothing).");
    expect(bar(parsed({ fresh: false }))).not.toContain("at its path, its text");
    expect(bar(parsed({ fresh: true }))).toContain("holding its commit or, at its path, its text, byte for byte");
    // the worktree's read, which only a fresh text's bar makes
    expect(bar(parsed({ fresh: true }))).toContain("A worktree is read just before its start begins");
    expect(bar(parsed({ fresh: false }))).not.toContain("A worktree is read");
  });

  it("says, for each read of the builds, any newer build found and any claude whose version could not be read, and when", () => {
    const notes = proposalNotes(parsed({ notes: { ...(PROPOSAL().notes as Record<string, unknown>), reads: [
      { when: "review", at: 1, builds: [{ claude: "/c", version: "2.1.274", newer: false }] },
      { when: "view", at: 1793045221, builds: [{ claude: "/c", version: "2.1.311", newer: true },
        { claude: "/t", version: null, newer: false, why: "it timed out" }] },
      { when: "acceptance", at: 1793045300, builds: [{ claude: null, version: null, newer: false }] }] } }));
    expect(notes).toContain("A newer build of Claude Code than those, 2.1.311, was found at the view of 2026-10-26 20:07 UTC: it may read the skill otherwise.");
    expect(notes).toContain("At the view of 2026-10-26 20:07 UTC, a claude's version could not be read (/t): it timed out.");
    expect(notes).toContain("At the acceptance, a claude's version could not be read: it did not say.");
    expect(notes.some((n) => n.includes("found at the review"))).toBe(false);          // the review's build is not newer
  });

  it("says the current version has no calls, and why for a clone, and nothing where it has calls or no counts were read", () => {
    const one = { calls: 1, failed: 0, typed: 0, tasks_done: 0, tasks_failed: 0, tasks_stopped: 0, tasks_open: 0, tasks_unknown: 0,
      tasks_checks_failed: 0 };
    const counts = (current: unknown) => ({ ...(PROPOSAL().counts as Record<string, unknown>), current });
    expect(proposalNotes(parsed({ counts: counts({ ...one, calls: 0 }) }))).toContain("Its current version has no calls.");
    const clone = proposalNotes(parsed({ counts: counts(null), notes: { ...(PROPOSAL().notes as Record<string, unknown>), linked: false } }));
    expect(clone).toContain("Its current version has no calls: a clone's sessions call a newer version only once brought up to date.");
    expect(proposalNotes(parsed({ counts: counts(one) })).some((n) => n.startsWith("Its current version"))).toBe(false);
    expect(proposalNotes(parsed({ counts: null })).some((n) => n.startsWith("Its current version"))).toBe(false);
  });

  it("says what a merge on GitHub brought besides, or that it was not checked, and nothing of it for a merge into his copy", () => {
    const notes = (merge: unknown, linked = false) => proposalNotes(parsed({ state: "merged",
      notes: { ...(PROPOSAL().notes as Record<string, unknown>), linked, merge } }));
    expect(notes({ brought_besides: ["README.md", "src/a.py"] })).toContain("The merge on GitHub brought besides: README.md, src/a.py.");
    expect(notes({ checked: false, why: "the fetch failed" })).toContain("What the merge on GitHub brought was not checked: the fetch failed.");
    expect(notes({ brought_besides: [] }).some((n) => n.includes("merge on GitHub"))).toBe(false);
    expect(notes({ brought_besides: ["README.md"] }, true).some((n) => n.includes("merge on GitHub"))).toBe(false);
  });

  it("says where a merge on GitHub did not have the head before as its first parent, or did not change the path to the text", () => {
    // the two checks after a merge on GitHub
    const notes = (merge: unknown) => proposalNotes(parsed({ state: "merged",
      notes: { ...(PROPOSAL().notes as Record<string, unknown>), linked: false, merge } }));
    const first = "The merge on GitHub's commit does not have the branch's head before it as its first parent.";
    const onto = `The merge on GitHub did not change ${PROPOSAL().path} to the proposal's text.`;
    expect(notes({ brought_besides: [], first_parent_was_head: false, changed_to_blob: true })).toContain(first);
    expect(notes({ brought_besides: [], first_parent_was_head: true, changed_to_blob: false })).toContain(onto);
    const fine = notes({ brought_besides: [], first_parent_was_head: true, changed_to_blob: true });
    expect(fine.includes(first) || fine.includes(onto)).toBe(false);
    const unchecked = notes({ checked: false, why: "git could not compare", first_parent_was_head: false, changed_to_blob: false });
    expect(unchecked.includes(first) || unchecked.includes(onto)).toBe(false);          // not checked: neither is said
    expect(notes({ brought_besides: [] }).some((n) => n.includes("merge on GitHub"))).toBe(false);   // an older answer
  });
});
