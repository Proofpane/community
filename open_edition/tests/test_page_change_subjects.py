"""The changes the app (ui/) names when it asks for a confirmation (Change.subject, accountSubject) are the ones the
server spends confirmations for (passkeys.CHANGE), and the app names every one of them. The page's own tests run it
against a test server, which takes whatever it sends, so a name the page and the server spell differently would pass
them and refuse every such confirmation in use: this reads the names from the page's source instead."""

import itertools
import os
import re

from proofpane_open import passkeys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", "ui", "src"))
ID = "0123456789abcdef0123456789abcdef"
LITERAL = r'"([^"\\]*)"|`([^`]*)`'


def _sources():
    for root, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d != "node_modules"]
        for fn in files:
            if fn.endswith((".ts", ".tsx")) and ".test." not in fn:
                with open(os.path.join(root, fn), encoding="utf-8") as fh:
                    yield fn, fh.read()


def _named(text):
    """Each change name written in `text`, as a string or a template: after `subject:`, or returned by accountSubject."""
    found = [m.group(1) if m.group(1) is not None else m.group(2)
             for m in re.finditer(r"\bsubject:\s*(?:" + LITERAL + ")", text)]
    body = re.search(r"export function accountSubject\(.*?\n}\n", text, re.S)
    if body:
        found += [m.group(1) if m.group(1) is not None else m.group(2)
                  for m in re.finditer(r"\breturn\s+(?:" + LITERAL + ")", body.group(0))]
    return found


def _spelled(template):
    """Every string a name can come out as: each `${a ? X : Y}` as each of its branches (a string literal as itself,
    anything else as an id), and any other `${...}` as an id."""
    choices = []
    for part in re.split(r"(\$\{[^}]*\})", template):
        if not part.startswith("${"):
            choices.append([part])
            continue
        expr = part[2:-1]
        branches = re.fullmatch(r"[^?]*\?(.*):(.*)", expr)
        options = []
        for branch in (branches.groups() if branches else [None]):
            literal = re.fullmatch(r'\s*"([^"]*)"\s*', branch) if branch is not None else None
            options.append(literal.group(1) if literal else ID)
        choices.append(options)
    return ["".join(p) for p in itertools.product(*choices)]


def _every_name():
    return {(fn, s) for fn, text in _sources() for named in _named(text) for s in _spelled(named)}


def test_every_change_the_page_names_is_one_the_server_spends_a_confirmation_for():
    names = _every_name()
    assert len(names) >= 14, names
    for fn, s in names:
        assert passkeys.CHANGE.fullmatch(s), f"{fn}: {s!r} is not one of passkeys.CHANGE's forms"


def test_the_page_names_every_change_the_server_spends_a_confirmation_for():
    used = {s.replace(ID, "ID") for _, s in _every_name()}
    assert used == {"passkey-add", "passkey-remove:ID", "high-security-on", "high-security-off", "sign-out-everywhere",
                    # renaming a passkey takes the person's word
                    "passkey-rename:ID",
                    "link-ticket:new", "link-ticket:ID", "link-revoke:ID", "link-approve:ID", "link-approve:ID:ID",
                    "connector-on", "connector-allow:ID:propose", "connector-allow:ID:read", "request-approve:ID",
                    "signer-ticket", "e2e-upgrade:ID",
                    # the tasks (passkeys.CHANGE's nine task forms)
                    "task-account-own:ID", "task-cross:ID", "task-custody-cross:ID", "task-offer:ID",
                    "task-custody:ID", "task-custody-accept:ID", "task-custody-confirm:ID", "task-account-record:ID",
                    "task-secret-lift:ID:ID",
                    # deleting a workspace left behind at once (ade/storage.py)
                    "workspace-delete"}


def test_a_name_in_another_spelling_is_caught():
    # the reading above sees a change: an underscore for a hyphen, or a branch the server has no form for
    assert not passkeys.CHANGE.fullmatch(_spelled("passkey_add")[0])
    assert _spelled('connector-allow:${id}:${propose ? "propose" : "write"}') == [
        f"connector-allow:{ID}:propose", f"connector-allow:{ID}:write"]
    assert _named('x = { subject: `link-revoke:${l.id}`, y: 1 }; const t: { subject: string | null };') == [
        "link-revoke:${l.id}"]
