"""A run's tree and context (runtree.py), from a transcript in the layout Claude Code 2.1.274 writes: the session's
file, its sub-agents' files with their .meta.json, a reply on several lines with one message id, and compactions as
compact_boundary lines followed by the summary."""

import json
import os

from proofpane_workspace import runtree
from proofpane_workspace.usage import slug

SESSION = "252bf2c2-11fb-45c8-88c9-2be6ce4d5fb3"
FIRST = ("Label the issues in src/triage/rules.py and docs/labels.md, see https://github.com/acme/demo/issues/7 and "
         "#42, commit 3fa9c1d2; keep the \"needs-owner\" label.")


def line(kind, at, **fields):
    return dict({"type": kind, "timestamp": f"2026-10-03T03:{at // 60:02d}:{at % 60:02d}.000Z"}, **fields)


def reply(at, mid, content, context):
    usage = {"input_tokens": context // 2, "cache_read_input_tokens": context - context // 2,
             "cache_creation_input_tokens": 0, "output_tokens": 10}
    row = line("assistant", at, requestId="req_" + mid, message={"id": mid, "usage": usage, "content": content})
    return [row, row]                                       # a streamed reply is written on two lines


def tool_result(at, tool_id, error=False):
    return line("user", at, message={"content": [{"type": "tool_result", "tool_use_id": tool_id, "is_error": error,
                                                  "content": "ok"}]})


def boundary(at, pre, summary):
    return [line("system", at, subtype="compact_boundary", compactMetadata={"trigger": "auto", "preTokens": pre,
                                                                          "durationMs": 40}),
            line("user", at, isCompactSummary=True, message={"content": summary})]


def write(claude_dir, worktree, rows, subagents=()):
    folder = os.path.join(claude_dir, "projects", slug(os.path.realpath(worktree)))
    os.makedirs(os.path.join(folder, SESSION, "subagents"), exist_ok=True)
    with open(os.path.join(folder, SESSION + ".jsonl"), "w") as fh:
        fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
    for agent_id, meta, sub_rows in subagents:
        base = os.path.join(folder, SESSION, "subagents", f"agent-{agent_id}")
        with open(base + ".jsonl", "w") as fh:
            fh.write("\n".join(json.dumps(r) for r in sub_rows) + "\n")
        with open(base + ".meta.json", "w") as fh:
            json.dump(meta, fh)


def conversation(compactions=0):
    rows = [line("user", 0, message={"content": FIRST})]
    rows += reply(2, "msg_1", [{"type": "tool_use", "id": "toolu_read", "name": "Read",
                                "input": {"file_path": "src/triage/rules.py"}}], 20_000)
    rows.append(tool_result(3, "toolu_read"))
    rows += reply(5, "msg_2", [{"type": "tool_use", "id": "toolu_task", "name": "Task",
                                "input": {"description": "check the labels", "prompt": "..."}}], 40_000)
    rows.append(tool_result(65, "toolu_task"))
    context = 250_000
    for n in range(compactions):
        summary = "Summary: working on src/triage/rules.py; #42 open" + (" and docs/labels.md" if n == 0 else "")
        rows += boundary(70 + n * 10, context, summary)
        rows += reply(71 + n * 10, f"msg_after_{n}", [{"type": "text", "text": "go on"}], 9_000 + n)
        context += 10_000
    rows += reply(120, "msg_last", [{"type": "tool_use", "id": "toolu_bash", "name": "Bash",
                                     "input": {"command": "npm test -- --run"}}], 30_000)
    rows.append(tool_result(124, "toolu_bash", error=True))
    rows.append(line("user", 125, message={"content": "The owner says: go ahead."}))
    rows += reply(126, "msg_answer", [{"type": "text", "text": "done"}], 31_000)
    return rows


SUB = [line("user", 6, message={"content": "check the labels"})] + \
    reply(10, "msg_sub", [{"type": "tool_use", "id": "toolu_grep", "name": "Grep", "input": {"pattern": "label"}}],
          5_000) + [tool_result(12, "toolu_grep")]
DEEP = [line("user", 13, message={"content": "deeper"})] + \
    reply(14, "msg_deep", [{"type": "tool_use", "id": "toolu_ls", "name": "Glob", "input": {"pattern": "*.md"}}], 1_000)


def test_the_tree_nests_a_sub_agent_under_its_call_and_counts_each_reply_once(tmp_path):
    work, claude = tmp_path / "work", tmp_path / "claude"
    work.mkdir()
    write(claude, work, conversation(), subagents=[
        ("a1", {"agentType": "general-purpose", "description": "check", "toolUseId": "toolu_task", "spawnDepth": 1}, SUB),
        ("a2", {"agentType": "Explore", "description": "deeper", "toolUseId": "toolu_grep", "spawnDepth": 2}, DEEP)])
    got = runtree.read(str(work), str(claude), SESSION, model_window=1_000_000)
    tree = got["tree"]
    first, answer = tree["turns"]
    assert (first["text"], answer["text"]) == ("", "")                   # their words stay in the chat
    assert [t["name"] for t in first["tools"]] == ["Read", "Task", "Bash"]
    read, task, bash = first["tools"]
    assert (read["target"], read["ms"], read["error"]) == ("src/triage/rules.py", 1000, False)
    assert (task["target"], bash["target"], task["agent"]["description"]) == ("", "npm", "")   # no prompt, no args
    assert (task["ms"], bash["error"]) == (60000, True)
    assert task["agent"]["type"] == "general-purpose" and task["agent"]["requests"] == 1
    grep = task["agent"]["tools"][0]
    assert grep["name"] == "Grep" and grep["agent"]["type"] == "Explore" and grep["agent"]["depth"] == 2
    assert tree["requests"] == 4 and first["requests"] == 3                  # four replies, each on two lines
    assert tree["tokens"]["output_tokens"] == 40


