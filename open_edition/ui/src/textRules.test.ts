import { describe, expect, it } from "vitest";
import { allShown, charactersRule, eventText, hides, historyFields, MARKS_MAX, questionRule, refusalWords, stackedMarks, talkText, withinRule, type Checked, type Refusal } from "./textRules";

// Characters that do not show as themselves are written by code point, so no editor or transport turns them into
// themselves; the expected outcomes are those of the server's own functions on the same values (link._history_row,
// link_commands.check_text).
const cp = (code: number) => String.fromCodePoint(code);
const NBSP = cp(0x00a0);
const ZWSP = cp(0x200b);
const VS16 = cp(0xfe0f);
const HEART = cp(0x2764);
const why = <T,>(c: Checked<T>): Refusal | null => (c.ok ? null : c.refusal);

describe("what does not show itself, as the server's link.hides", () => {
  it("is every control, format character, separator but the space, surrogate and unassigned code point", () => {
    expect(hides(" ", null)).toBe(false);
    expect(hides("a", null)).toBe(false);
    for (const code of [0x00a0, 0x3000, 0x2007, 0x0085, 0x0009, 0x200b, 0x202e, 0xfeff, 0xe000, 0x0378, 0xe0041, 0x2028]) {
      expect(hides(cp(code), null), code.toString(16)).toBe(true);
    }
    expect(hides(String.fromCharCode(0xd800), null)).toBe(true);
  });

  it("is the characters drawn as nothing, and the variation selectors but VS15 and VS16 after a character", () => {
    for (const code of [0x034f, 0x115f, 0x1160, 0x17b4, 0x17b5, 0x180b, 0x180f, 0x2800, 0x3164, 0xffa0, 0xfe00, 0xfe0d, 0xe0100]) {
      expect(hides(cp(code), 0x61), code.toString(16)).toBe(true);
    }
    expect(hides(VS16, 0x2764)).toBe(false);
    expect(hides(VS16, null)).toBe(true);                    // at the start
    expect(hides(VS16, 0xfe0e)).toBe(true);                  // right after another
    expect(hides(cp(0x1f600), null)).toBe(false);
  });

  it("holds a History field to no line break and no tab either", () => {
    expect(allShown("a b")).toBe(true);
    expect(allShown(`heart ${HEART}${VS16}`)).toBe(true);
    for (const t of ["a\tb", "a\nb", "a\rb", `a${NBSP}b`, `${VS16}start`]) expect(allShown(t), JSON.stringify(t)).toBe(false);
  });
});

