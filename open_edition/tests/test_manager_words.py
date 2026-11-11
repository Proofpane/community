"""The role the code calls the controller is the Manager in every
word the product shows. These are the server's words for it: its refusals, the journal's
lines, a task's flags, the mirror's and the packets' errors and headers, the push titles and the storage note; and the
workspace's: its chat's title, its refusals and tools' answers, the prompts it writes and the briefs its models read.
Code names, routes, kinds and stored values stay, as do the app's own log lines to stderr and its SQL."""
import ast
import glob
import json
import os
import re

from proofpane_open.ade import controller, push
from conftest import manual_or_skip

ADE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "proofpane_open", "ade")
WS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "proofpane_workspace")
SQL = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|SAVEPOINT|RELEASE|ROLLBACK)\b|\bade_controller")
ROLE = re.compile(r"controller", re.I)
CODE = re.compile(r"`[^`]*`")        # a code name in backquotes, as the briefs name the packets' key `controller`


def _prose(name, folder=ADE):
    """Every prose string of a module of the ADE's: a string with a space in it that is not a docstring, not inside a
    print() (the app's own log lines) and not SQL; an f-string by its literal parts, adjacent literals as one."""
    tree = ast.parse(open(os.path.join(folder, name), encoding="utf-8").read())
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body \
                and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            skip.add(id(node.body[0].value))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            skip.update(id(n) for n in ast.walk(node))
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
            and " " in node.value.strip() and not SQL.search(node.value)]