def test_the_context_its_peak_and_its_compaction_point(tmp_path):
    work, claude = tmp_path / "work", tmp_path / "claude"
    work.mkdir()
    write(claude, work, conversation())
    context = runtree.read(str(work), str(claude), SESSION, model_window=1_000_000)["context"]
    assert (context["model_window"], context["compaction_window"]) == (1_000_000, 967_000)
    assert context["peak_tokens"] == 40_000 and context["last_tokens"] == 31_000
    small = runtree.read(str(work), str(claude), SESSION, autocompact_window=300_000, model_window=1_000_000)["context"]
    assert small["compaction_window"] == 300_000 and small["peak_pct"] == round(40_000 / 300_000, 4)
    assert context["compactions"] == 0 and context["retention"] is None and context["fragmented"] is False


def test_compactions_are_counted_and_a_third_fragments_the_run_with_what_it_lost(tmp_path):
    work, claude = tmp_path / "work", tmp_path / "claude"
    work.mkdir()
    write(claude, work, conversation(compactions=2))
    two = runtree.read(str(work), str(claude), SESSION, model_window=1_000_000)["context"]
    assert two["compactions"] == 2 and two["fragmented"] is False
    assert [(c["trigger"], c["pre_tokens"], c["post_tokens"]) for c in two["compaction_log"]] == \
        [("auto", 250_000, 9_000), ("auto", 260_000, 9_001)]               # tokens after: the next request's
    write(claude, work, conversation(compactions=3))
    three = runtree.read(str(work), str(claude), SESSION, model_window=1_000_000)["context"]
    assert three["fragmented"] is True
    assert "src/triage/rules.py" not in three["lost"] and "#42" not in three["lost"]
    assert "docs/labels.md" in three["lost"] and "3fa9c1d2" in three["lost"]
    # an address and a quoted name are counted, never listed: either can carry a key or a person's words
    assert "https://github.com/acme/demo/issues/7" not in three["lost"] and "needs-owner" not in three["lost"]
    assert three["retention"] == round(2 / len(runtree.needles(FIRST)), 4)          # the summary kept two


def test_the_first_message_s_identifiers(tmp_path):
    found = runtree.needles(FIRST)
    for n in ("src/triage/rules.py", "docs/labels.md", "https://github.com/acme/demo/issues/7", "#42", "3fa9c1d2",
              "needs-owner"):
        assert n in found, n
    assert len(runtree.needles(" ".join(f"x/file{i}.py" for i in range(80)))) == runtree.NEEDLES


def test_a_large_tree_is_cut_to_fit_and_says_how_many_it_left_out(tmp_path):
    work, claude = tmp_path / "work", tmp_path / "claude"
    work.mkdir()
    rows = [line("user", 0, message={"content": "many"})]
    for n in range(2000):
        rows += reply(1, f"msg_{n}", [{"type": "tool_use", "id": f"t{n}", "name": "Read",
                                       "input": {"file_path": f"src/{'x' * 90}/file{n}.py"}}], 1_000)
    write(claude, work, rows)
    tree = runtree.read(str(work), str(claude), SESSION)["tree"]
    assert len(json.dumps(tree)) <= runtree.MAX_TREE
    (turn,) = tree["turns"]
    assert turn["more"] == 2000 - len(turn["tools"]) and len(turn["tools"]) >= 1


def test_no_transcript_is_none(tmp_path):
    assert runtree.read(str(tmp_path), str(tmp_path / "claude"), SESSION) is None


def test_the_tree_keeps_no_message_s_words_nor_a_command_s_address_or_key(tmp_path):
    """The tree is copied into the app: names, paths, times and counts, never contents."""
    work, claude = tmp_path / "work", tmp_path / "claude"
    work.mkdir()
    rows = [line("user", 0, message={"content": "Use the deploy key DK-sekret-1111 for the release."})]
    rows += reply(2, "msg_1", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {
        "command": "TOKEN=x curl -H 'Authorization: Bearer sk-live-2222' https://h.example/?token=tok-3333"}}], 1_000)
    rows.append(tool_result(3, "t1"))
    rows += reply(4, "msg_2", [{"type": "tool_use", "id": "t2", "name": "WebFetch", "input": {
        "url": "https://h.example/?token=tok-5555", "prompt": "find the password"}}], 1_000)
    rows.append(tool_result(5, "t2"))
    rows.append(line("user", 6, message={"content": "the password is hunter2-4444"}))
    rows += reply(7, "msg_3", [{"type": "text", "text": "done"}], 1_000)
    write(claude, work, rows)
    got = json.dumps(runtree.read(str(work), str(claude), SESSION, model_window=1_000_000))
    for secret in ("DK-sekret-1111", "sk-live-2222", "tok-3333", "tok-5555", "hunter2-4444", "password"):
        assert secret not in got, secret
    bash, fetch = json.loads(got)["tree"]["turns"][0]["tools"]
    assert (bash["target"], fetch["target"]) == ("curl", "")