describe("a History row's sealed fields, as the server's _history_row holds them", () => {
  const listed = (over: Record<string, unknown> = {}) => ({ title: "Fix the sign-in", project: "~/work/app", branch: "main", repositories: ["~/work/app"], ...over });

  it("give the row's title, project, branch and repositories", () => {
    expect(historyFields(listed(), false)).toEqual({ ok: true, value: { title: "Fix the sign-in", project: "~/work/app", branch: "main", repositories: ["~/work/app"] } });
    expect(historyFields({ title: "only a title" }, false)).toEqual({ ok: true, value: { title: "only a title", project: null, branch: null, repositories: null } });
    expect(historyFields(listed({ project: null, branch: null, repositories: null }), false).ok).toBe(true);
  });

  it("refuse a title, branch or project with a character that does not show itself", () => {
    expect(why(historyFields(listed({ title: `a${NBSP}b` }), false))).toEqual({ why: "hidden" });
    expect(why(historyFields(listed({ branch: "a\tb" }), false))).toEqual({ why: "hidden" });
    expect(why(historyFields(listed({ project: `~/a${ZWSP}` }), false))).toEqual({ why: "hidden" });
    expect(why(historyFields(listed({ title: `${VS16}start` }), false))).toEqual({ why: "hidden" });
    expect(why(historyFields(listed({ title: "a\u{0000}b" }), false))).toEqual({ why: "hidden" });
    expect(historyFields(listed({ title: `${cp(0x1f600)} ok` }), false).ok).toBe(true);
    expect(historyFields(listed({ title: `heart ${HEART}${VS16}` }), false).ok).toBe(true);
    expect(historyFields(listed({ title: "e\u{0301} decomposed" }), false).ok).toBe(true);   // a title is not held to NFC
  });

  it("refuse one past its length, counted by character as Python counts", () => {
    expect(historyFields(listed({ title: "x".repeat(120) }), false).ok).toBe(true);
    expect(historyFields(listed({ title: cp(0x1f600).repeat(120) }), false).ok).toBe(true);   // 240 UTF-16 units
    expect(why(historyFields(listed({ title: "x".repeat(121) }), false))).toEqual({ why: "rule", rule: "it is longer than 120 characters" });
    expect(why(historyFields(listed({ branch: "b".repeat(121) }), false))).toEqual({ why: "rule", rule: "it is longer than 120 characters" });
    expect(historyFields(listed({ project: "p".repeat(300) }), false).ok).toBe(true);
    expect(why(historyFields(listed({ project: "p".repeat(301) }), false))).toEqual({ why: "rule", rule: "it is longer than 300 characters" });
  });

  it("refuse an empty title as Python's strip reads empty, and a title shaped like a secret", () => {
    expect(why(historyFields(listed({ title: "   " }), false))).toEqual({ why: "rule", rule: "it is empty" });
    expect(why(historyFields(listed({ title: cp(0x85) }), false))).toEqual({ why: "rule", rule: "it is empty" });   // a space to Python, not to trim()
    expect(why(historyFields(listed({ title: cp(0xfeff) }), false))).toEqual({ why: "hidden" });                     // a space to trim(), not to Python
    expect(why(historyFields(listed({ title: "sk-ant-xyz" }), false))).toEqual({ why: "rule", rule: "it holds something shaped like an Anthropic key (sk-ant-)" });
    for (const token of ["~/ghp_x", "~/ghs_x"]) {     // GitHub's tokens are one shape, its server-to-server ones among them
      expect(why(historyFields(listed({ project: token }), false)), token).toEqual({ why: "rule", rule: "it holds something shaped like a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)" });
    }
    expect(historyFields(listed({ project: "" }), false)).toMatchObject({ ok: true, value: { project: "" } });          // optional: may be empty
  });

  it("leave out a repository the rules refuse, or one named twice, alone, and keep at most twenty", () => {
    const got = historyFields(listed({ repositories: ["~/a", "~/a", `~/b${ZWSP}`, 5, "", "~/c", "s".repeat(301)] }), false);
    expect(got).toEqual({ ok: true, value: expect.objectContaining({ repositories: ["~/a", "~/c"] }) });
    const many = Array.from({ length: 25 }, (_, i) => `~/r${i}`);
    expect(historyFields(listed({ repositories: many }), false)).toEqual({ ok: true, value: expect.objectContaining({ repositories: many.slice(0, 20) }) });
    expect(historyFields(listed({ repositories: [`~/b${ZWSP}`] }), false)).toEqual({ ok: true, value: expect.objectContaining({ repositories: null }) });
  });

  it("are not what a row seals when their shape or types are other", () => {
    for (const bad of [listed({ repositories: "~/a" }), listed({ extra: 1 }), { project: "~/p" }, listed({ title: 5 }),
      listed({ title: null }), listed({ branch: ["main"] }), listed({ project: 7 }), [listed()], "text", null]) {
      expect(why(historyFields(bad, false)), JSON.stringify(bad)).toEqual({ why: "unreadable" });
    }
  });

  it("are a withheld row's folder alone", () => {
    expect(historyFields({ project: "~/secret" }, true)).toEqual({ ok: true, value: { title: null, project: "~/secret", branch: null, repositories: null } });
    expect(historyFields({ project: null }, true)).toEqual({ ok: true, value: { title: null, project: null, branch: null, repositories: null } });
    expect(why(historyFields({ project: "~/p", title: "t" }, true))).toEqual({ why: "unreadable" });
    expect(why(historyFields({ project: "~/p", repositories: ["~/p"] }, true))).toEqual({ why: "unreadable" });
    expect(why(historyFields({ project: `~/p${NBSP}` }, true))).toEqual({ why: "hidden" });
    expect(why(historyFields({ project: "p".repeat(301) }, true))).toEqual({ why: "rule", rule: "it is longer than 300 characters" });
    expect(why(historyFields(listed(), true))).toEqual({ why: "unreadable" });          // a listed row's fields, sealed as withheld
  });
});

