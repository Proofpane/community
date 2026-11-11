"""More than one sign-in to Claude Code, or to Codex, in one workspace: each account in a folder of its own, and the one
new sessions start on.

The default account is the program's own folder (`~/.claude` and `~/.claude.json`, `~/.codex`): under the local
provider the person's own sign-in, in a workspace machine the workspace's. Each other account is a folder under
`agent-accounts/<id>/` in the workspace's root, given to the program as Claude Code's `CLAUDE_CONFIG_DIR` (where it
then keeps its `.claude.json`, its settings, its transcripts and, on Linux, its `.credentials.json`; on a Mac its
keychain item is named after the folder, `Claude Code-credentials-<8 hex>`, read from Claude Code 2.1.274) or as Codex's
`CODEX_HOME` (its `auth.json`, `config.toml` and conversation files). So the accounts' sign-ins and conversations are
apart, and a session stays on the account it started on, also when it is started again (its transcripts are in that
account's folder). The person's own settings, instructions, agents, commands and skills are linked into a new account's
folder from the default one, so only the sign-in and what it keeps differ.

What is kept here (a file in the workspace's root, the agent's user's alone, whose name the workspace's gate refuses a
tool call to name: gate.py) is each account's id, its kind, the name the person gave it and when it was added; which
account of each kind new sessions start on; and the account each session runs on. That binding is kept here and never
in the session's own record, which its programs can write: a session that changed it would start again on another
account, or drop out of its account's count, so that the account could be removed under it. Nothing of a sign-in is
kept here: each account's is in its folder, where its program keeps it.
"""

import json
import os
import re
import secrets
import shutil
import threading
import time
import unicodedata

from . import watch

KINDS = ("claude", "codex")
# which kind of account a session's program uses
KIND_OF = {"claude": "claude", "chat": "claude", "claude-sign-in": "claude", "codex": "codex", "codex-sign-in": "codex"}
DEFAULT = "default"
FOLDER = "agent-accounts"
MAX_PER_KIND = 8
MAX_NAME = 40
_ID = re.compile(r"^[0-9a-f]{12}$")
_SESSION = re.compile(r"^[0-9a-f]{12}$")
# what of the default account's folder a new one links to, so the person's own setup carries over
SHARED = {"claude": ("settings.json", "CLAUDE.md", "agents", "commands", "skills", "output-styles"),
          "codex": ("config.toml", "AGENTS.md", "prompts")}


class AccountError(ValueError):
    pass


def default_folder(kind):
    """The program's own folder: Claude Code's `~/.claude`, Codex's `$CODEX_HOME` or `~/.codex`."""
    if kind == "codex":
        return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    return os.path.join(os.path.expanduser("~"), ".claude")


def _unseen(text):
    return any(unicodedata.category(c) in ("Cc", "Cf", "Co", "Cn", "Cs", "Zl", "Zp") for c in text)