def _constant(name, folder, module):
    """A module-level string of a module, read from its source: adjacent literals as one."""
    tree = ast.parse(open(os.path.join(folder, module), encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return " ".join(ast.literal_eval(node.value).split())
    raise KeyError(name)


def test_no_word_the_server_has_for_the_role_says_controller():
    said = [f"{name}:{line}: {text[:90]}" for name in ("controller.py", "push.py", "storage.py", "controller_routes.py")
            for line, text in _prose(name) if ROLE.search(CODE.sub("", text))]
    assert said == []


def test_the_scan_reads_the_words_it_guards():
    # not vacuous: the scan finds the journal's, a refusal's and the push table's words, and leaves the log lines out
    words = {text for _, text in _prose("controller.py")}
    assert "You turned the Manager on" in words and "The Manager is off: there is nothing to stop." in words
    assert not any(t.startswith("controller: ") for t in words)
    assert "The Manager asks you something" in {text for _, text in _prose("push.py")}


def test_the_guards_read_a_backquoted_code_name_as_code_and_nothing_else_so():
    # what CODE takes out is a code name in backquotes alone: the role's word outside them is still read
    assert not ROLE.search(CODE.sub("", "your own settings are under `controller`, the code's name for the Manager"))
    assert ROLE.search(CODE.sub("", "the controller planned it, under `controller`"))


def test_every_journal_line_names_the_manager_and_never_the_controller():
    payload = {"n": 3, "session": "abcabcabcabc", "flag": "started while it was off", "share": 2, "question": "qq1",
               "changed": {"cap": 2}, "to": "off", "sessions": 1, "why": "the reason", "as": "done", "state": "done",
               "outcome": "stopped", "cause": "account", "from": "needs_owner", "round": 1, "passed": 1, "failed": 0}
    seen = []
    for kind in controller.JOURNAL_KINDS:
        words = controller.words_of({"kind": kind, "payload": json.dumps(payload)})
        assert not ROLE.search(words), (kind, words)
        seen.append(words)
    for cause in [*controller.CAUSE_WORDS, "account"]:
        for outcome in ("stopped", "refused", "failed"):
            words = controller.words_of({"kind": "attempt.ended", "payload": json.dumps(
                {"n": 1, "outcome": outcome, "cause": cause})})
            assert not ROLE.search(words), (cause, outcome, words)
            seen.append(words)
    assert "You turned the Manager on" in seen and "You stopped the Manager" in seen
    assert "The Manager started session abcabcabcabc, outside the Manager's bounds: started while it was off" in seen
    assert "Its manager's attempt 1 ended: stopped: the Manager's account changed" in seen


def test_the_push_titles_and_their_words_name_the_manager():
    for kind in ("controller-question", "controller-look", "controller-cap-waits"):
        title, body, _ = push.WORDS[kind]
        assert not ROLE.search(title) and not ROLE.search(body), kind
    assert push.WORDS["controller-cap-waits"][:2] == ("The Manager waits for a place",
                                                      "Open the control view: free a place, then tell the Manager.")


def test_the_packets_tell_the_model_what_the_manager_wrote():
    assert "what the Manager wrote" in controller.HEADER and not ROLE.search(controller.HEADER)
    assert "what the Manager and this requirement's earlier attempts wrote" in controller.MANAGER_HEADER
    assert not ROLE.search(controller.MANAGER_HEADER)


def test_no_word_the_workspace_has_for_the_role_says_controller():
    said = [f"{name}:{line}: {text[:90]}" for name in ("controller.py", "gate.py", "workspace.py")
            for line, text in _prose(name, WS) if ROLE.search(CODE.sub("", text))]
    assert said == []


def test_the_workspace_scan_reads_the_manager_s_title_and_its_brief():
    words = {text for _, text in _prose("controller.py", WS)}
    assert "The Manager" in words                                          # its chat's title
    assert any(t.startswith("The workspace: you are the owner's Manager in this workspace.") for t in words)


def test_the_manual_s_page_words_name_the_manager():
    # the manual's account of the control view is the page's words: the role is the Manager there, and its vocabulary
    # says so; its sections on the code keep the code's word
    manual_or_skip()
    readme = " ".join(open(os.path.join(ADE, "..", "..", "MANUAL.md"), encoding="utf-8").read().split())
    assert "On the page the controller is the Manager, an owner's request a goal, a requirement a sub-goal" in readme
    assert "Above the map the control view shows the Manager's own panel" in readme
    assert "The Manager's own chat opens from the panel as a session's does" in readme
    assert "which the panel says in words: the Manager comes with a later version." in readme
    assert "what the app keeps of the account's agents, tasks, Manager and finishes stays." in readme


def test_the_briefs_say_which_key_is_the_manager_s_and_which_a_requirement_s_own_manager_s():
    # the briefs call the model the Manager, and its settings come under the code's key
    # `controller`, a requirement's own manager's state under `manager`; so the briefs name both keys
    brief = _constant("BRIEF", WS, "controller.py")
    attempt = _constant("ATTEMPT_BRIEF", WS, "controller.py")
    assert "your own settings are under `controller`, the code's name for the Manager" in brief
    assert "a requirement's own manager's state, where it has one, under `manager`" in brief
    assert "the owner's Manager's settings are under `controller`, the code's name for the Manager" in attempt


def test_a_sentence_with_the_manager_and_a_requirement_s_manager_tells_them_apart():
    # where the two meet in one sentence, the requirement's manager is named as the requirement's
    attempt = _constant("ATTEMPT_BRIEF", WS, "controller.py")
    assert "counts the requirements' managers and the owner's Manager waiting for a place in the cap" in attempt
    words = {" ".join(text.split()) for _, text in _prose("controller.py", WS)}
    assert "the manager the owner's Manager keeps for one of its requirements" in words
    assert ("the requirement as the owner's Manager wrote it and this task as the requirement's manager wrote it"
            in words)
    line = controller.words_of({"kind": "plan", "payload": json.dumps({"n": 3, "share": 2})})
    assert line == ("The Manager planned it from request 3, for the requirement's own manager, with 2 of the cap's "
                    "sessions")


def _strings(path):
    """Every string of a module, with its line, but its docstrings and what a print() writes (the app's own log
    lines)."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body \
                and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            skip.add(id(node.body[0].value))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            skip.update(id(n) for n in ast.walk(node))
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip]


def test_no_module_of_the_app_or_the_workspace_says_controller_in_its_words():
    # every module, not a list kept by hand; and a capital Controller whatever its spaces, since
    # no code name is written so; a code name in backquotes is the code's
    said = []
    for folder in (os.path.dirname(ADE), WS):
        for path in sorted(glob.glob(os.path.join(folder, "**", "*.py"), recursive=True)):
            for line, text in _strings(path):
                bare = CODE.sub("", text)
                if "Controller" in bare or (" " in bare.strip() and not SQL.search(bare) and ROLE.search(bare)):
                    said.append(f"{os.path.relpath(path, folder)}:{line}: {text[:90]}")
    assert said == []


def test_the_manual_quotes_the_product_s_words_for_the_role_as_the_product_says_them():
    # where the manual quotes what the product shows, the push titles, the chat's title and the
    # flags, it quotes Manager; the quoted words it keeps with the role's code name are code: a tag and a name
    manual_or_skip()
    readme = " ".join(open(os.path.join(ADE, "..", "..", "MANUAL.md"), encoding="utf-8").read().split())
    quoted = {q for q in re.findall(r'"([^"]{0,300}?)"', readme) if ROLE.search(q)}
    assert quoted <= {"proofpane-controller/1", "controller"}, quoted
    assert ("in these words: started while the Manager was off (or stopped), on another account than the "
            "Manager's") in readme