describe("a question's words, as the server's check_text holds them", () => {
  it("let through what the server takes", () => {
    for (const q of ["fine?", `a${NBSP}b`, "tab\tand\nline", "x".repeat(4000), cp(0x1f600).repeat(4000), `ship it ${HEART}${VS16}`]) {
      expect(questionRule(q), q.slice(0, 12)).toBeNull();
    }
  });

  it("refuse what it refuses, each by its rule", () => {
    expect(questionRule("  ")).toEqual({ why: "rule", rule: "it is empty" });
    expect(questionRule(cp(0x85))).toEqual({ why: "rule", rule: "it is empty" });
    expect(questionRule("x".repeat(4001))).toEqual({ why: "rule", rule: "it is longer than 4,000 characters" });
    for (const q of ["a\rb", `a${cp(0x85)}b`, "a\u{0007}b", `a${cp(0x7f)}b`, `a${ZWSP}b`, `a${String.fromCharCode(0xd800)}b`, `a${cp(0x202e)}b`, `${VS16}x`]) {
      expect(questionRule(q), JSON.stringify(q)).toEqual({ why: "hidden" });
    }
    expect(questionRule("cafe\u{0301}")).toEqual({ why: "rule", rule: "it is not in Unicode normal form C" });
    expect(questionRule("which key, sk-ant-1?")).toEqual({ why: "rule", rule: "it holds something shaped like an Anthropic key (sk-ant-)" });
  });

  it("refuse a mark drawn over or around a character, as the server does, and take an ordinary one", () => {
    for (const code of [0x0336, 0x0337, 0x0338, 0x20dd, 0x20de, 0x20df, 0x20e0, 0x20e2, 0x20e3, 0x20e4, 0x0488, 0x1abe, 0xa670, 0x1d167]) {
      expect(questionRule(`may I merge x${cp(code)}?`), code.toString(16)).toEqual({ why: "rule", rule: "it holds a mark drawn over or around a character, which could change how it reads" });
    }
    for (const code of [0x0301, 0x0323, 0x20e1, 0x0e48]) expect(questionRule(`x${cp(code)} now?`), code.toString(16)).toBeNull();
  });
});

const HIDDEN_WHY = { why: "hidden" };
const DRAWN_WHY = { why: "rule", rule: "it holds a mark drawn over or around a character, which could change how it reads" };
const FORM_WHY = { why: "rule", rule: "it is not in Unicode normal form C" };