class Accounts:
    def __init__(self, root):
        self.root = root
        self.path = os.path.join(root, FOLDER + ".json")
        self.dir = os.path.join(root, FOLDER)
        self._lock = threading.Lock()

    @staticmethod
    def _sanitized(data):
        known = {a["id"]: a["kind"] for a in data["accounts"]}
        return {"accounts": data["accounts"],
                "active": {k: v if known.get(v) == k else None for k, v in data["active"].items()},
                "sessions": {sid: a for sid, a in data["sessions"].items() if a in known}}

    def _load(self, writing=False):
        """The record, as read: one that cannot be read is none to look at, and refused to be written over (`writing`),
        since writing over it would lose every account in it, and their folders with them."""
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError):
            if writing:
                raise AccountError(f"The record of the accounts ({FOLDER}.json in the workspace) cannot be read: it is "
                                   "left as it is.") from None
            data = {}
        if not isinstance(data, dict):
            if writing:
                raise AccountError(f"The record of the accounts ({FOLDER}.json in the workspace) is not one: it is left "
                                   "as it is.")
            data = {}
        accounts = [a for a in data.get("accounts", []) if isinstance(a, dict) and isinstance(a.get("id"), str)
                    and _ID.match(a["id"]) and a.get("kind") in KINDS and isinstance(a.get("name"), str)]
        active = data.get("active") if isinstance(data.get("active"), dict) else {}
        sessions = data.get("sessions") if isinstance(data.get("sessions"), dict) else {}
        known = {a["id"]: a["kind"] for a in accounts}
        return {"accounts": accounts,
                "active": {k: active.get(k) if known.get(active.get(k)) == k else None for k in KINDS},
                "sessions": {sid: a for sid, a in sessions.items()
                             if isinstance(sid, str) and _SESSION.match(sid) and a in known}}

    def _save(self, data):
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, self.path)
        watch.wrote(self.path)

    def all(self, strict=False):
        """{"accounts": [{id, kind, name, created_at}], "active": {kind: id or None}, "sessions": {session: id}};
        `strict`, refused when the record cannot be read, where none would be taken for no accounts at all."""
        with self._lock:
            return self._load(writing=strict)

    def of(self, session_id, strict=False):
        """The id of the account a session runs on, or None for its program's own; `strict` as in `all`: what starts
        a program asks so, so that a record it cannot read never starts one on its program's own sign-in instead."""
        return self.all(strict)["sessions"].get(session_id)

    def bind(self, session_id, account_id):
        """A new session runs on `account_id`: kept here, read back from nowhere else."""
        with self._lock:
            data = self._load(writing=True)
            if not any(a["id"] == account_id for a in data["accounts"]):
                raise AccountError("No such account.")
            data["sessions"][session_id] = account_id
            self._save(data)

    def unbind(self, session_id):
        with self._lock:
            data = self._load(writing=True)
            if data["sessions"].pop(session_id, None) is not None:
                self._save(data)

    def get(self, account_id):
        return next((a for a in self.all()["accounts"] if a["id"] == account_id), None)

    def folder(self, account):
        """The folder the program is given for an account (its CLAUDE_CONFIG_DIR or CODEX_HOME)."""
        return os.path.join(self.dir, account["id"], account["kind"])

    def env(self, account):
        """What a program on `account` is started with; the default account's is nothing."""
        if account is None:
            return {}
        return {"CLAUDE_CONFIG_DIR" if account["kind"] == "claude" else "CODEX_HOME": self.folder(account)}

    def add(self, kind, name):
        """A new account of `kind`, named by the person: its folder made (the agent's user's alone) and the default
        account's own setup linked into it. Not signed in: its first sign-in does that."""
        if kind not in KINDS:
            raise AccountError("An account is Claude Code's or Codex's.")
        if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME or _unseen(name):
            raise AccountError(f"An account's name is 1 to {MAX_NAME} characters of plain text.")
        name = " ".join(name.split())
        with self._lock:
            data = self._load(writing=True)
            mine = [a for a in data["accounts"] if a["kind"] == kind]
            if len(mine) >= MAX_PER_KIND:
                raise AccountError(f"At most {MAX_PER_KIND} accounts of one program, besides its own.")
            if any(a["name"].lower() == name.lower() for a in mine) or name.lower() == DEFAULT:
                raise AccountError(f"There is an account named {name} already.")
            account = {"id": secrets.token_hex(6), "kind": kind, "name": name, "created_at": int(time.time())}
            os.makedirs(self.dir, mode=0o700, exist_ok=True)
            os.chmod(self.dir, 0o700)
            folder = self.folder(account)
            os.makedirs(folder, mode=0o700)
            os.chmod(os.path.dirname(folder), 0o700)
            _share(kind, folder)
            data["accounts"].append(account)
            self._save(data)
        return account

    def remove(self, account_id):
        """Forget an account and its folder; new sessions go back to the default one if it was theirs."""
        with self._lock:
            data = self._load(writing=True)
            account = next((a for a in data["accounts"] if a["id"] == account_id), None)
            if account is None:
                raise AccountError("No such account.")
            # one that was new sessions' account is no longer: _load reads an active id it does not know as none
            data["accounts"] = [a for a in data["accounts"] if a["id"] != account_id]
            self._save(self._sanitized(data))
        shutil.rmtree(os.path.join(self.dir, account_id), ignore_errors=True)
        return account

    def choose(self, kind, account_id):
        """The account new sessions of `kind` start on: an id of that kind, or None (or "default") for the default."""
        if kind not in KINDS:
            raise AccountError("An account is Claude Code's or Codex's.")
        with self._lock:
            data = self._load(writing=True)
            if account_id in (None, DEFAULT):
                account_id = None
            elif not any(a["id"] == account_id and a["kind"] == kind for a in data["accounts"]):
                raise AccountError("No such account.")
            data["active"][kind] = account_id
            self._save(data)
        return account_id

    def for_session(self, program, asked=None):
        """The account a new session of `program` starts on: the one `asked` names (an id, or "default"), else the
        active one of its kind; None for the default, and for a program that has none. Refuses an id that is not one
        of its kind's."""
        kind = KIND_OF.get(program)
        if kind is None:
            if asked not in (None, DEFAULT):
                raise AccountError("Only Claude Code's and Codex's sessions start on an account.")
            return None
        data = self.all(strict=True)
        if asked is None:
            return data["active"][kind]
        if asked == DEFAULT:
            return None
        if not isinstance(asked, str) or not any(a["id"] == asked and a["kind"] == kind for a in data["accounts"]):
            raise AccountError("No such account for this program.")
        return asked


def _share(kind, folder):
    """Link what the person keeps in the default account's folder (their settings, instructions, agents, commands,
    skills) into a new account's, where the program looks for it; what is not there is not linked."""
    home = default_folder(kind)
    for name in SHARED[kind]:
        source = os.path.join(home, name)
        if os.path.exists(source):
            os.symlink(source, os.path.join(folder, name))