describe("a text's characters, as the server's check_characters holds them", () => {
  it("are asked nothing of the text's size, of whether it begins as an envelope does, or of the shapes", () => {
    for (const t of ["", "  ", "x".repeat(4001), "e2e1.abc", "which key, sk-ant-1?", "tab\tand\nline", `ship it ${HEART}${VS16}`, `a${NBSP}b`,
      `caf${cp(0xe9)}`, cp(0x1f600), cp(0x0301), `x${cp(0x0323)}`]) {
      expect(charactersRule(t), JSON.stringify(t.slice(0, 12))).toBeNull();
    }
  });

  it("refuse what the rule refuses, each by its clause, as a question's words do", () => {
    for (const t of ["a\rb", `a${cp(0x85)}b`, "a\u{0007}b", "\u{0000}", `a${cp(0x7f)}b`, `a${ZWSP}b`, `a${String.fromCharCode(0xd800)}b`, `a${cp(0x202e)}b`,
      `${VS16}x`, VS16, `x${VS16}${VS16}`, `a${cp(0x2028)}b`, cp(0xe000), cp(0xfe00)]) {
      expect(charactersRule(t), JSON.stringify(t)).toEqual(HIDDEN_WHY);
      expect(questionRule(t), JSON.stringify(t)).toEqual(HIDDEN_WHY);
    }
    for (const code of [0x0336, 0x0338, 0x20dd, 0x0488]) {
      expect(charactersRule(`x${cp(code)}`), code.toString(16)).toEqual(DRAWN_WHY);
      expect(charactersRule(cp(code)), code.toString(16)).toEqual(DRAWN_WHY);
    }
    expect(charactersRule("cafe\u{0301}")).toEqual(FORM_WHY);
    expect(charactersRule(`${cp(0x1100)}${cp(0x1161)}`)).toEqual(FORM_WHY);
    // the first clause a text breaks is the one said, in the rule's order
    expect(charactersRule(`e${cp(0x0301)}${cp(0x0338)}${ZWSP}`)).toEqual(HIDDEN_WHY);
    expect(charactersRule(`e${cp(0x0301)}${cp(0x0338)}`)).toEqual(DRAWN_WHY);
  });
});

describe("the texts and names within what an item's JSON holds", () => {
  const bad = `x${cp(0x0338)}`;

  it("are each held to the rule on characters, a name as much as a text, at any depth, in a list or in an object", () => {
    for (const v of [bad, [bad], { a: bad }, { [bad]: 1 }, { a: [1, { b: [[bad]] }] }, { a: { b: { [bad]: null } } }, [[], {}, [{ a: 1, b: "ok", c: [bad] }]],
      { tool: "Bash", input: { command: "ls" }, session_id: bad }, { __proto__: null, a: bad }, JSON.parse(`{"__proto__":{"a":"x\\u0338"}}`),
      JSON.parse(`{"a":"ok","a":"x\\u0338"}`)]) {
      expect(withinRule(v), JSON.stringify(v)).toEqual(DRAWN_WHY);
    }
    expect(withinRule({ a: "ok", b: ["cafe\u{0301}"] })).toEqual(FORM_WHY);
    expect(withinRule({ [`${VS16}a`]: "ok" })).toEqual(HIDDEN_WHY);
    expect(withinRule(["a\rb"])).toEqual(HIDDEN_WHY);
  });

  it("are held as the texts they are: one that begins with a selector is refused wherever it stands, and an accent is not joined to the text before it", () => {
    expect(withinRule([HEART, VS16])).toEqual(HIDDEN_WHY);
    expect(withinRule({ [HEART]: `${VS16}x` })).toEqual(HIDDEN_WHY);
    expect(withinRule([`${HEART}${VS16}`, `a${VS16}`])).toBeNull();
    expect(withinRule(["cafe", cp(0x0301)])).toBeNull();
    expect(withinRule({ e: cp(0x0301) })).toBeNull();
  });

  it("ask nothing of what is no text, of an empty text or name, nor of what the rule lets through", () => {
    for (const v of [null, undefined, 7, 1.5, true, false, [], {}, "", [""], { "": "" }, { a: [1, 2, { b: null }] }, "x".repeat(5000), ["e2e1.abc", "sk-ant-1"],
      { command: "printf 'a\\tb'\n\tdone", description: `caf${cp(0xe9)} ${HEART}${VS16} ${cp(0x1f600)}` }]) {
      expect(withinRule(v), JSON.stringify(v)?.slice(0, 40)).toBeNull();
    }
  });

  it("say the first text or name that breaks the rule, in the order the value gives them", () => {
    expect(withinRule(["cafe\u{0301}", bad])).toEqual(FORM_WHY);
    expect(withinRule([bad, "cafe\u{0301}"])).toEqual(DRAWN_WHY);
    expect(withinRule({ a: { [`${VS16}k`]: bad }, b: "cafe\u{0301}" })).toEqual(HIDDEN_WHY);
    expect(withinRule({ a: [bad], [`${VS16}k`]: 1 })).toEqual(DRAWN_WHY);
  });

  it("are read at the deepest a text of 4,000 characters nests one that breaks the rule", () => {
    // an opened text is at most 4,000 characters (eventText). A list takes two of them and an object, its name empty,
    // five; a text that breaks the rule takes three, its quotation marks with it: so 1,998 lists or 799 objects
    // around one, and one more of either is past 4,000
    const mark = cp(0x0338);
    const lists = (n: number, inner: string) => `${"[".repeat(n)}${inner}${"]".repeat(n)}`;
    const objects = (n: number, inner: string) => `${'{"":'.repeat(n)}${inner}${"}".repeat(n)}`;
    expect(lists(1998, `"${mark}"`)).toHaveLength(3999);
    expect(lists(1999, `"${mark}"`).length).toBeGreaterThan(4000);
    expect(objects(799, `"${mark}"`)).toHaveLength(3998);
    expect(objects(800, `"${mark}"`).length).toBeGreaterThan(4000);
    expect(withinRule(JSON.parse(lists(1998, `"${mark}"`)))).toEqual(DRAWN_WHY);
    expect(withinRule(JSON.parse(lists(1998, `"ok"`)))).toBeNull();
    expect(withinRule(JSON.parse(objects(799, `"${mark}"`)))).toEqual(DRAWN_WHY);
    expect(withinRule(JSON.parse(objects(799, `"ok"`)))).toBeNull();
    // and a name at the bottom, written as an escape
    expect(withinRule(JSON.parse(objects(797, `{"x\\u0338":1}`)))).toEqual(DRAWN_WHY);
  });
});

describe("marks stacked on one character", () => {
  it("are found past MARKS_MAX, as the server's link_commands.stacked finds them", () => {
    expect(MARKS_MAX).toBe(2);
    expect(stackedMarks(`ok x${cp(0x0323)}${cp(0x0301)}`)).toBeNull();
    expect(stackedMarks(`ok x${cp(0x0301).repeat(3)} and more`)).toBe("x");
    expect(stackedMarks(`${cp(0x0301).repeat(3)}x`)).toBe(cp(0x0301));
    expect(stackedMarks(`a${cp(0x0301)}b${cp(0x0301)}${cp(0x0301)}`)).toBeNull();     // two runs of one and two
  });
});

describe("an event's sealed text", () => {
  it("is its text when its shape is the one a link seals, the session and time null or absent", () => {
    expect(eventText({ text: "ran the tests", session: null, said_at: null }, {})).toEqual({ ok: true, value: "ran the tests" });
    expect(eventText({ text: "ran the tests" }, {})).toEqual({ ok: true, value: "ran the tests" });
    for (const bad of [{ text: 5 }, { text: "x", extra: 1 }, { session: null }, ["x"], "x", null, { text: "x", session: "bad id!" },
      { text: "x", said_at: 1.5 }, { text: "x", said_at: -1 }, { text: "x", said_at: "1" }]) {
      expect(why(eventText(bad, {})), JSON.stringify(bad)).toEqual({ why: "unreadable" });
    }
  });

  it("says of its session and of when it was said what the server serves beside it", () => {
    const note = { mirror: "owner", session: "s1", said_at: 1_790_000_000_000 };
    expect(eventText({ text: "hi", session: "s1", said_at: 1_790_000_000_000 }, note).ok).toBe(true);
    expect(why(eventText({ text: "hi", session: "s2", said_at: 1_790_000_000_000 }, note))).toEqual({ why: "unreadable" });
    expect(why(eventText({ text: "hi", session: null, said_at: 1_790_000_000_000 }, note))).toEqual({ why: "unreadable" });
    expect(why(eventText({ text: "hi", session: "s1", said_at: 1_790_000_000_001 }, note))).toEqual({ why: "unreadable" });
    expect(why(eventText({ text: "hi", session: "s1", said_at: null }, note))).toEqual({ why: "unreadable" });
    expect(why(eventText({ text: "hi", session: null, said_at: 5 }, { mirror: "owner" }))).toEqual({ why: "unreadable" });
    // a time is whole milliseconds, whatever the server serves beside it
    expect(why(eventText({ text: "hi", session: null, said_at: 1.5 }, { mirror: "owner", said_at: 1.5 }))).toEqual({ why: "unreadable" });
  });

  it("is held to the server's rule for an event's text in the clear: not empty, at most 4,000 characters, no NUL, nothing shaped like a secret", () => {
    expect(eventText({ text: "x".repeat(4000) }, {}).ok).toBe(true);
    expect(why(eventText({ text: "x".repeat(4001) }, {}))).toEqual({ why: "rule", rule: "it is longer than 4,000 characters" });
    expect(why(eventText({ text: cp(0x1f600).repeat(4001) }, {}))).toEqual({ why: "rule", rule: "it is longer than 4,000 characters" });
    expect(eventText({ text: cp(0x1f600).repeat(4000) }, {}).ok).toBe(true);                // counted by code point
    expect(why(eventText({ text: " \n" }, {}))).toEqual({ why: "rule", rule: "it is empty" });
    expect(why(eventText({ text: `a${cp(0)}b` }, {}))).toEqual({ why: "hidden" });
    expect(why(eventText({ text: "the key is sk-ant-xyz" }, {}))).toEqual({ why: "rule", rule: "it holds something shaped like an Anthropic key (sk-ant-)" });
  });

  it("may name a session the server did not tag, on a mirrored note alone", () => {
    expect(eventText({ text: "hi", session: "s9", said_at: 7 }, { mirror: "session", said_at: 7 }).ok).toBe(true);
    expect(why(eventText({ text: "hi", session: "s9", said_at: null }, {}))).toEqual({ why: "unreadable" });
    // what it names is a session's id all the same
    for (const bad of ["bad id!", "-starts-with-a-dash", "x".repeat(101), 5, true]) {
      expect(why(eventText({ text: "hi", session: bad, said_at: null }, { mirror: "session" })), String(bad)).toEqual({ why: "unreadable" });
    }
  });
});

describe("a conversation message's sealed text", () => {
  it("is its text alone", () => {
    expect(talkText({ text: "hello" })).toEqual({ ok: true, value: "hello" });
    for (const bad of [{ text: "x", who: "owner" }, { text: 1 }, {}, ["x"], null]) expect(why(talkText(bad)), JSON.stringify(bad)).toEqual({ why: "unreadable" });
  });

  it("is held to the server's rule for a message in the clear: not empty, at most 4,100 characters, no NUL, nothing shaped like a secret", () => {
    expect(talkText({ text: "x".repeat(4100) }).ok).toBe(true);
    expect(why(talkText({ text: "x".repeat(4101) }))).toEqual({ why: "rule", rule: "it is longer than 4,100 characters" });
    expect(why(talkText({ text: "" }))).toEqual({ why: "rule", rule: "it is empty" });
    expect(why(talkText({ text: `a${cp(0)}b` }))).toEqual({ why: "hidden" });
    expect(why(talkText({ text: "token ghp_abc" }))).toEqual({ why: "rule", rule: "it holds something shaped like a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)" });
  });
});

describe("a refusal", () => {
  it("is said in the words the page shows in the text's place", () => {
    expect(refusalWords({ why: "unreadable" })).toBe("This text could not be read");
    expect(refusalWords({ why: "hidden" })).toBe("This text holds characters the ADE does not show");
    expect(refusalWords({ why: "rule", rule: "it is empty" })).toBe("This text is not shown: it is empty");
  });
});
