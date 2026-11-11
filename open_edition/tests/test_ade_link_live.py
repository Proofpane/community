"""The live view: a linked session's turn on its owner's timeline while it runs. The
tool-call hook (`ade_link.py mirror live`, on PreToolUse) only keeps a flush of its session going: it tries the
session's flush lock and, where it is free, starts one; it writes nothing else, posts nothing and returns at once. A
flush (`mirror flush`, a process of its own) posts each round the turn since the last round, from a place of its own
apart from the Stop's: the words, and a line for each tool call in the same entries, judged with them, in the turn's
order, as one note of mirror "live" (more only where they do not fit in one, and a new one where a turn starts). A Stop
posts its turn again as its own notes, and its last says when it posted the turn's words whole (whole_since), so the
ADE can say which rounds it replaced. Through the app served on a local port, as the mirror's tests are; the flush the
hook would start is recorded instead, holding the session's lock as a flush does, and run once here."""

import io
import json
import os
import random
import re
import signal
import subprocess
import sys
import tempfile
import time
import types

import pytest

import ade_link
from proofpane_open import store
from test_ade_link_client import link_row, owner, pair, run, served  # noqa: F401  fixtures and helpers
from test_ade_link_mirror import (_events, _in_projects, _participant, _ruled, _said, _text, _tool_use, _turn,
                                  _typed, touched_note)
from test_ade_link_mirror import _transcript as _bare_transcript

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = "sk-" + "Zq8Wn3Ke6Rt9Yu2Io5Pa1Sd4Fg7Hj0Kl3Zx6Cv"
LEFT_OUT = "[left out: it held something shaped like a secret, which the ADE does not keep]"
TURN_NOTE = "[mirror] A turn of session s1 was not mirrored: it touched a project history.json excepts."
STAMPED_AT = int(time.time()) - 3600        # a transcript's first time here: an hour back, a second for each entry


def _stamp(n):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(STAMPED_AT + n))


def _transcript(path, entries):
    """A transcript as Claude Code writes one: each entry with its time, the n-th a second after the one before, where
    the case gave it none. A round's note is timed by its first entry, and a Stop says from which entry's time on it
    posted its turn's words whole (whole_since), which is how the ADE tells the rounds it replaced: a case of entries
    with no time (_bare_transcript) has neither."""
    _bare_transcript(path, [e if "timestamp" in e else dict(e, timestamp=_stamp(n)) for n, e in enumerate(entries)])


@pytest.fixture(autouse=True)
def a_home_of_its_own(monkeypatch, own_home):
    monkeypatch.setenv("HOME", own_home)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


@pytest.fixture(autouse=True)
def a_link_that_seals(monkeypatch):
    """Each link here as one that seals, its notes posted as they are: where a link does not seal, a round carries no
    free text, and these cases read a round's words. The sealing itself
    is the mirror's, whose cases read it; a case of a link that does not seal sets its own mode."""
    monkeypatch.setattr(ade_link, "_mirror_mode", lambda link: ("seal", None, "a-key", None))
    monkeypatch.setattr(ade_link, "_mirror_sealed", lambda link, body, mode, replaced=None: body)


def _lock_name(session):
    """The session's flush lock in the link's directory, where the client names one."""
    name = getattr(ade_link, "_live_lock_name", None)
    return name(session) if name else None


@pytest.fixture
def spawned(monkeypatch):
    """The flushes the hook would start, recorded instead: (directory, session, transcript, flags). Each takes the
    session's lock as a flush does, so the hook sees a flush of that session running."""
    calls, held = [], []

    def spawn(directory, session, transcript, cwd, *flags):
        calls.append((directory, session, transcript, flags))
        if _lock_name(session):
            held.append(ade_link._try_lock(ade_link.Link(directory), _lock_name(session)))
        return os.getpid()

    monkeypatch.setattr(ade_link, "_live_spawn", spawn, raising=False)
    yield calls
    for fd in held:
        if isinstance(fd, int) and fd >= 0:
            os.close(fd)


def _cli(capsys, *argv):
    """run, an exit taken as the code it gives: where the client knows no such command, a case fails on its
    assertion."""
    try:
        return run(capsys, *argv)
    except SystemExit as e:
        out, err = capsys.readouterr()
        return e.code, out, err


def _live(monkeypatch, capsys, directory, payload, *flags):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return _cli(capsys, "--dir", directory, "mirror", "live", *flags)


def _call(path, tool, tool_input, session="s1", event="PreToolUse", cwd=None):
    return {"hook_event_name": event, "session_id": session, "transcript_path": path,
            "cwd": cwd or os.path.dirname(path), "tool_name": tool, "tool_input": tool_input}


def _flush(capsys, directory, path, session="s1", cwd=None, *flags):
    """One round of the flush the hook starts, with the session's folder as the hook passes it where one is given."""
    where = ("--cwd", cwd) if cwd else ()
    return _cli(capsys, "--dir", directory, "mirror", "flush", "--session", session, "--transcript", path, *where,
                *flags, "--once")


def _stop(monkeypatch, capsys, directory, path, session="s1", cwd=None):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"transcript_path": path, "session_id": session,
                                                              "cwd": cwd or os.path.dirname(path)})))
    return run(capsys, "--dir", directory, "mirror", "reply")


def _shown(client, owner, name, rounds_too=False):
    """The owner's timeline, oldest first, as the page shows it: each note with whose words, or what of the session's,
    it reports. A live view's round is one note (mirror "live") whose items are shown as the notes they stand for, in
    its order, unless the ADE says a Stop of its session posted it again (posted_again). `rounds_too` shows each round
    all the same, as posted."""
    label = {"owner": "owner", "session": "session", "info": "mirror", "step": "step"}
    events = _events(client, owner, name)
    out = []
    for e in events:
        if e.get("mirror") == "live" and not rounds_too and e.get("posted_again") is True:
            continue
        if e.get("mirror") != "live":
            out.append(f"[{label.get(e.get('mirror'), '?')}] {e['text']}")
            continue
        try:
            held = json.loads(e["text"])
        except ValueError:
            out.append(f"[live] {e['text']}")
            continue
        for who, value in held["items"]:
            out.append(f"[step] {chr(10).join(value)}" if who == "calls" else f"[{label[who]}] {value}")
    return out


def _whole(directory):
    """Each mirrored note the link of `directory` posted, oldest first: (its mirror, its whole_since, None where it has
    none), read in the ADE's store."""
    link_id = ade_link.Link(directory).link_id
    with store.connect() as db:
        return [tuple(r) for r in db.execute("SELECT mirror, whole_since FROM ade_link_events WHERE link_id = ? AND "
                                             "mirror IS NOT NULL ORDER BY seq", (link_id,))]


def _steps(client, owner, name):
    return [s[len("[step] "):] for s in _shown(client, owner, name) if s.startswith("[step] ")]


def _bash(uid, command):
    return _tool_use(uid, "Bash", {"command": command})


def _files(directory):
    """Every file under the link's directory, with its bytes and its time."""
    out = {}
    for base, _, names in os.walk(directory):
        for n in names:
            p = os.path.join(base, n)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, directory)] = (fh.read(), os.stat(p).st_mtime_ns)
    return out


def test_the_live_view_s_numbers():
    # a round every 10 seconds; a flush ends 90 seconds after its last
    # post or after an hour; a round's calls hold 20 lines and a line 110 characters, a line or two on a phone; after
    # the ADE refused a round, the rounds of the next 60 seconds ask nothing
    assert [getattr(ade_link, n, None) for n in ("LIVE_EVERY", "LIVE_IDLE", "LIVE_LIFE", "LIVE_STEPS", "LIVE_LINE",
                                                 "LIVE_REFUSED")] == [10, 90, 3600, 20, 110, 60]


def test_a_call_s_step_and_the_words_before_it_reach_the_owner_while_the_turn_runs(client, owner, served, capsys,
                                                                                  tmp_path, monkeypatch, spawned):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    entries = [_typed("u1", "how is the build?"), _said("a1", _text("Checking the tests.")),
               _bash("a2", "pytest -q tests/test_app.py\necho done")]
    _transcript(path, entries)
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "pytest -q"})) == (0, "", "")
    assert spawned == [(mac, "s1", path, ())]                    # a flush of the session started; it posts nothing
    assert _shown(client, owner, "mac") == []
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] how is the build?", "[session] Checking the tests.",
                                            "[step] Bash: pytest"]  # its program alone
    # the turn's end posts the turn again as its own notes, which stand in the round's place
    _transcript(path, entries + [_said("a3", _text("12 passed."))])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] how is the build?", "[session] Checking the tests.",
                                            "[step] Bash: pytest",
                                            "[session] 12 passed."]


@pytest.mark.parametrize("tool,tool_input,line,never", [
    ("Read", {"file_path": "/work/app/main.py"}, "Read: /work/app/main.py", "THE "),
    ("Edit", {"file_path": "/work/app/main.py", "old_string": "THE OLD TEXT", "new_string": "THE NEW TEXT"},
     "Edit: /work/app/main.py", "THE "),
    ("Write", {"file_path": "/work/notes.md", "content": "THE CONTENT"}, "Write: /work/notes.md", "THE "),
    ("Grep", {"pattern": "def main", "path": "/work"}, "Grep: /work", "THE "),
    ("Glob", {"pattern": "**/*.py"}, "Glob", "THE "),
    ("WebFetch", {"url": "https://admin:hunter2@example.org/docs?token=abc123#top", "prompt": "THE PROMPT"},
     "WebFetch", "hunter2"),
    ("WebFetch", {"url": "https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/getUpdates"},
     "WebFetch: https://api.telegram.org", "AAHdq"),
    ("WebSearch", {"query": "release notes"}, "WebSearch", "THE "),
    ("Agent", {"description": "Read the suite", "prompt": "THE PROMPT"}, "Agent", "THE "),
    ("TodoWrite", {"todos": [{"content": "THE TODO", "status": "pending"}]}, "TodoWrite", "THE "),
    ("Bash", {"command": "pytest -q tests/test_app.py"}, "Bash: pytest", None),
    ("Bash", {"command": "cd /work/app\nmake test\n"}, "Bash: cd", "\n"),
    ("Bash", {"command": f"export OPENAI_API_KEY={KEY}"}, "Bash: export", KEY[3:20]),
    ("Bash", {"command": "PGPASSWORD=hunter2 psql -h db -c 'select 1'"}, "Bash: psql",
     "hunter2"),
    ("Bash", {"command": 'curl -H "Authorization: Bearer 9f8e7d6c5b4a" https://api.example.org/v1'},
     "Bash: curl", "9f8e"),
    ("Bash", {"command": "psql postgres://app:S3cretPass@db.internal/prod"}, "Bash: psql",
     "S3cret"),
    ("Bash", {"command": "aws configure set aws_secret_access_key wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY"},
     "Bash: aws", "wJalr"),
    ("Bash", {"command": "cat > .env <<EOF\nDB_PASSWORD=hunter2\nEOF\n"}, "Bash: cat", "hunter2"),
    ("Bash", {"command": "curl https://admin:hunter2@example.org/docs?token=abc123"},
     "Bash: curl", "hunter2"),
    ("Bash", {"command": "curl -u admin:hunter2 https://example.org"}, "Bash: curl",
     "hunter2"),
    ("Bash", {"command": "deploy --password hunter2 --token=abc123def"}, "Bash: deploy",
     "hunter2"),
    # a place a mask names, in a form the masks missed
    ("Bash", {"command": "docker login -u bot --password-stdin <<EOF\nhunter2\nEOF\n"},
     "Bash: docker", "hunter2"),
    ("Bash", {"command": 'tr a-z A-Z <<< "hunter2"'}, "Bash: tr", "hunter2"),
    ("Bash", {"command": 'curl "https://acct.blob.core.windows.net/c?sv=2021-08-06&ss=b&sp=rl&sig=AbCdEf123"'},
     "Bash: curl", "AbCdEf"),
    ("Bash", {"command": "curl https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/getUpdates"},
     "Bash: curl", "AAHdq"),
    ("Bash", {"command": 'curl -H "Cookie: theme=dark; sessionid=a8f3c2d9e1" https://example.org'},
     "Bash: curl", "a8f3c2"),
    ("Bash", {"command": "curl -uadmin:hunter2 https://example.org"}, "Bash: curl",
     "hunter2"),
    ("Bash", {"command": "curl -u :hunter2 https://example.org"}, "Bash: curl", "hunter2"),
    ("Bash", {"command": "PGPASSWORD=-x9hunter2 psql -h db"}, "Bash: psql", "hunter2"),
    ("Bash", {"command": """curl -d '{"password":"hunter2"}' https://example.org/login"""},
     "Bash: curl", "hunter2"),
    ("Bash", {"command": "openssl pkcs12 -export -passout pass:hunter2 -out a.p12"},
     "Bash: openssl", "hunter2"),
    # what a mask hid that the owner needs
    ("Bash", {"command": "git commit -m \"$(cat <<'EOF'\nFix the thing\n\nCo-Authored-By: Claude\nEOF\n)\" && "
                         "git push origin main"},
     "Bash: git", "Co-Authored"),
    ("Bash", {"command": "aws s3 rm s3://prod-bucket/tmp/ --recursive"},
     "Bash: aws", None),
    ("Bash", {"command": "grep -rn password_reset src/auth"}, "Bash: grep", None),
    ("Bash", {"command": "git checkout -b fix-token-expiry origin/main"},
     "Bash: git", None),
    # places a mask names, in forms it missed
    ("Bash", {"command": "docker login -u bot --password-stdin <<\\EOF\nhunter2\nEOF\n"},
     "Bash: docker", "hunter2"),
    ("Bash", {"command": "cat <<'END OF FILE' > a.env\nDB_PASSWORD=hunter2\nEND OF FILE\nls"},
     "Bash: cat", "hunter2"),
    ("Bash", {"command": "psql <<SQL-END\nALTER USER app PASSWORD 'hunter2';\nSQL-END\necho done"},
     "Bash: psql", "hunter2"),
    ("Bash", {"command": "curl -u admin:'hunter2' https://example.org"}, "Bash: curl",
     "hunter2"),
    ("Bash", {"command": 'curl -u "admin:correct horse battery" https://example.org'},
     "Bash: curl", "horse"),
    ("Bash", {"command": 'curl -d "{\\"password\\":\\"hunter2\\"}" https://example.org/login'},
     "Bash: curl", "hunter2"),
    ("Bash", {"command": """python3 -c 'requests.get(u, headers={"Authorization": "Bearer 9f8e7d6c5b4a"})'"""},
     "Bash: python3", "9f8e"),
    ("Bash", {"command": """node -e 'fetch(u, {headers: {Authorization: "Bearer abc123def456"}})'"""},
     "Bash: node", "abc123"),
    ("Bash", {"command": 'curl -H "X-Vault-Token: hvs.CAESIJlU9f2k" https://vault.example.org/v1/sys/health'},
     "Bash: curl", "CAESIJ"),
    # the here-document read before the masks: --password=VALUE takes the `<<` with it
    ("Bash", {"command": "mysql -u root --password=hunter2<<SQL\nUPDATE users SET plan='pro';\nSQL\n"},
     "Bash: mysql", "UPDATE"),
    ("Bash", {"command": "aws configure set aws_secret_access_key \"wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY\""},
     "Bash: aws", "wJalr"),
    # what a mask hid that the owner needs
    ("Bash", {"command": "aws s3 rm s3://prod-bucket/backups/db-2026-11-07T0300Z.sql.gz"},
     "Bash: aws", None),
    ("Bash", {"command": "curl https://api.example.org/v1/jobs/3f2a9c1e-5b7d-4e8f-9a0b-1c2d3e4f5a6b"},
     "Bash: curl", None),
    ("Bash", {"command": "curl https://api.github.com/repos/o/r/commits/39f12631b4d27c84ed1100f8163db105102aefb8"},
     "Bash: curl", None),
    ("Bash", {"command": "curl https://hooks.example.org/reset/5f4dcc3b5aa765d61d8327deb882cf99"},
     "Bash: curl", "5f4dcc3b"),
    ("Bash", {"command": "curl https://hooks.slack.com/services/T0123ABCD/B0123EFGH/AbCdEf1234567890GhIjKlMn"},
     "Bash: curl", "AbCdEf1234"),
    # the token runs' thresholds either side: 20 mixing capitals, small letters and digits, 32 hexadecimal digits
    ("Bash", {"command": "curl https://files.example.org/f/aB3dE5fG7hJ9kL1mN3pQ"},
     "Bash: curl", "aB3dE5fG"),
    ("Bash", {"command": "curl https://files.example.org/f/aB3dE5fG7hJ9kL1mN3p"},
     "Bash: curl", None),
    ("Bash", {"command": "curl https://files.example.org/f/5f4dcc3b5aa765d61d8327deb882cf9"},
     "Bash: curl", None),
    # places a mask names, in forms an earlier reading missed or lost
    ("Bash", {"command": "wp config set DB_PASSWORD 'secret!pass'"}, "Bash: wp",
     "secret!"),
    ("Bash", {"command": "aws configure set aws_secret_access_key \"hunter2\""},
     "Bash: aws", "hunter2"),
    ("Bash", {"command": 'http GET https://api.example.org/v1 Authorization:"Bearer abc123def456"'},
     "Bash: http", "abc123"),
    ("Bash", {"command": "curl https://x.example.org/reset/k3j4h5g6f7d8s9a0q1w2e3r4t5y6u7i8"},
     "Bash: curl", "k3j4h5g6"),
    ("Bash", {"command": "aws s3 cp s3://acme-exports/daily/Users_Export_2026_11_07.csv ."},
     "Bash: aws", None),
    ("Bash", {"command": 'grep -rn "max_tokens: 4096" cfg.yml'}, "Bash: grep", None),
    ("Bash", {"command": """curl -d '{"max_tokens": 4096}' https://api.example.org/v1/messages"""},
     "Bash: curl", None),
    ("Bash", {"command": 'curl -H "Private-Token: glpat-AbCdEf123456" https://gitlab.example.org/api/v4/p'},
     "Bash: curl", "glpat-"),
    ("Bash", {"command": "curl https://x.org/b/5f4dcc3b5aa765d61d8327deb882cf995f4dcc3b5aa765d61d8327deb882cf99"},
     "Bash: curl", "5f4dcc3b"),
    ("Bash", {"command": "echo $((1 << bits))\nls"}, "Bash: echo", None),
    ("Bash", {"command": 'curl -H "X-RapidAPI-Key: 9f8e7d6c5b4a" https://x.org/v1'},
     "Bash: curl", "9f8e"),
    ("Bash", {"command": "http GET https://x.org/v1 X-RapidAPI-Key:9f8e7d6c5b4a"},
     "Bash: http", "9f8e"),
    ("Bash", {"command": 'curl -H "X-CMC_PRO_API_KEY: abc123def456" https://x.org'},
     "Bash: curl", "abc123"),
    ("Bash", {"command": 'curl -H "pinata_api_key: abc123def456" https://x.org'},
     "Bash: curl", "abc123"),
    ("Bash", {"command": 'curl -H "X-AuthToken: abc123def456" https://x.org'},
     "Bash: curl", "abc123"),
    ("Bash", {"command": 'echo "db_password: hunter2" >> config.yml'},
     "Bash: echo", "hunter2"),
    ("Bash", {"command": "curl https://x.org/s/Ab3dE5fG7h_J9kL1mN3pQ_rS5tU7vW9x"}, "Bash: curl",
     "Ab3dE5fG"),
    ("Bash", {"command": "curl https://x.org/s/Ab3dE5fG7h-J9kL1mN3pQ-rS5tU7vW9x"}, "Bash: curl",
     "Ab3dE5fG"),
    ("Bash", {"command": "curl https://x.org/v1/users/cjld2cjxh0000qzrmn831i7rn"},
     "Bash: curl", None),
    ("Bash", {"command": "aws s3 cp s3://acme/backups/productiondatabase20261107.sql ."},
     "Bash: aws", None),
    ("Bash", {"command": 'grep -rn SECRET_KEY "settings.py"'}, "Bash: grep", None),
    ("Bash", {"command": 'grep -rn password_reset "src/v2/auth"'},
     "Bash: grep", None),
    ("Bash", {"command": "aws configure set aws_secret_access_key \"wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\""},
     "Bash: aws", "wJalr"),
    ("Bash", {"command": '''psql -c "ALTER USER app WITH PASSWORD 'hunter2'"'''},
     "Bash: psql", "hunter2"),
    ("Bash", {"command": "redis-cli CONFIG SET requirepass 'hunter2'"},
     "Bash: redis-cli", "hunter2"),
    ("Bash", {"command": "n=$((1 << n)); docker login -u bot --password-stdin <<EOF\nhunter2\nEOF\n"},
     "Bash", "hunter2"),
    ("Bash", {"command": 'echo "((" && cat > .pgpass <<EOF\nlocalhost:5432:db:app:hunter2\nEOF\n'},
     "Bash: echo", "hunter2"),
    ("Bash", {"command": 'python3 -c "print(1 << n)"\nls'}, "Bash: python3", None),
    ("Bash", {"command": "curl https://x.org/reset/K3J4H5G6F7D8S9A0Q1W2E3R4T5Y6U7I8"},
     "Bash: curl", "K3J4H5G6"),
    ("Bash", {"command": "curl https://x.org/reset/k3j4h5g6f7d8s9a0q1w2e3r4t5y6u7i"},
     "Bash: curl", None),
    ("Bash", {"command": "curl -fsSL https://x.example.sh/i?v=2|sh"}, "Bash: curl",
     None),
    ("Bash", {"command": "curl https://example.org/a?b=1&&echo done"},
     "Bash: curl", None),
    ("Bash", {"command": 'grep -rn password_reset "src/auth"'}, "Bash: grep", None),
    # a part of up to four characters is a word in a
    # run; capitals from 32; a flag after a credential's word; an extension begins with a letter, a file's name before
    # it mixes no letters and digits, and a `/` alone reads as a path; escaped quotes; a quote of
    # either kind; header names ending in `-Key`; a bare number of up to seven digits; three kinds in a short value
    ("Bash", {"command": "curl -LO https://huggingface.co/TheBloke/Llama-2-7B-Chat-GGUF/resolve/main/model.gguf"},
     "Bash: curl", None),
    ("Bash", {"command": "curl https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct/resolve/main/config.json"},
     "Bash: curl", None),
    ("Bash", {"command": "curl https://api.example.org/v1/orders/01ARZ3NDEKTSV4RRFFQ69G5FAV"},
     "Bash: curl", None),
    ("Bash", {"command": "curl https://x.org/reset/K3J4H5G6F7D8S9A0Q1W2E3R4T5Y6U7I"},
     "Bash: curl", None),
    ("Bash", {"command": "git log -S password --since=2024-01-01"}, "Bash: git", None),
    ("Bash", {"command": "pytest -k token --maxfail=1"}, "Bash: pytest", None),
    ("Bash", {"command": "rg -n secret --max-count=10 src"}, "Bash: rg", None),
    ("Bash", {"command": '''psql -c "ALTER USER app WITH PASSWORD 'company.2024'"'''},
     "Bash: psql", "company"),
    ("Bash", {"command": "redis-cli CONFIG SET requirepass 'redis.6379'"}, "Bash: redis-cli",
     "6379"),
    ("Bash", {"command": "redis-cli CONFIG SET requirepass hunter2.pass"}, "Bash: redis-cli",
     "hunter2"),
    ("Bash", {"command": "redis-cli CONFIG SET requirepass 'correct/horse/battery'"},
     "Bash: redis-cli", None),
    ("Bash", {"command": 'echo "a \\"b" && cat > .pgpass <<EOF && echo "done"\nlocalhost:5432:db:app:hunter2\nEOF\n'},
     "Bash: echo", "hunter2"),
    ("Bash", {"command": "echo it\\'s && cat > .netrc <<EOF && echo it\\'s\nmachine x login u password hunter2\nEOF\n"},
     "Bash: echo", "hunter2"),
    ("Bash", {"command": "grep -c '<<EOF' setup.sh\nls -la"}, "Bash: grep", None),
    ("Bash", {"command": 'curl -H "X-Auth-Key: c2547eb745079dac9320b638f5e225cf483cc" https://api.cloudflare.com/v4'},
     "Bash: curl", "c2547eb7"),
    ("Bash", {"command": 'curl -H "Ocp-Apim-Subscription-Key: 0123456789abcdef0123456789abcdef" https://x.azure-api.net'},
     "Bash: curl", "0123456789"),
    ("Bash", {"command": 'echo "primary key: id"'}, "Bash: echo", None),
    ("Bash", {"command": 'curl -H "X-Api-Key: 8273645519283746" https://x.org/v1'},
     "Bash: curl", "82736455"),
    ("Bash", {"command": """curl -d '{"password": 12345678}' https://x.org/login"""},
     "Bash: curl", "12345678"),
    ("Bash", {"command": """curl -d '{"max_tokens": 1234567}' https://api.example.org/v1/messages"""},
     "Bash: curl", None),
    ("Bash", {"command": 'http POST https://x.org/v1 X-Token-Limit:"4096"'},
     "Bash: http", None),
    ("Bash", {"command": 'http POST https://x.org/v1 X-Api-Key:"82736455"'},
     "Bash: http", "82736455"),
    ("Bash", {"command": 'aws configure set aws_secret_access_key "aB3/xY9/kL2mNpQ"'},
     "Bash: aws", "kL2mN"),
    # a token's parts of five characters mixing letters and digits are no words, and a
    # part of four is one (`8x7B`)
    ("Bash", {"command": "curl https://x.org/s/aB3dE-fG7hJ-kL1mN-pQ3rS"}, "Bash: curl", "aB3dE"),
    ("Bash", {"command": "curl -LO https://huggingface.co/mistralai/Mixtral-8x7B-Instruct-v0.1/resolve/main/config.json"},
     "Bash: curl", None),
    # a backslash inside single quotes escapes nothing,
    # and one inside double quotes escapes the next character; `-Key` ends a key's name too, and only ends it; a file
    # read by a known extension; a flag of one dash after a credential's word
    ("Bash", {"command": "tr -d '\\' < in.txt > out.txt && cat > ~/.pgpass <<'EOF'\nlocalhost:5432:db:app:hunter2\nEOF\n"},
     "Bash: tr", "hunter2"),
    ("Bash", {"command": "echo 'C:\\temp\\' && cat > .netrc <<EOF && echo 'done'\nmachine x login u password hunter2\nEOF\n"},
     "Bash: echo", "hunter2"),
    ("Bash", {"command": 'echo "a\\\\" && cat > .pgpass <<EOF && echo "b"\nlocalhost:5432:db:app:hunter2\nEOF\n'},
     "Bash: echo", "hunter2"),
    ("Bash", {"command": """python3 -c "requests.get(u, headers={'X-Auth-Key': 'c2547eb745079dac9320b638f5e225cf483cc'})\""""},
     "Bash: python3", "c2547eb7"),
    ("Bash", {"command": """curl -d '{"Ocp-Apim-Subscription-Key": "0123456789abcdef0123456789abcdef"}' https://x.org"""},
     "Bash: curl", "0123456789"),
    ("Bash", {"command": 'curl -H "X-Key-Id: k1-prod" https://x.org'}, "Bash: curl",
     None),
    ("Bash", {"command": "grep -rn token k8s.yaml"}, "Bash: grep", None),
    ("Bash", {"command": "grep -rn SECRET_KEY oauth2.py"}, "Bash: grep", None),
    ("Bash", {"command": "grep -n password 'config2.yaml'"}, "Bash: grep", None),
    ("Bash", {"command": "redis-cli CONFIG SET requirepass 'Spring.Day'"}, "Bash: redis-cli",
     "Spring"),
    ("Bash", {"command": '''psql -c "ALTER USER app WITH PASSWORD 'p@ss.word'"'''},
     "Bash: psql", "p@ss"),
    ("Bash", {"command": "mvn test -Dsecret -Xmx2048m"}, "Bash: mvn", None),
    # a header's name ending in `Authorization` or
    # `Cookie`, quoted, httpie's and bare; bash's `$'...'`, where a backslash escapes; a line's second here-document; a
    # file's extension read before its kinds of character; `-Key` only at a key's name's end
    ("Bash", {"command": 'curl -H "X-Authorization: Bearer x7Kq2mP9vL4nR8tW3yZ6aB1cD5eF0gH2" https://x.org/api'},
     "Bash: curl", "x7Kq2mP9"),
    ("Bash", {"command": 'http x.org X-Authorization:"9f86d081884c7d659a2feaa0c55ad015"'},
     "Bash: http", "9f86d081"),
    ("Bash", {"command": "curl -H X-Forwarded-Authorization:abc123def456 https://x.org"},
     "Bash: curl", "abc123"),
    ("Bash", {"command": 'curl -H "Set-Cookie: session=abc123def456" https://x.org'},
     "Bash: curl", "abc123"),
    ("Bash", {"command": "printf $'it\\'s done\\n' >> log.txt && cat > ~/.pgpass <<'EOF'\nlocalhost:5432:db:app:hunter2\nEOF\n"},
     "Bash: printf", "hunter2"),
    ("Bash", {"command": "cat > a.txt <<EOF1 && cat > ~/.pgpass <<EOF2\nhello\nEOF1\nlocalhost:5432:db:app:hunter2\nEOF2\nls"},
     "Bash: cat", "hunter2"),
    ("Bash", {"command": "grep -rn SECRET_KEY S3Uploader.py"}, "Bash: grep", None),
    ("Bash", {"command": """curl -d '{"X-Key-Id": "k1-prod"}' https://x.org"""},
     "Bash: curl", None),
    # a quote that opens on one line and closes on the
    # next, both ways; a snake_case name ending in cookie or authorization, no header's; curl's cookie, bearer and
    # proxy user; a header's value with its spaces escaped; two here-documents of one delimiter; `$'C:\\'`
    ("Bash", {"command": "python3 -c 'import sys\nprint(sys.argv)' a && cat > ~/.pgpass <<'EOF'\nlocalhost:5432:db:app:hunter2\nEOF\n"},
     "Bash: python3", "hunter2"),
    ("Bash", {"command": "python3 -c '\nmask = 1 << bits\nprint(mask)' && git push origin main"},
     "Bash: python3", None),
    ("Bash", {"command": 'echo "use_cookie: true" >> app.yml'}, "Bash: echo", None),
    ("Bash", {"command": 'python3 -c "def f(proxy_authorization: str): pass"'},
     "Bash: python3", None),
    ("Bash", {"command": 'curl --cookie "sid=a8f3c2d9e1b7" https://x.org'}, "Bash: curl", "a8f3c2d9"),
    ("Bash", {"command": 'curl -b "sid=a8f3c2d9e1b7" https://x.org'}, "Bash: curl", "a8f3c2d9"),
    ("Bash", {"command": "git checkout -b fix-cookie-expiry"}, "Bash: git", None),
    ("Bash", {"command": "curl --oauth2-bearer x7Kq2mP9vL4nR8tW https://x.org"}, "Bash: curl",
     "x7Kq2mP9"),
    ("Bash", {"command": "curl --proxy-user admin:hunter2 -x proxy:3128 https://x.org"},
     "Bash: curl", "hunter2"),
    ("Bash", {"command": "curl -H X-Authorization:\\ Bearer\\ x7Kq2mP9vL4nR8tW https://x.org"},
     "Bash: curl", "x7Kq2mP9"),
    ("Bash", {"command": "cat > a.txt <<EOF && cat > ~/.pgpass <<EOF\nhello\nEOF\nlocalhost:5432:db:app:hunter2\nEOF\nls"},
     "Bash: cat", "hunter2"),
    ("Bash", {"command": "printf $'C:\\\\' > f && cat > ~/.pgpass <<'EOF'\nlocalhost:5432:db:app:hunter2\nEOF\n"},
     "Bash: printf", "hunter2"),
    # a here-document of the script a quote across lines holds (ssh's, sh -c's): read as bash reads the quote, its
    # `<<` is inside it and its body would show; left out to its delimiter's line, which shows where it ends the quote
    ("Bash", {"command": "ssh host 'cat > ~/.pgpass <<EOF\nlocalhost:5432:db:app:hunter2\nEOF' && ls"},
     "Bash: ssh", "hunter2"),
    ("Bash", {"command": "sh -c \"cat > ~/.pgpass <<EOF\nlocalhost:5432:db:app:hunter2\nEOF\"\nls"},
     "Bash: sh", "hunter2"),
    ("Bash", {"command": "ssh host '\ncd /srv\ncat > .env <<EOF\nAPI=hunter2\nEOF\n./run.sh\n' && ls"},
     "Bash: ssh", "hunter2"),
    # shapes the cases above did not hold: curl's -U and a quoted --proxy-user; a $( ) and backticks in
    # double quotes, their own quotes read as their own (a here-document in either is
    # left out by a quoted script's reading too, so only quotes inside them tell); a comment's apostrophe, and a `#` inside a word that begins none; what the commit idiom leaves open, read
    # past its body to a here-document after it; arithmetic's own parentheses; a `<<` in a quote that ends on its line,
    # though a later line is its word
    ("Bash", {"command": "curl -U admin:hunter2 -x proxy:3128 https://x.org"},
     "Bash: curl", "hunter2"),
    ("Bash", {"command": "curl --proxy-user 'admin:hunter 2' -x proxy:3128 https://x.org"},
     "Bash: curl", "hunter 2"),
    ("Bash", {"command": "echo \"$(printf '%s' \"<<EOF\")\" && git push origin main"},
     "Bash: echo", None),
    ("Bash", {"command": "echo \"`printf '%s' \"<<EOF\"`\" && git push origin main"},
     "Bash: echo", None),
    ("Bash", {"command": "# don't forget\ncat > ~/.pgpass <<'EOF'\nlocalhost:5432:db:app:hunter2\nEOF\nls"},
     "Bash: cat", "hunter2"),
    ("Bash", {"command": "echo issue#12 && cat > ~/.pgpass <<'EOF'\nlocalhost:5432:db:app:hunter2\nEOF\n"},
     "Bash: echo", "hunter2"),
    ("Bash", {"command": "git commit -m \"$(cat <<'EOF'\nFix the thing\nEOF\n)\"\ncat > ~/.pgpass <<'EOF'\n"
                         "localhost:5432:db:app:hunter2\nEOF\nls"},
     "Bash: git", "hunter2"),
    ("Bash", {"command": "echo $(( (1 + (2)) << b ))\nls"}, "Bash: echo", None),
    ("Bash", {"command": "grep -c \"<<EOF\" setup.sh\nwc -l setup.sh\nEOF"},
     "Bash: grep", None),
    # a wrong reading the row before last let through (a quoted script's reading hid that body all the same): the commit
    # idiom's `)"` read from nothing open opens a quote, and an apostrophe after it hides the next here-document's `<<`
    ("Bash", {"command": "git commit -m \"$(cat <<'EOF'\nFix the thing\nEOF\n)\"\necho \"it's done\"\n"
                         "cat > ~/.pgpass <<'EOF'\nlocalhost:5432:db:app:hunter2\nEOF\nls"},
     "Bash: git", "localhost:5432"),
    # a `((` and a `$((` that bash reads as subshells, and a
    # regex's groups; a quote holding `<<` and its word across lines, closed before any line is its delimiter, double
    # and single; curl's header attached to -H; a snake_case name's value shaped like a credential; -b outside curl,
    # attached to it, and name=value bare; -U quoted; a proxy's user given in -x; header names in a search; a comment
    # after `;`
    ("Bash", {"command": "((cd /srv && make) 2>&1) | tail -5\ncat > ~/.pgpass <<'EOF'\n"
                         "localhost:5432:db:app:hunter2\nEOF\n"},
     "Bash", "hunter2"),
    ("Bash", {"command": "n=$((cd src && ls) | wc -l)\ncat > ~/.pgpass <<'EOF'\nlocalhost:5432:db:app:hunter2\nEOF\n"},
     "Bash", "hunter2"),
    ("Bash", {"command": "[[ $x =~ ^((a|b)c)$ ]] && echo y\ncat > ~/.pgpass <<'EOF'\n"
                         "localhost:5432:db:app:hunter2\nEOF\n"},
     "Bash", "hunter2"),
    ("Bash", {"command": "git commit -m \"Fix <<EOF\n\nAdds tests.\" && git push\ncat > ~/.pgpass <<EOF\n"
                         "localhost:5432:db:app:hunter2\nEOF\nls"},
     "Bash: git", "hunter2"),
    ("Bash", {"command": "git commit -m 'Escape <<EOF\nAdds tests.'\ncat > a.txt <<'EOF'\nhi\nEOF\n"
                         "cat > ~/.pgpass <<'EOF'\nlocalhost:5432:db:app:hunter2\nEOF\n"},
     "Bash: git",
     "hunter2"),
    ("Bash", {"command": "git commit -m \"Use <<'EOF' here\nAdds tests.\"\ncat > a.txt <<'EOF'\nhi\nEOF\n"
                         "cat > ~/.pgpass <<'EOF'\nlocalhost:5432:db:app:hunter2\nEOF\n"},
     "Bash: git",
     "hunter2"),
    ("Bash", {"command": "curl -HAuthorization:sk_live_a8f3c2d9e1b7 https://x.org"},
     "Bash: curl", "a8f3c2d9"),
    ("Bash", {"command": "curl -HCookie:sid=a8f3c2d9e1b7 https://x.org"}, "Bash: curl",
     "a8f3c2d9"),
    ("Bash", {"command": 'echo "session_cookie: a8f3c2d9e1b7" >> app.yml'},
     "Bash: echo", "a8f3c2d9"),
    ("Bash", {"command": "curl -H 'proxy_authorization: Basic YWRtaW46aHVudGVyMg==' https://x.org"},
     "Bash: curl", "YWRtaW46"),
    ("Bash", {"command": 'grep -b "DEBUG=1" .env'}, "Bash: grep", None),
    ("Bash", {"command": "curl -bsid=a8f3c2d9e1b7 https://x.org"}, "Bash: curl", "a8f3c2d9"),
    ("Bash", {"command": 'curl -b"sid=a8f3c2d9e1b7" https://x.org'}, "Bash: curl", "a8f3c2d9"),
    ("Bash", {"command": "curl -b sid=a8f3c2d9e1b7 https://x.org"}, "Bash: curl", "a8f3c2d9"),
    ("Bash", {"command": "curl -U 'admin:hunter 2' -x proxy:3128 https://x.org"},
     "Bash: curl", "hunter 2"),
    ("Bash", {"command": "curl -x admin:hunter2@proxy.local:3128 https://x.org"},
     "Bash: curl", "hunter2"),
    ("Bash", {"command": "sed -n '/Set-Cookie: /p' access.log"}, "Bash: sed", None),
    ("Bash", {"command": "grep -n 'Authorization: ' nginx.conf"}, "Bash: grep", None),
    ("Bash", {"command": "cd src;# it's here\ncat > ~/.pgpass <<'EOF'\nlocalhost:5432:db:app:hunter2\nEOF\nls"},
     "Bash: cd", "hunter2"),
    # shapes the cases above did not hold: a `<<` in a quote that ends on its line, the next line its
    # word; a `((`'s first `)` ending both, told by a double-quoted `$((` holding a quote of its own; a quoted script's
    # word on a line after its quote ends that no outer here-document holds; a cookie file after curl's -b
    ("Bash", {"command": 'echo "<<ls"\nls'}, "Bash: echo", None),
    ("Bash", {"command": "echo \"$((cd src && ls) | grep \"it's\")\" && cat > ~/.pgpass <<'EOF'\n"
                         "localhost:5432:db:app:hunter2\nEOF\n"},
     "Bash: echo", "hunter2"),
    ("Bash", {"command": 'git commit -m "Run <<make first\nthen test"\nmake\nls'},
     "Bash: git", None),
    ("Bash", {"command": "curl -b cookies.txt https://x.org"}, "Bash: curl", None),
    # an apostrophe put into a single-quoted script
    # as '\'' or '"'"', after the body and before it; curl's -b on a continuation line, and after `&&`; a boolean after
    # a snake_case key and after a camelCase header's name; a proxy's user quoted, after -x and after --proxy=, and bare
    # after --proxy; a snake_case name's credential quoted; a shift in `$((` after a letter
    ("Bash", {"command": "sudo sh -c 'cat > ~/.pgpass <<EOF\nlocalhost:5432:db:app:hunter2\n# don'\\''t share\nEOF'"},
     "Bash: sudo", "hunter2"),
    ("Bash", {"command": "sudo sh -c 'cat > ~/.pgpass <<EOF\n# don'\\''t share\nlocalhost:5432:db:app:hunter2\nEOF'"},
     "Bash: sudo", "hunter2"),
    ("Bash", {"command": "docker exec -i db sh -c 'cat > ~/.pgpass <<EOF\nit'\"'\"'s here\n"
                         "localhost:5432:db:app:hunter2\nEOF'"},
     "Bash: docker", "hunter2"),
    ("Bash", {"command": "curl -s https://x.org \\\n-H \"Accept: application/json\" \\\n"
                         "-b \"session=a8f3c2d9e1b7c4d5\""},
     "Bash: curl", "a8f3c2d9"),
    ("Bash", {"command": 'cd /tmp && curl -b "sid=a8f3c2d9e1b7" https://x.org'},
     "Bash: cd", "a8f3c2d9"),
    ("Bash", {"command": "echo '{\"use_cookie\": true}' > cfg.json"},
     "Bash: echo", None),
    ("Bash", {"command": "python3 -c \"print({'use_cookie': True})\""},
     "Bash: python3", None),
    ("Bash", {"command": "grep -rn 'skipAuthorization: true' src/"},
     "Bash: grep", None),
    ("Bash", {"command": "curl -x 'admin:hunter2@proxy.local:3128' https://x.org"},
     "Bash: curl", "hunter2"),
    ("Bash", {"command": "curl --proxy='admin:hunter2@proxy:3128' https://x.org"},
     "Bash: curl", "hunter2"),
    ("Bash", {"command": "curl --proxy admin:hunter2@proxy:3128 https://x.org"},
     "Bash: curl", "hunter2"),
    ("Bash", {"command": "echo 'session_cookie: \"a8f3c2d9e1b7\"' >> app.yml"},
     "Bash: echo", "a8f3c2d9"),
    ("Bash", {"command": "echo $((x << bits))\nls"}, "Bash: echo", None),
    # shapes the cases above did not hold: a `<<` in a quote that ends, before another opens on its line,
    # the next line its word (which the line's end alone cannot tell); a quote that goes on in an escaped one and
    # then in none, so the script's word on the line after is past its end
    ("Bash", {"command": 'echo "<<ls" "and\nls\nmore"'}, "Bash: echo", None),
    ("Bash", {"command": "sh -c 'cat > f <<EOF\ndon'\\'t\nEOF\nls"},
     "Bash: sh", None),
    # a quoted script's delimiter quoted inside
    # its quote, as '\'' or '"'"', and one whose word goes on past an apostrophe; a word that goes on past a quote into
    # a variable, a `${ }`, a `$( )` or a `$(( ))`; a boolean before a comma in a quoted header; -b where the command
    # names curl only in a commit's message, and -x where it names none; a word going on in `$'` or `\"`, a `<<` in
    # double quotes that close into a single quote, the line's end ending a word, a word ending at a blank; `false`,
    # `None` and `null`; a proxy's user in double quotes; a snake_case name's credential in single quotes; a key's
    # value whatever it is; curl on a continuation line; and here-documents in a `$'...'` script and, behind an
    # escaped quote, in a double-quoted one
    ("Bash", {"command": "ssh db 'cat > ~/.pgpass <<'\\''EOF'\\''\nlocalhost:5432:db:app:hunter2\nEOF\n'"},
     "Bash: ssh", "hunter2"),
    ("Bash", {"command": "ssh db 'cat > ~/.pgpass <<'\"'\"'EOF'\"'\"'\nlocalhost:5432:db:app:hunter2\nEOF\n'"},
     "Bash: ssh", "hunter2"),
    ("Bash", {"command": "sudo sh -c 'cat > ~/.pgpass <<EOF && echo '\\''written'\\''\n"
                         "localhost:5432:db:app:hunter2\nEOF'"},
     "Bash: sudo", "hunter2"),
    ("Bash", {"command": "ssh h 'cat > ~/.pgpass <<EOF\nlocalhost:5432:db:app:hunter2\ndb2:5432:app:'$U':x\nEOF'"},
     "Bash: ssh", "hunter2"),
    ("Bash", {"command": "ssh h 'cat > ~/.pgpass <<EOF\nlocalhost:5432:db:app:hunter2\ndb2:5432:app:'${U}':x\nEOF'"},
     "Bash: ssh", "hunter2"),
    ("Bash", {"command": "ssh h 'cat > ~/.pgpass <<EOF\nlocalhost:5432:db:app:hunter2\n"
                         "db2:5432:app:'$(whoami)':x\nEOF'"},
     "Bash: ssh", "hunter2"),
    ("Bash", {"command": "ssh h 'cat > ~/.pgpass <<EOF\nlocalhost:5432:db:app:hunter2\n"
                         "db2:5432:app:'$((N+1))':x\nEOF'"},
     "Bash: ssh", "hunter2"),
    ("Bash", {"command": "grep -rn 'useCookie: false,' src/"}, "Bash: grep", None),
    ("Bash", {"command": 'grep -b "API_MODE=1" .env && git commit -m "drop curl"'},
     "Bash: grep", None),
    ("Bash", {"command": "grep -x 'alice:x@y' users.txt"}, "Bash: grep", None),
    ("Bash", {"command": "sh -c 'cat > f <<EOF\na'$'\\t''b\nhunter2\nEOF'"},
     "Bash: sh", "hunter2"),
    ("Bash", {"command": "sh -c 'cat > f <<EOF\na'\\\"'b\nhunter2\nEOF'"},
     "Bash: sh", "hunter2"),
    ("Bash", {"command": "sh -c \"cat > f <<EOF\"'\nhunter2\nEOF'"},
     "Bash: sh", "hunter2"),
    ("Bash", {"command": "echo \"<<EOF\"\\'\nEOF\nls"}, "Bash: echo", None),
    ("Bash", {"command": "ssh h 'cat > f <<EOF\nx'y 'z\nEOF'"}, "Bash: ssh", None),
    ("Bash", {"command": "echo '{\"use_cookie\": false}' > cfg.json"},
     "Bash: echo", None),
    ("Bash", {"command": "python3 -c \"print({'use_cookie': None})\""},
     "Bash: python3", None),
    ("Bash", {"command": "grep -rn 'skipAuthorization: null' src/"},
     "Bash: grep", None),
    ("Bash", {"command": "curl -x \"admin:hunter2@proxy.local:3128\" https://x.org"},
     "Bash: curl", "hunter2"),
    ("Bash", {"command": "echo \"session_cookie: 'a8f3c2d9e1b7'\" >> app.yml"},
     "Bash: echo", "a8f3c2d9"),
    ("Bash", {"command": "echo '{\"use_cookie\": \"yes\"}' > cfg.json"},
     "Bash: echo", None),
    ("Bash", {"command": "cd /tmp && \\\ncurl -s https://x.org \\\n-b \"session=a8f3c2d9e1b7\""},
     "Bash: cd", "a8f3c2d9"),
    ("Bash", {"command": "bash -c $'cat > f <<EOF\nhunter2\nEOF'"},
     "Bash: bash", "hunter2"),
    ("Bash", {"command": "sh -c \"cat > f <<\\\"EOF\\\"\nhunter2\nEOF\n\""},
     "Bash: sh", "hunter2"),
    # shapes the cases above did not hold: a word going on past a quote in a plain character; a
    # snake_case name's credential right after its colon; a quoted header's number with more after a blank; a quoted
    # header's boolean before a `}`; a proxy's password after --proxy where no curl is named
    ("Bash", {"command": "ssh h 'cat > ~/.pgpass <<EOF\nlocalhost:5432:db:app:hunter2\nhome:'~':x\nEOF'"},
     "Bash: ssh", "hunter2"),
    ("Bash", {"command": "echo 'session_cookie:a8f3c2d9e1b7' >> app.yml"},
     "Bash: echo", "a8f3c2d9"),
    ("Bash", {"command": "curl -H 'X-Api-Key: 2024 a8f3c2d9e1b7' https://x.org"},
     "Bash: curl", "a8f3c2d9"),
    ("Bash", {"command": "grep -rn 'useCookie: false}' src/"}, "Bash: grep", None),
    ("Bash", {"command": "pip install --proxy admin:hunter2@proxy:3128 requests"},
     "Bash: pip", "hunter2"),
    # misreadings a later reading let through, each now ending on its row's answer by a
    # quoted script's here-document instead: a comment's apostrophe read as a quote and `$'...'` read as '...', each
    # told apart by an apostrophe in the body, which ends the misread's quote before the delimiter
    ("Bash", {"command": "# don't forget\ncat > ~/.pgpass <<'EOF'\n# it's here\n"
                         "localhost:5432:db:app:hunter2\nEOF\nls"},
     "Bash: cat", "hunter2"),
    ("Bash", {"command": "printf $'it\\'s\\n' && cat > ~/.pgpass <<EOF\n# don't share\n"
                         "localhost:5432:db:app:hunter2\nEOF"},
     "Bash: printf", "hunter2"),
    # another, let through the same way: what a line leaves open forgotten
    # after a here-document, told apart by a `"` in the next here-document's body
    ("Bash", {"command": "git commit -m \"$(cat <<'EOF'\nFix the thing\nEOF\n)\"\ncat > ~/.pgpass <<'EOF'\n"
                         "# a 6\" pipe\nlocalhost:5432:db:app:hunter2\nEOF\nls"},
     "Bash: git", "hunter2"),
    # a `;` after a key's boolean, which the masks' comment says
    # is shown; a `$'...'` script's delimiter quoted by `\x27` and by `\047`, which is read as the
    # script reads it; and a pin, `<<\EOF` in a `$'...'` script, whose `\E` bash reads as an escape, so no delimiter
    ("Bash", {"command": "echo '{\"use_cookie\": false;}' > cfg.json"},
     "Bash: echo", None),
    ("Bash", {"command": "bash -c $'cat > f <<\\x27EOF\\x27\nhunter2\nEOF'"},
     "Bash: bash", "hunter2"),
    ("Bash", {"command": "bash -c $'cat > f <<\\047EOF\\047\nhunter2\nEOF'"},
     "Bash: bash", "hunter2"),
    ("Bash", {"command": "bash -c $'cat > f <<\\EOF\nhunter2\nEOF'"},
     "Bash: bash", "hunter2"),
    # curl's -x and -b in curl.exe and curlie, and -x in any
    # command again, unquoted too; a credential's name's value in the other kind of quote, a bare number's there shown;
    # a word going on past a quote in a letter (KEY='value'), and a `;` ending one; `$'...'`'s \x22; a `;` and a `]`
    # after a quoted header's boolean, and a `,` after httpie's
    ("Bash", {"command": "curl.exe -x admin:hunter2@proxy:3128 https://x.org"},
     "Bash: curl.exe", "hunter2"),
    ("Bash", {"command": "curlie -x 'admin:hunter2@proxy:3128' https://x.org"},
     "Bash: curlie", "hunter2"),
    ("Bash", {"command": "curl.exe -b \"sid=a8f3c2d9e1b7\" https://x.org"},
     "Bash: curl.exe", "a8f3c2d9"),
    ("Bash", {"command": "curlie -b \"sid=a8f3c2d9e1b7\" https://x.org"},
     "Bash: curlie", "a8f3c2d9"),
    ("Bash", {"command": "grep -x alice:x@y users.txt"}, "Bash: grep", None),
    ("Bash", {"command": "echo 'password: \"S3cr3tPassw0rd2024\"' >> config.yml"},
     "Bash: echo", "S3cr3t"),
    ("Bash", {"command": "echo 'db_password: \"S3cr3t!pass\"' >> config.yml"},
     "Bash: echo", "S3cr3t"),
    ("Bash", {"command": "echo \"X-Api-Key: 'a8f3c2d9e1b7'\""}, "Bash: echo", "a8f3c2d9"),
    ("Bash", {"command": "echo 'max_tokens: \"4096\"' >> cfg.yml"},
     "Bash: echo", None),
    ("Bash", {"command": "ssh h 'cat > app.env <<EOF\nexport PW='hunter2'\nlocalhost:5432:db:app:hunter2\nEOF'"},
     "Bash: ssh", "hunter2"),
    ("Bash", {"command": "echo '<<ls';'and\nls\nmore'"}, "Bash: echo", None),
    ("Bash", {"command": "bash -c $'cat > f <<\\x22EOF\\x22\nhunter2\nEOF'"},
     "Bash: bash", "hunter2"),
    ("Bash", {"command": "grep -rn 'useCookie: false;' src/"}, "Bash: grep", None),
    ("Bash", {"command": "grep -rn 'useCookie: true]' src/"}, "Bash: grep", None),
    ("Bash", {"command": "http x.org Cookie:'false,'"}, "Bash: http", None),
    # a credential's name, blanks, its colon, and its value in quotes of its own
    ("Bash", {"command": "echo 'password : \"S3cr3tPassw0rd2024\"' >> config.yml"},
     "Bash: echo", "S3cr3t"),
    ("Bash", {"command": "echo \"X-Api-Key : 'a8f3c2d9e1b7'\""}, "Bash: echo", "a8f3c2d9"),
    # a value in escaped quotes inside the name's own; a
    # quote of the kind the name stands inside, which closes it and opens no value, with blanks before it or none, the
    # name right after that quote or not, and a value after a name that did so; blanks before a name in the other
    # quote; an empty value and a name after `/` in the other quote; curl's -b in capitals; `<<-` with a quoted
    # delimiter in a quoted script; a word ended by `|`, `&`, `<` or `>`; a `;` and a `]` after httpie's boolean; and
    # `$'...'`'s \u0022
    ("Bash", {"command": 'echo "password: \\"hunter2\\"" >> config.yml'},
     "Bash: echo", "hunter2"),
    ("Bash", {"command": 'echo "X-Api-Key: \\"a8f3c2d9e1b7\\""'}, "Bash: echo", "a8f3c2d9"),
    ("Bash", {"command": 'printf "%s\\n" "db_password: \\"S3cr3t!pass\\""'},
     "Bash: printf", "S3cr3t"),
    ("Bash", {"command": "grep -rn 'token: ' . | grep -v 'test'"}, "Bash: grep", None),
    ("Bash", {"command": 'grep -rn "token: " . --include="*.yml"'},
     "Bash: grep", None),
    ("Bash", {"command": 'echo "Password: "; read -s PW; curl -u "admin:$PW" https://x.org'},
     "Bash: echo", None),
    ("Bash", {"command": "grep 'token:' f | grep 'x'"}, "Bash: grep", None),
    ("Bash", {"command": 'read -p "Enter password: " PW && echo "done"'},
     "Bash: read", None),
    ("Bash", {"command": "grep 'token: ' password: 'hunter2x'"}, "Bash: grep", "hunter2x"),
    ("Bash", {"command": "echo '  password: \"S3cr3tPass\"'"}, "Bash: echo", "S3cr3t"),
    ("Bash", {"command": "grep -rn 'password: \"\"' config/"}, "Bash: grep", None),
    ("Bash", {"command": "awk '/password: \"/ {print \"found\"}' f"},
     "Bash: awk", None),
    ("Bash", {"command": 'CURL.EXE -b "sid=a8f3c2d9e1b7" https://x.org'}, "Bash: CURL.EXE",
     "a8f3c2d9"),
    ("Bash", {"command": "sh -c 'cat > f <<-'\\''EOF'\\''\n\thunter2\n\tEOF'"},
     "Bash: sh", "hunter2"),
    ("Bash", {"command": "echo '<<ls'|'and\nls\nmore'"}, "Bash: echo", None),
    ("Bash", {"command": "echo '<<ls'&'and\nls\nmore'"}, "Bash: echo", None),
    ("Bash", {"command": "echo '<<ls'<'and\nls\nmore'"}, "Bash: echo", None),
    ("Bash", {"command": "echo '<<ls'>'and\nls\nmore'"}, "Bash: echo", None),
    ("Bash", {"command": "http x.org Cookie:'false;'"}, "Bash: http", None),
    ("Bash", {"command": "http x.org Cookie:'null]'"}, "Bash: http", None),
    ("Bash", {"command": "bash -c $'cat > f <<\\u0022EOF\\u0022\nhunter2\nEOF'"},
     "Bash: bash", "hunter2"),
    # the quote a name stands inside read through `$'...'`'s escapes,
    # and through an escaped quote outside any quote, so neither ends a quote before the name
    ("Bash", {"command": "printf $'it\\'s a token: ' && echo 'x'"}, "Bash: printf",
     None),
    ("Bash", {"command": "echo don\\'t; grep 'token: ' f | grep 'x'"},
     "Bash: echo", None),
    # A value in single quotes that begins with a backslash, which is no escape there: masked to the closing quote (a
    # fix that left it to the bare-value mask showed the rest)
    ("Bash", {"command": "echo 'password: \\'; ls"}, "Bash: echo", None),
    ("Bash", {"command": "sh -c 'token: \\x27; echo x'"}, "Bash: sh", "echo x"),
    # and one in double quotes after an escaped quote, holding a blank: masked to the quote not escaped, never to the
    # blank (the bare value's mask, which an earlier fix left it to, showed ` words\""`)
    ("Bash", {"command": 'echo "password: \\"two words\\""'}, "Bash: echo", "words"),
    # a value in quotes escaped inside another's, `-c "...[\"token: x y\"]..."`: read after its escaped opening quote to
    # its next `"`, its escaped closing quote kept as written (read to the blank, it showed
    # ` y\"`; written as a bare `"`, it turned the line's later quotes about)
    ("Bash", {"command": "python3 -c \"for c in ['sh -c \\\"token: x y\\\"']: print(c)\""},
     "Bash: python3", " y\\"),
    # a name in escaped quotes inside a double-quoted
    # script, the rest of the script shown; an escaped value with a `'` after it, a line's end before its closing
    # quote, and a `'` in a double-quoted value; a name after `/`, as sed writes one; a line begun inside a quote, and
    # one after a value in escaped quotes, a cookie after them masked; a `'` before the name's quote; Curl.exe
    ("Bash", {"command": 'docker exec app sh -c "curl -sf -H \\"X-Api-Key: $KEY\\" http://localhost:8080/reset && rm '
                         '-rf /data/*"'},
     "Bash: docker", None),
    ("Bash", {"command": 'ssh prod "curl -s -H \\"Authorization: Bearer $TOKEN\\" https://api.x.org/v1/health | jq '
                         '.status"'},
     "Bash: ssh", None),
    ("Bash", {"command": 'ssh prod "grep \\"password: \\" /etc/app.conf | head -5"'},
     "Bash: ssh", None),
    ("Bash", {"command": 'echo "password: \\"hunter2\\" isn\'t it" >> notes.txt'},
     "Bash: echo", "hunter2"),
    ("Bash", {"command": 'printf "X-Api-Key: \\"a8f3c2d9e1b7\\" (the team\'s)\\n"'}, "Bash: printf",
     "a8f3c2d9"),
    ("Bash", {"command": 'echo "password: \\"hunter2\\"\nis the old one" > f'},
     "Bash: echo", "hunter2"),
    ("Bash", {"command": 'echo "password: it\'sMyS3cret!"'}, "Bash: echo", "S3cret"),
    ("Bash", {"command": "sed -i 's/password: .*/password: \"NewP4ss!word\"/' config.yml"},
     "Bash: sed", "NewP4ss"),
    ("Bash", {"command": "ssh h '\ncd /app && ./run\n' && http x.org Cookie:'sid=abc123'"},
     "Bash: ssh", "abc123"),
    ("Bash", {"command": "python3 -c \"for c in ['sh -c \\\"token: x y\\\"']: print(c)\" && http x.org "
                         "Cookie:'sid=abc123'"},
     "Bash: python3", "abc123"),
    ("Bash", {"command": "echo \"it's done\"; grep 'token: ' f | grep 'x'"},
     "Bash: echo", None),
    ("Bash", {"command": 'Curl.exe -b "sid=a8f3c2d9e1b7" https://x.org'}, "Bash: Curl.exe",
     "a8f3c2d9"),
    # a header's value quoted in a single-quoted script by '\'' or '"'"', or bare after '\', each a `'` as the
    # script reads it, on its line or on one begun inside the quote; never one with a blank after that `'`, which
    # closes the script's own quote (`grep '\''password: '\'' f`), nor an empty value or a bare number there
    ("Bash", {"command": "ssh prod 'echo password: '\\''hunter2'\\'' >> /etc/app.yml'"},
     "Bash: ssh", "hunter2"),
    ("Bash", {"command": "ssh prod 'echo Authorization: '\"'\"'Bearer a8f3c2d9e1b7'\"'\"' >> f'"},
     "Bash: ssh", "a8f3c2d9"),
    ("Bash", {"command": "echo 'db_password: '\\'S3cr3t!pass\\' >> config.yml"},
     "Bash: echo", "S3cr3t"),
    ("Bash", {"command": "ssh prod '\necho password: '\\''hunter2'\\'' >> f\n'"},
     "Bash: ssh", "hunter2"),
    ("Bash", {"command": "ssh prod 'grep '\\''password: '\\'' /etc/app.conf'"},
     "Bash: ssh", None),
    ("Bash", {"command": "ssh prod 'grep password: '\\'''\\'' f'"}, "Bash: ssh",
     None),
    ("Bash", {"command": "ssh prod 'echo max_tokens: '\\''4096'\\'' >> f'"},
     "Bash: ssh", None),
    # and the same shape where its name stands in a double quote or in none, masked all the same, as the third mask
    # no longer reads its `'\'` (when it did, `'***''hunter2'\''`)
    ("Bash", {"command": "echo \"password: '\\''hunter2'\\''\" >> notes.txt"},
     "Bash: echo", "hunter2"),
    ("Bash", {"command": "echo password: '\\''hunter2'\\'''"}, "Bash: echo", "hunter2"),
    # a delimiter's end at any escape: a delimiter after a backslash in a
    # double-quoted script whose word goes on past the quote's end, read to the word's end (`EOFx`), its body left out
    ("Bash", {"command": "sh -c \"cat > f <<\\EOF\"x'\nhunter2\nEOFx\n'"},
     "Bash: sh", "hunter2"),
    # a line inside a `( )` or a `$( )`, read from
    # its start; one begun inside a quote an earlier line left open, read from its start to where that quote ends and
    # as the command reads it after (a multi-line `bash -c "`'s `read -p`, the grep after `ssh h '...'`); a header's
    # `"` inside single quotes; a value in a single-quoted script's own quote after `=`, `--name=`, `-u`'s user and an
    # identifier, and a header's in `'"'...'"'`; a `'` in a value in quotes escaped inside a double-quoted script's; a
    # boolean in the idiom
    ("Bash", {"command": "(\ncd /srv && grep -l 'token: ' *.yml | xargs rm -f && echo 'done'\n)"},
     "Bash", None),
    ("Bash", {"command": "(\ngrep 'token: ' password: 'hunter2x'\n)"}, "Bash",
     "hunter2x"),
    ("Bash", {"command": "x=$(\ngrep 'token: ' . | grep -v 'test'\n)"},
     "Bash", None),
    ("Bash", {"command": "bash -c \"\nread -p 'Enter password: ' PW && echo 'done'\n\""},
     "Bash: bash", None),
    ("Bash", {"command": "ssh h '\ncd /app\n' && grep 'token: ' . | grep -v 'test'"},
     "Bash: ssh", None),
    ("Bash", {"command": "grep -rl '\"Authorization: Bearer' docs/ | xargs rm -f && echo \"removed\""},
     "Bash: grep", None),
    ("Bash", {"command": "ssh prod 'export DB_PASSWORD='\\''S3cret!pass'\\'' && ./run'"},
     "Bash: ssh", "S3cret"),
    ("Bash", {"command": "ssh db 'mysql --password='\\''S3cret!pass'\\'' -e status'"},
     "Bash: ssh", "S3cret"),
    ("Bash", {"command": "ssh h 'curl -u admin:'\\''S3cret!pass'\\'' https://x.org'"},
     "Bash: ssh", "S3cret"),
    ("Bash", {"command": "ssh h 'aws configure set aws_secret_access_key '\\''a8f3c2d9e1b7S3cretKey'\\'''"},
     "Bash: ssh", "a8f3c2d9"),
    ("Bash", {"command": "ssh prod 'export DB_PASSWORD='\"'\"'S3cret!pass'\"'\"' && ./run'"},
     "Bash: ssh", "S3cret"),
    ("Bash", {"command": "ssh prod 'echo password: '\"'hunter2'\"' >> f'"},
     "Bash: ssh", "hunter2"),
    ("Bash", {"command": "ssh prod \"echo \\\"password: it'sMyS3cret!\\\" >> /etc/app.yml\""},
     "Bash: ssh", "S3cret"),
    ("Bash", {"command": "ssh prod 'grep -rn useCookie: '\\''false'\\'' src/'"},
     "Bash: ssh", None),
    # a `$( )` closing inside the quote a line began in, before that
    # quote ends; a header's value with a blank in a `"` inside single quotes, which never runs past their `'`; a
    # flag's value after a blank in a single-quoted script's own quote
    ("Bash", {"command": "bash -c \"\necho x\n$(date)\" && grep 'token: ' . | grep -v 'test'"},
     "Bash: bash", None),
    ("Bash", {"command": "grep '\"password: two words\"' notes.md"}, "Bash: grep",
     "words"),
    ("Bash", {"command": "ssh db 'mysql --password '\\''S3cret!pass'\\'' -e status'"},
     "Bash: ssh", "S3cret"),
    # the quote's reading not begun again for a place before the
    # last: mask 2 declines at a `"` inside single quotes and changes nothing, so mask 3 asks the same line for an
    # earlier place, outside any quote, where a value is masked
    ("Bash", {"command": "grep token: 'hunter2x' && grep -rl '\"Authorization: Bearer' docs/ | xargs rm -f && "
                         "echo \"done\""},
     "Bash: grep", "hunter2x"),
    # a name with the idiom's `'` and a blank after
    # it, which closes a grep's pattern; prose in a quote an earlier line left open, its apostrophe no quote, read as
    # bash reads the command, in a double quote and in a single one; the line that closes a multi-line `"$( )"`; a
    # user's password, a proxy's, curl's cookie and a key's value in a single-quoted script's own quote; a file named
    # in the idiom; a header after a value with an apostrophe; a line begun in a single quote, read inside it as bash
    # reads it (a script's own quotes in it not read, as on one line); multi-line backticks; an empty value in the
    # idiom. The prose rows and the header after the multi-line `"$( )"` pass under any line's reading, as the rest of a
    # word after a quote that closes its name's is masked; the rows with a
    # `read -p` begun in a quote, and the grep pairs after a multi-line `"$( )"` below, are the ones that read it
    ("Bash", {"command": "ssh prod 'grep -q '\\''DB_PASSWORD='\\'' .env && rm -f .env.bak && echo '\\''ok'\\'''"},
     "Bash: ssh", None),
    ("Bash", {"command": "ssh prod 'grep '\\''^DB_PASSWORD='\\'' /etc/app.env | wc -l'"},
     "Bash: ssh", None),
    ("Bash", {"command": "git commit -m \"Fix login\nIt's the token: 'abc12345' in the docs\n\""},
     "Bash: git", "abc12345"),
    ("Bash", {"command": "git tag -a v1 -m \"\nThe client's config had password: 'hunter2x' hardcoded.\n\""},
     "Bash: git", "hunter2x"),
    ("Bash", {"command": "git commit -m '\nFix the 6\" pipe; token: \"abc12345\" was wrong\n'"},
     "Bash: git", "abc12345"),
    ("Bash", {"command": "out=\"$(\ndate\n)\" && http POST x.org Authorization:\"Bearer abc123def\""},
     "Bash", "abc123def"),
    ("Bash", {"command": "x=\"$(\ndate\n)\" && grep 'token: ' . | grep -v 'test'"},
     "Bash", None),
    ("Bash", {"command": "out=\"$(ssh h '\ncd /app\n')\"; grep 'token: ' . | grep -v 'test'"},
     "Bash", None),
    ("Bash", {"command": "ssh h 'curl -u '\\''admin:S3cret!pass'\\'' https://x.org'"},
     "Bash: ssh", "S3cret"),
    ("Bash", {"command": "ssh h 'curl -x '\\''bob:S3cret@proxy:8080'\\'' https://x.org'"},
     "Bash: ssh", "S3cret"),
    ("Bash", {"command": "ssh h 'curl --proxy-user '\\''bob:S3cret'\\'' https://x.org'"},
     "Bash: ssh", "S3cret"),
    ("Bash", {"command": "ssh h 'curl --cookie '\\''sid=abc123xyz'\\'' https://x.org'"},
     "Bash: ssh", "abc123xyz"),
    ("Bash", {"command": "ssh h 'echo {\"password\": '\\''S3cret!pass'\\''}'"},
     "Bash: ssh", "S3cret"),
    ("Bash", {"command": "ssh h 'grep -rn SECRET_KEY '\\''settings.py'\\'''"},
     "Bash: ssh", None),
    ("Bash", {"command": "curl -H \"X-Api-Key: abcdefgh12345678\" -H \"Authorization: Bearer it's\" && "
                         "grep 'token: ' . | grep -v 'test'"},
     "Bash: curl",
     "abcdefgh"),
    ("Bash", {"command": "ssh h '\nread -p \"Enter password: \" PW && echo \"done\"\n'"},
     "Bash: ssh", None),
    ("Bash", {"command": "x=`\ngrep 'token: ' . | grep -v 'test'\n`"},
     "Bash", None),
    ("Bash", {"command": "ssh h 'export DB_PASSWORD='\\'''\\'' && ./run'"},
     "Bash: ssh", None),
    # a value after the quote that closes its name's, the word going on after
    # it (`echo "password: "..."`), which showed on one line once such a quote was declined, and on a line begun in a
    # double quote once such a line was read as one; a quote that closes its name's
    # before a `)`, which ends the word; a line after the line that closes a quote an earlier line left open; and a line
    # begun in a double quote opened after a `$(` on the line that closed the quote before it, its `'` read as bash
    # reads them there
    ("Bash", {"command": 'echo "password: "hunter2x""'}, "Bash: echo", "hunter2x"),
    ("Bash", {"command": "git commit -m \"\nIt's the token: \"abc12345\" here\n\""},
     "Bash: git", "abc12345"),
    ("Bash", {"command": "ssh h '\ncd /app' && out=$(echo \"\npassword: \"hunter2x\"\n\")"},
     "Bash: ssh", "hunter2x"),
    ("Bash", {"command": 'x=$(echo "token: ") && echo "done"'}, "Bash", None),
    ("Bash", {"command": "ssh h '\ncd /app'\nls -la"}, "Bash: ssh", None),
    ("Bash", {"command": "ssh h '\ncd /app' && out=$(echo \"\ngrep 'token: ' . | grep -v 'test'\n\")"},
     "Bash: ssh", None),
    # the quote's reading never begun again for a place before the last, again, which the
    # row for it above no longer catches once mask 3 declines only where the word ends after the value's quote: mask 2
    # declines at a `"` inside single quotes and changes nothing, and mask 3 asks for an earlier place inside a double
    # quote, where a `"` and a blank after it close the name's quote and open no value
    ("Bash", {"command": "grep -rn \"token: \" src/ \"lib\" && grep -rl '\"Authorization: Bearer' docs/ | xargs rm -f && "
                         "echo \"done\""},
     "Bash: grep",
     None),
    # a flag's value in the idiom that reads as a path, masked by the
    # flag's mask whatever it is, as on one line, which the identifier's mask after it would show; and a value after the
    # quote that closes its name's whose first character is neither a letter nor a digit
    ("Bash", {"command": "ssh db 'mysql --password-file '\\''/run/secrets/db'\\'' -e status'"},
     "Bash: ssh", "secrets"),
    ("Bash", {"command": 'echo "password: "@dm1nS3cret""'}, "Bash: echo", "S3cret"),
    # over the multi-line shapes above: a value read in quotes that ends with a credential's
    # name and its colon (`' password: '`), whose closing quote opens that name's value, not this one's: it showed the
    # value after it on one line, and in a multi-line `bash -c "` once its line was read as one line; pins: a value
    # ending with a word that names no credential and its colon, and one ending with a blank
    # after a name and its colon inside it
    ("Bash", {"command": "bash -c \"grep 'token: ' password: 'hunter2x'\""},
     "Bash: bash", "hunter2x"),
    ("Bash", {"command": "bash -c \"\ngrep 'token: ' password: 'hunter2x'\n\""},
     "Bash: bash", "hunter2x"),
    ("Bash", {"command": "echo \"token: ' Authorization: 'Bearer abc123def'\""},
     "Bash: echo", "abc123def"),
    ("Bash", {"command": "echo \"token: 'abc12345 note:'\""}, "Bash: echo", "abc12345"),
    ("Bash", {"command": "echo \"token: 'my password: is long '\""}, "Bash: echo", None),
    # after a quote that closes its name's, the rest of the word alone, not to the next
    # quote of its kind, which swallowed a later name (an export's, a key's, a flag's, a user's) and showed its value
    # or hid a command
    ("Bash", {"command": 'read -p "Enter password: "PW && export DB_PASSWORD="hunter2x"'},
     "Bash: read", "hunter2x"),
    ("Bash", {"command": 'echo "token: "$T; export API_KEY="sk-abc123def456"'},
     "Bash: echo", "sk-abc123"),
    ("Bash", {"command": 'read -p "Enter password: "PW && make clean && echo "done"'},
     "Bash: read", None),
    ("Bash", {"command": 'echo "token: "$T && mysql --password "hunter2x" -e status'},
     "Bash: echo", "hunter2x"),
    ("Bash", {"command": 'echo "token: "$T && curl -u "admin:S3cret!pass" https://x.org'},
     "Bash: echo", "S3cret"),
    # a value read in quotes that ends with what a later mask reads as a name, an export's, a flag's, a
    # cookie's, an identifier's, a user's, an argument's, in a multi-line script and on one line; pins: a value that
    # ends with a name and its colon but holds more before it (a decline only where the whole value is a name and its
    # colon passes the rest), and one ending with a key's name
    ("Bash", {"command": "bash -c \"\ngrep 'token: ' app.log; export DB_PASSWORD='hunter2x'; ./run\n\""},
     "Bash: bash", "hunter2x"),
    ("Bash", {"command": "ssh h '\ngrep \"token: \" app.log; export DB_PASSWORD=\"hunter2x\"\n'"},
     "Bash: ssh", "hunter2x"),
    ("Bash", {"command": "python3 -c \"\nprint('token: ', t); conn = connect(password='hunter2x')\n\""},
     "Bash: python3", "hunter2x"),
    ("Bash", {"command": "bash -c \"\ngrep 'token: ' app.log; mysql --password 'hunter2x' -e status\n\""},
     "Bash: bash", "hunter2x"),
    ("Bash", {"command": "bash -c \"\ngrep 'token: ' app.log; curl --cookie 'sid=abc123xyz' https://x.org\n\""},
     "Bash: bash", "abc123xyz"),
    ("Bash", {"command": "bash -c \"\ngrep 'token: ' f; aws configure set aws_secret_access_key "
                         "'a8f3c2d9e1b7S3cretKey'\n\""},
     "Bash: bash", "a8f3c2d9"),
    ("Bash", {"command": "bash -c \"grep 'token: ' f; curl -u 'admin:S3cret!pass' https://x.org\""},
     "Bash: bash", "S3cret"),
    ("Bash", {"command": "bash -c \"grep 'token: ' app.log; echo password: 'hunter2x'\""},
     "Bash: bash", "hunter2x"),
    ("Bash", {"command": "bash -c \"grep 'token: ' f; echo {\\\"password\\\": 'hunter2x'}\""},
     "Bash: bash", "hunter2x"),
    # a line begun where one line's reading of the command has closed the quote that _live_scan's outermost
    # still holds open, read as one line's reading has it; pins: a `$( )` inside double quotes read as the quote's
    # text, multi-line as on one line (its `'` no quotes, so the masks read a name and a quoted value across)
    ("Bash", {"command": "x=\"$(echo \"a\nb\")\" && grep 'token: ' . | grep -v 'test'"},
     "Bash", None),
    ("Bash", {"command": "n=\"$(\ngrep -c 'password: ' a.yml | tr -d ' '\n)\""},
     "Bash", None),
    ("Bash", {"command": "n=\"$(grep -c 'password: ' a.yml | tr -d ' ')\""},
     "Bash", None),
    # the idiom's opening before a `"` or a `;`, which closes the script's own quote, and --cookie's
    ("Bash", {"command": "ssh h 'docker exec db sh -c \"grep -q '\\''DB_PASSWORD='\\''\" && echo ok'"},
     "Bash: ssh", None),
    ("Bash", {"command": "ssh h 'grep -c '\\''^TOKEN='\\''; echo $?'"},
     "Bash: ssh", None),
    ("Bash", {"command": "ssh h 'grep -rn -e '\\''--cookie '\\'' scripts/ && make X=1'"},
     "Bash: ssh", None),
    # the idiom written `'"'"'` in a user's password, a proxy's, curl's cookie, a key's value and a
    # file's name
    ("Bash", {"command": "ssh h 'curl -u '\"'\"'admin:S3cret!pass'\"'\"' https://x.org'"},
     "Bash: ssh", "S3cret"),
    ("Bash", {"command": "ssh h 'curl -x '\"'\"'bob:S3cret@proxy:8080'\"'\"' https://x.org'"},
     "Bash: ssh", "S3cret"),
    ("Bash", {"command": "ssh h 'curl --cookie '\"'\"'sid=abc123xyz'\"'\"' https://x.org'"},
     "Bash: ssh", "abc123xyz"),
    ("Bash", {"command": "ssh h 'echo {\"password\": '\"'\"'S3cret!pass'\"'\"'}'"},
     "Bash: ssh", "S3cret"),
    ("Bash", {"command": "ssh h 'grep -rn SECRET_KEY '\"'\"'settings.py'\"'\"''"},
     "Bash: ssh", None),
    # a bare number and a boolean after the quote that
    # closes its name's, shown as after a colon; a value read in quotes ending with -u attached to its quote; a line
    # with another before it inside the quote an earlier line left open; the idiom inside a double-quoted script
    ("Bash", {"command": 'echo "max_tokens: "4096'}, "Bash: echo", None),
    ("Bash", {"command": 'echo "use_token: "true && echo "done"'}, "Bash: echo", None),
    ("Bash", {"command": "bash -c \"grep 'token: ' f; curl -u'admin:S3cret!pass' https://x.org\""},
     "Bash: bash", "S3cret"),
    ("Bash", {"command": "ssh h '\ncd /app\nread -p \"Enter password: \" PW && echo \"done\"\n'"},
     "Bash: ssh", None),
    ("Bash", {"command": "ssh h \"echo password: '\\''hunter2x'\\''\""},
     "Bash: ssh", "hunter2x"),
    # a value in the idiom beginning with a `"` or one of `;&|()<>`, read where no
    # blank comes before the idiom's closing; pin: a grep's pattern closed by `'\''` and a `;`, the next idiom's value
    # masked by its own name
    ("Bash", {"command": "ssh h 'export DB_PASSWORD='\\''(S3cret!pass'\\'' && ./run'"},
     "Bash: ssh", "S3cret"),
    ("Bash", {"command": "ssh db 'mysql --password '\\''&S3cret!pass'\\'' -e status'"},
     "Bash: ssh", "S3cret"),
    ("Bash", {"command": "ssh h 'aws configure set aws_secret_access_key '\\''|a8f3c2d9e1b7S3cretKey'\\'''"},
     "Bash: ssh", "a8f3c2d9"),
    ("Bash", {"command": "ssh prod 'export DB_PASSWORD='\"'\"'&S3cret!pass'\"'\"' && ./run'"},
     "Bash: ssh", "S3cret"),
    ("Bash", {"command": "ssh h 'echo {\"password\": '\\''<S3cret!pass'\\''}'"},
     "Bash: ssh", "S3cret"),
    ("Bash", {"command": "ssh h 'grep -q '\\''TOKEN='\\''; export DB_PASSWORD='\\''hunter2x'\\'''"},
     "Bash: ssh", "hunter2x"),
    # a comment's apostrophe, on a line of its own or after a command, opening no quote for the lines after;
    # pin: a `#` inside a word, which begins no comment
    ("Bash", {"command": "# don't\ncd /app\nmake build\ngrep 'token: ' . | grep -v 'test'"},
     "Bash: cd", None),
    ("Bash", {"command": "# Here's the request\nhttp x.org X-API-Key:'abc123 def456'"},
     "Bash: http", "def456"),
    ("Bash", {"command": "# Post as the bot (it's allowed)\n"
                         "http POST x.org Authorization:'Bot MTIzNDU2Nzg5.GhIjKl.abcdef123456'"},
     "Bash: http", "abcdef123456"),
    ("Bash", {"command": "echo hi # don't\ngrep 'token: ' . | grep -v 'test'"},
     "Bash: echo", None),
    ("Bash", {"command": "echo a#b 'it'\ngrep 'token: ' . | grep -v 'test'"},
     "Bash: echo", None),
    # a value in quotes escaped inside a double-quoted script, after NAME=, --name=, --name
    ("Bash", {"command": 'bash -c "export DB_PASSWORD=\\"S3cretPass9\\" && ./run"'},
     "Bash: bash", "S3cretPass9"),
    ("Bash", {"command": 'bash -c "mysql --password=\\"S3cretPass9\\" -e status"'},
     "Bash: bash", "S3cretPass9"),
    ("Bash", {"command": 'bash -c "mysql --password \\"S3cretPass9\\" -e status"'},
     "Bash: bash", "S3cretPass9"),
    ("Bash", {"command": 'sh -c "PGPASSWORD=\\"S3cretPass9\\" psql"'}, "Bash: sh",
     "S3cretPass9"),
    # a header's value ending with what reads as NAME=, nothing after its closing quote; a snake_case
    # name's at a value's end
    ("Bash", {"command": 'http x.org X-Api-Key:"Q8xPwD2k9fJ1mN7aZ3s="'}, "Bash: http",
     "Q8xPwD2k"),
    ("Bash", {"command": 'http x.org Cookie:"sid=abc123def; csrf_token="'}, "Bash: http",
     "abc123def"),
    ("Bash", {"command": "node -e \"\nconsole.log('token: ', t); fetch(u, {session_cookie: 'S3cretPass5'})\n\""},
     "Bash: node", "S3cretPass5"),
    ("Bash", {"command": "bash -c \"\ngrep 'token: ' f; echo proxy_authorization: 'S3cretPass7'\n\""},
     "Bash: bash", "S3cretPass7"),
    # -u's user before its quote, -x, --proxy, --oauth2-bearer and --user= at a value's end, -x
    # attached to its quote; a number after a quote closing its name's before a `;`; the word's rest ending at `>` and
    # at `<`
    ("Bash", {"command": "bash -c \"grep 'token: ' f; curl -u admin:'S3cretPass1' x.org\""},
     "Bash: bash", "S3cretPass1"),
    ("Bash", {"command": "bash -c \"grep 'token: ' f; curl -x 'bob:S3cret@proxy:8080' x.org\""},
     "Bash: bash", "S3cret"),
    ("Bash", {"command": "bash -c \"grep 'token: ' f; curl --proxy 'bob:S3cret@proxy:8080' x.org\""},
     "Bash: bash", "S3cret"),
    ("Bash", {"command": "bash -c \"grep 'token: ' f; curl --oauth2-bearer 'a8f3c2d9e1b7S3cretKey' x.org\""},
     "Bash: bash", "a8f3c2d9"),
    ("Bash", {"command": "bash -c \"grep 'token: ' f; curl --user='admin:S3cretPass1' x.org\""},
     "Bash: bash", "S3cretPass1"),
    ("Bash", {"command": "bash -c \"grep 'token: ' f; curl -x'bob:S3cret@proxy:8080' x.org\""},
     "Bash: bash", "S3cret"),
    ("Bash", {"command": 'echo "max_tokens: "4096; echo done'}, "Bash: echo", None),
    ("Bash", {"command": 'echo "password: "$PW>out.txt'}, "Bash: echo", None),
    ("Bash", {"command": 'echo "token: "$T<in.txt'}, "Bash: echo", None),
], ids=["read", "edit", "write", "grep", "glob", "webfetch", "webfetch-path", "websearch", "agent", "other", "bash",
        "bash-lines", "bash-secret", "bash-env", "bash-header", "bash-dsn", "bash-aws", "bash-heredoc", "bash-url",
        "bash-user", "bash-flags", "bash-heredoc-flag", "bash-herestring", "bash-url-query", "bash-url-token",
        "bash-cookie", "bash-user-attached", "bash-user-empty", "bash-env-dash", "bash-json", "bash-openssl",
        "bash-commit-idiom", "bash-s3-path", "bash-identifier-path", "bash-branch", "bash-heredoc-backslash",
        "bash-heredoc-spaced", "bash-heredoc-word", "bash-user-quoted", "bash-user-spaced", "bash-json-escaped",
        "bash-header-key", "bash-header-key-js", "bash-header-named", "bash-heredoc-after-value",
        "bash-identifier-quoted-credential", "bash-s3-dated", "bash-uuid-path", "bash-commit-path",
        "bash-hex-token-path", "bash-mixed-token-path", "bash-token-20", "bash-token-19", "bash-hex-31",
        "bash-identifier-quoted-bang", "bash-identifier-quoted-short", "bash-header-httpie", "bash-small-token-path",
        "bash-underscored-file", "bash-tokens-word", "bash-tokens-number", "bash-header-private", "bash-hex-64",
        "bash-shift", "bash-header-rapidapi", "bash-header-rapidapi-httpie", "bash-header-cmc", "bash-header-pinata",
        "bash-header-authtoken", "bash-config-password", "bash-token-underscores", "bash-token-dashes", "bash-cuid",
        "bash-file-of-a-word", "bash-quoted-file", "bash-quoted-path-digit", "bash-aws-documented",
        "bash-sql-password", "bash-requirepass", "bash-shift-then-heredoc", "bash-quoted-parens-heredoc",
        "bash-shift-in-quotes", "bash-capitals-token", "bash-small-31", "bash-url-pipe", "bash-url-and",
        "bash-identifier-quoted", "bash-model-path", "bash-model-path-instruct", "bash-ulid", "bash-capitals-31",
        "bash-flag-after-word", "bash-flag-after-word-k", "bash-flag-after-word-rg", "bash-sql-password-dotted",
        "bash-requirepass-dotted", "bash-requirepass-file-shaped", "bash-requirepass-slashes", "bash-escaped-quotes-heredoc",
        "bash-escaped-apostrophe-heredoc", "bash-shift-in-single-quotes", "bash-header-auth-key", "bash-header-apim-key",
        "bash-words-key", "bash-header-long-number", "bash-json-long-number", "bash-json-number-7",
        "bash-header-httpie-number", "bash-header-httpie-long-number", "bash-aws-short-three-kinds",
        "bash-token-parts-of-five", "bash-model-path-parts-of-four", "bash-backslash-in-single-quotes",
        "bash-backslashes-in-single-quotes", "bash-escaped-backslash-in-double-quotes", "bash-key-header-in-object",
        "bash-key-header-in-json", "bash-key-not-at-the-end", "bash-file-with-digit-yaml", "bash-file-with-digit-py",
        "bash-file-with-digit-quoted", "bash-unknown-extension", "bash-unknown-extension-sql", "bash-one-dash-flag",
        "bash-header-ending-authorization", "bash-header-ending-authorization-httpie",
        "bash-header-ending-authorization-bare", "bash-header-ending-cookie", "bash-ansi-c-quote-heredoc",
        "bash-two-heredocs", "bash-file-of-three-kinds", "bash-key-not-at-a-key-s-end", "bash-quote-across-lines-heredoc",
        "bash-quote-across-lines-shift", "bash-snake-cookie", "bash-snake-authorization", "bash-cookie-flag",
        "bash-cookie-b", "bash-branch-b", "bash-oauth2-bearer", "bash-proxy-user", "bash-header-escaped-spaces",
        "bash-two-heredocs-one-delimiter", "bash-ansi-c-escaped-backslash", "bash-heredoc-in-a-quoted-script",
        "bash-heredoc-in-a-double-quoted-script", "bash-heredoc-in-a-quoted-script-s-middle", "bash-proxy-user-short",
        "bash-proxy-user-quoted", "bash-quotes-in-a-substitution-in-quotes", "bash-quotes-in-backticks-in-quotes",
        "bash-comment-apostrophe", "bash-hash-in-a-word",
        "bash-commit-idiom-then-heredoc", "bash-shift-nested-parens", "bash-quoted-heredoc-word-then-its-line",
        "bash-commit-idiom-then-a-quote", "bash-subshells-in-double-parens", "bash-substitution-of-a-subshell",
        "bash-regex-groups", "bash-quoted-word-closed-before-its-line",
        "bash-quoted-word-closed-before-its-line-single", "bash-quoted-word-closed-before-its-line-double",
        "bash-header-attached-authorization",
        "bash-header-attached-cookie", "bash-snake-cookie-credential", "bash-snake-authorization-credential",
        "bash-b-outside-curl", "bash-cookie-b-attached", "bash-cookie-b-attached-quoted", "bash-cookie-b-bare",
        "bash-proxy-user-short-quoted", "bash-proxy-in-x", "bash-header-name-in-sed", "bash-header-name-in-grep",
        "bash-comment-after-semicolon", "bash-quoted-word-then-its-line-at-once",
        "bash-substitution-of-a-subshell-in-quotes", "bash-quoted-word-on-a-read-line-after-its-quote",
        "bash-cookie-b-file", "bash-quoted-script-apostrophe-after", "bash-quoted-script-apostrophe-before",
        "bash-quoted-script-apostrophe-double", "bash-cookie-b-continuation-line", "bash-cookie-b-after-and",
        "bash-snake-boolean-json", "bash-snake-boolean-python", "bash-camel-header-boolean", "bash-proxy-in-x-quoted",
        "bash-proxy-equals-quoted", "bash-proxy-bare", "bash-snake-cookie-credential-quoted",
        "bash-shift-after-a-letter", "bash-quoted-word-before-another-quote", "bash-quote-going-on-in-none",
        "bash-quoted-script-delimiter-quoted", "bash-quoted-script-delimiter-quoted-double",
        "bash-quoted-script-word-goes-on-in-an-escaped-quote", "bash-quoted-script-word-goes-on-in-a-variable",
        "bash-quoted-script-word-goes-on-in-braces", "bash-quoted-script-word-goes-on-in-a-substitution",
        "bash-quoted-script-word-goes-on-in-arithmetic", "bash-quoted-header-boolean-comma",
        "bash-b-where-a-message-names-curl", "bash-x-outside-curl", "bash-quoted-script-goes-on-in-ansi",
        "bash-quoted-script-goes-on-in-escaped-double", "bash-double-quoted-heredoc-into-single",
        "bash-quote-going-on-to-the-line-s-end", "bash-quoted-script-word-ends-at-a-blank", "bash-snake-false-json",
        "bash-snake-none-python", "bash-camel-header-null", "bash-proxy-in-x-double-quoted",
        "bash-snake-cookie-credential-single-quoted", "bash-snake-key-any-value", "bash-cookie-b-curl-on-a-later-line",
        "bash-ansi-c-script-heredoc", "bash-double-quoted-script-escaped-delimiter",
        "bash-quoted-script-word-goes-on-in-a-tilde", "bash-snake-cookie-credential-no-space",
        "bash-header-number-then-a-credential", "bash-quoted-header-boolean-brace", "bash-proxy-outside-curl",
        "bash-comment-apostrophe-and-a-body-apostrophe", "bash-ansi-c-quote-and-a-body-apostrophe",
        "bash-commit-idiom-then-a-body-quote", "bash-key-boolean-semicolon", "bash-ansi-c-script-delimiter-hex",
        "bash-ansi-c-script-delimiter-octal", "bash-ansi-c-script-delimiter-escape", "bash-proxy-in-x-curl-exe",
        "bash-proxy-in-x-curlie", "bash-cookie-b-curl-exe", "bash-cookie-b-curlie", "bash-x-outside-curl-bare",
        "bash-header-value-in-the-other-quote", "bash-snake-header-value-in-the-other-quote",
        "bash-header-value-in-the-other-quote-single", "bash-header-number-in-the-other-quote",
        "bash-quoted-script-word-goes-on-in-a-letter", "bash-quoted-heredoc-word-ends-at-a-semicolon",
        "bash-ansi-c-script-delimiter-hex-double-quote", "bash-quoted-header-boolean-semicolon",
        "bash-quoted-header-boolean-bracket", "bash-header-httpie-boolean-comma", "bash-header-value-after-a-blank-and-colon",
        "bash-header-value-after-a-blank-and-colon-single", "bash-header-value-in-escaped-quotes",
        "bash-header-key-value-in-escaped-quotes", "bash-snake-value-in-escaped-quotes",
        "bash-name-before-its-quote-s-end", "bash-name-before-its-quote-s-end-double",
        "bash-name-before-its-quote-s-end-then-a-command", "bash-name-before-its-quote-s-end-no-blank",
        "bash-name-among-its-quote-s-words", "bash-value-after-a-name-before-its-quote-s-end",
        "bash-header-value-in-the-other-quote-after-blanks", "bash-header-empty-value-in-the-other-quote",
        "bash-header-name-after-a-slash-in-the-other-quote", "bash-cookie-b-curl-exe-capitals",
        "bash-quoted-script-second-dash-quoted-delimiter", "bash-quoted-heredoc-word-ends-at-a-pipe",
        "bash-quoted-heredoc-word-ends-at-an-ampersand", "bash-quoted-heredoc-word-ends-at-a-less-than",
        "bash-quoted-heredoc-word-ends-at-a-greater-than", "bash-header-httpie-boolean-semicolon",
        "bash-header-httpie-boolean-bracket", "bash-ansi-c-script-delimiter-u-double-quote",
        "bash-name-before-its-ansi-c-quote-s-end", "bash-name-after-an-escaped-quote",
        "bash-header-value-after-a-backslash-in-single-quotes",
        "bash-header-value-with-blanks-after-a-backslash-in-single-quotes",
        "bash-header-value-with-blanks-in-escaped-quotes", "bash-header-value-in-quotes-escaped-in-another-s",
        "bash-header-key-in-escaped-quotes-in-a-script", "bash-header-bearer-in-escaped-quotes-in-a-script",
        "bash-header-name-in-escaped-quotes-in-a-script", "bash-header-escaped-value-then-an-apostrophe",
        "bash-header-escaped-value-then-an-apostrophe-printf", "bash-header-escaped-value-to-the-line-s-end",
        "bash-header-value-holding-an-apostrophe", "bash-header-name-after-a-slash-sed",
        "bash-header-value-on-a-line-begun-in-a-quote", "bash-header-value-after-an-escaped-quote-s-value",
        "bash-name-before-its-quote-s-end-after-an-apostrophe", "bash-cookie-b-curl-exe-mixed-case",
        "bash-header-value-in-the-quote-idiom", "bash-header-value-in-the-double-quote-idiom",
        "bash-header-value-bare-after-an-escaped-quote-in-a-script",
        "bash-header-value-in-the-quote-idiom-on-a-line-begun-in-a-quote",
        "bash-header-name-in-the-quote-idiom-closing-it", "bash-header-empty-value-in-the-quote-idiom",
        "bash-header-number-in-the-quote-idiom", "bash-header-value-in-the-quote-idiom-in-double-quotes",
        "bash-header-value-in-the-quote-idiom-in-no-quote",
        "bash-double-quoted-script-escaped-delimiter-going-on-past-its-quote",
        "bash-name-before-its-quote-s-end-in-a-subshell", "bash-value-after-a-name-before-its-quote-s-end-in-a-subshell",
        "bash-name-before-its-quote-s-end-in-a-substitution", "bash-name-before-its-quote-s-end-in-a-double-quoted-script",
        "bash-name-before-its-quote-s-end-after-a-quoted-script", "bash-header-name-s-quote-inside-single-quotes",
        "bash-env-value-in-the-quote-idiom", "bash-flag-value-in-the-quote-idiom", "bash-user-password-in-the-quote-idiom",
        "bash-identifier-value-in-the-quote-idiom", "bash-env-value-in-the-double-quote-idiom",
        "bash-header-value-in-a-double-quoted-apostrophe", "bash-header-apostrophe-in-escaped-quotes-in-a-script",
        "bash-header-boolean-in-the-quote-idiom", "bash-name-before-its-quote-s-end-after-a-substitution-in-it",
        "bash-header-value-in-a-double-quote-inside-single-quotes", "bash-flag-spaced-value-in-the-quote-idiom",
        "bash-value-before-a-declined-quoted-header",
        "bash-env-name-in-the-quote-idiom-closing-it",
        "bash-env-name-in-the-quote-idiom-closing-it-pattern",
        "bash-header-value-in-prose-on-a-line-begun-in-a-double-quote",
        "bash-header-value-in-prose-on-a-line-begun-in-a-double-quote-tag",
        "bash-header-value-in-prose-on-a-line-begun-in-a-single-quote",
        "bash-header-value-after-a-multi-line-substitution-in-quotes",
        "bash-name-before-its-quote-s-end-after-a-multi-line-substitution-in-quotes",
        "bash-name-before-its-quote-s-end-after-a-quoted-script-in-a-substitution-in-quotes",
        "bash-user-password-quoted-in-the-quote-idiom",
        "bash-proxy-password-in-the-quote-idiom",
        "bash-proxy-user-in-the-quote-idiom",
        "bash-cookie-in-the-quote-idiom",
        "bash-key-value-in-the-quote-idiom",
        "bash-file-named-in-the-quote-idiom",
        "bash-header-after-a-value-with-an-apostrophe",
        "bash-name-before-its-quote-s-end-in-a-single-quoted-script-masked-across",
        "bash-name-before-its-quote-s-end-in-backticks",
        "bash-env-empty-value-in-the-quote-idiom",
        "bash-value-after-the-quote-closing-its-name-s-the-word-going-on",
        "bash-value-after-the-quote-closing-its-name-s-on-a-line-begun-in-a-double-quote",
        "bash-value-after-the-quote-closing-its-name-s-on-a-line-begun-after-a-closed-quote",
        "bash-quote-closing-its-name-s-before-a-parenthesis",
        "bash-line-after-the-line-closing-a-quote",
        "bash-line-begun-in-a-double-quote-opened-after-a-substitution-on-the-line-closing-a-quote",
        "bash-name-in-a-double-quote-before-a-declined-header-in-single-quotes",
        "bash-flag-path-value-in-the-quote-idiom",
        "bash-value-after-the-quote-closing-its-name-s-a-symbol-first",
        "bash-value-in-quotes-ending-with-a-name-in-a-double-quoted-script",
        "bash-value-in-quotes-ending-with-a-name-in-a-multi-line-double-quoted-script",
        "bash-value-in-quotes-ending-with-a-header-name",
        "bash-value-in-quotes-ending-with-a-word-naming-no-credential",
        "bash-value-in-quotes-ending-with-a-blank-after-a-name-inside-it",
        "bash-word-after-the-quote-closing-its-name-s-then-an-export",
        "bash-word-after-the-quote-closing-its-name-s-then-a-key",
        "bash-word-after-the-quote-closing-its-name-s-then-a-command",
        "bash-word-after-the-quote-closing-its-name-s-then-a-flag",
        "bash-word-after-the-quote-closing-its-name-s-then-a-user",
        "bash-value-in-quotes-ending-with-an-export-s-name",
        "bash-value-in-quotes-ending-with-an-export-s-name-in-a-single-quoted-script",
        "bash-value-in-quotes-ending-with-an-argument-s-name",
        "bash-value-in-quotes-ending-with-a-flag-s-name",
        "bash-value-in-quotes-ending-with-a-cookie-s-flag",
        "bash-value-in-quotes-ending-with-an-identifier",
        "bash-value-in-quotes-ending-with-a-user-s-flag",
        "bash-value-in-quotes-with-a-name-inside-it-not-at-its-end",
        "bash-value-in-quotes-ending-with-a-key-s-name",
        "bash-line-begun-where-one-line-s-reading-closed-the-quote",
        "bash-substitution-in-double-quotes-multi-line-read-as-one-line",
        "bash-substitution-in-double-quotes-on-one-line",
        "bash-env-name-in-the-quote-idiom-before-a-double-quote",
        "bash-env-name-in-the-quote-idiom-before-a-semicolon",
        "bash-cookie-flag-in-the-quote-idiom-closing-it",
        "bash-user-password-quoted-in-the-double-quote-idiom",
        "bash-proxy-password-in-the-double-quote-idiom",
        "bash-cookie-in-the-double-quote-idiom",
        "bash-key-value-in-the-double-quote-idiom",
        "bash-file-named-in-the-double-quote-idiom",
        "bash-number-after-the-quote-closing-its-name-s",
        "bash-boolean-after-the-quote-closing-its-name-s",
        "bash-value-in-quotes-ending-with-a-user-s-flag-attached",
        "bash-line-begun-in-a-quote-after-another-inside-it",
        "bash-value-in-the-quote-idiom-in-a-double-quoted-script",
        "bash-env-value-in-the-quote-idiom-beginning-with-a-parenthesis",
        "bash-flag-value-in-the-quote-idiom-beginning-with-an-ampersand",
        "bash-identifier-value-in-the-quote-idiom-beginning-with-a-pipe",
        "bash-env-value-in-the-double-quote-idiom-beginning-with-an-ampersand",
        "bash-key-value-in-the-quote-idiom-beginning-with-a-less-than",
        "bash-pattern-in-the-quote-idiom-closed-before-a-semicolon",
        "bash-comment-line-with-an-apostrophe-before-the-grep-pair",
        "bash-comment-line-with-an-apostrophe-before-a-header",
        "bash-comment-line-with-an-apostrophe-before-a-bot-token",
        "bash-comment-after-a-command-with-an-apostrophe",
        "bash-hash-inside-a-word-begins-no-comment",
        "bash-env-value-in-escaped-quotes-in-a-script",
        "bash-flag-equals-value-in-escaped-quotes-in-a-script",
        "bash-flag-spaced-value-in-escaped-quotes-in-a-script",
        "bash-env-prefix-value-in-escaped-quotes-in-a-script",
        "bash-header-value-ending-with-what-reads-as-a-name-and-equals",
        "bash-cookie-value-ending-with-a-name-and-equals",
        "bash-value-in-quotes-ending-with-a-snake-case-key",
        "bash-value-in-quotes-ending-with-a-snake-case-header",
        "bash-value-in-quotes-ending-with-a-user-before-its-quote",
        "bash-value-in-quotes-ending-with-a-proxy-flag",
        "bash-value-in-quotes-ending-with-a-proxy-long-flag",
        "bash-value-in-quotes-ending-with-a-bearer-flag",
        "bash-value-in-quotes-ending-with-a-user-equals",
        "bash-value-in-quotes-ending-with-a-proxy-flag-attached",
        "bash-number-after-the-quote-closing-its-name-s-before-a-semicolon",
        "bash-word-after-the-quote-closing-its-name-s-before-a-redirection",
        "bash-word-after-the-quote-closing-its-name-s-before-an-input"])
def test_a_step_says_what_the_call_acts_on_and_never_its_content(client, owner, served, capsys, tmp_path, monkeypatch,
                                                                 spawned, tool, tool_input, line, never):
    """A call's step is its tool's name and its target alone: a file tool's path, an address's scheme and host, a
    command's program; never an argument of it, a pattern, a query or a description. It replaced the command as written
    with each credential in it masked, whose masks' worst misses came from a mask's reading the fix before had moved;
    the rows here are their cases, each command's `never` still shown nowhere. A row with nothing to hide has no
    `never`."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _tool_use("a1", tool, tool_input)])
    assert _live(monkeypatch, capsys, mac, _call(path, tool, tool_input)) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    shown = _shown(client, owner, "mac")
    assert shown[-1] == f"[step] {line}", shown
    assert never is None or not any(never in s for s in shown), shown


@pytest.mark.parametrize("command,line,never", [
    ('git commit -m "Fix the login"', "Bash: git", "login"),
    ('FOO="a b" BAR=y ./run --pw hunter2x', "Bash: ./run", "hunter2x"),
    ("PASSWORD=hunter2x", "Bash", "hunter2x"),
    ("a=1 PASSWORD=hunter2x", "Bash", "hunter2x"),
    ("DB_PASSWORD='hunter2x' psql -h db", "Bash: psql", "hunter2x"),
    ("X=$(cat ~/.token) curl x.org", "Bash", "token"),
    ("n=\"$(grep -c 'password: ' a.yml)\"", "Bash", "password"),
    ("# deploy with the key\n\ngit push", "Bash: git", "deploy"),
    ("#!/bin/bash\nset -e", "Bash: set", "/bin"),
    ("( cd /app && make )", "Bash: cd", "make"),
    ("{ echo hunter2x; }", "Bash: echo", "hunter2x"),
    ("! grep -q hunter2x f", "Bash: grep", "hunter2x"),
    ("$EDITOR notes.md", "Bash", "notes"),
    ('"/opt/my tools/x" hunter2x', "Bash", "hunter2x"),
    ("`which python` -V", "Bash", "which"),
    ("\\rm -rf hunter2x", "Bash", "hunter2x"),
    ("cd /app&&make", "Bash: cd", "make"),
    ("echo>out.txt hunter2x", "Bash: echo", "hunter2x"),
    ("git;rm -rf /", "Bash: git", "rm -rf"),
    ("cat <<EOF\nhunter2x\nEOF", "Bash: cat", "hunter2x"),
    ("~/proofpane-wt/oe-venv/bin/python -I x.py", "Bash: ~/proofpane-wt/oe-venv/bin/python", "x.py"),
    ("A=x\\ hunter2x cmd", "Bash", "hunter2x"),
    ('A="x\\" hunter2x ok" cmd', "Bash", "hunter2x"),
    ("A=$'x\\' hunter2x ok' cmd", "Bash", "hunter2x"),
    ("A=${B:-x hunter2x} cmd", "Bash", "hunter2x"),
    ("A=`echo hunter2x ok` cmd", "Bash", "hunter2x"),
    ("A=x" + chr(0x0b) + "hunter2x cmd", "Bash: cmd", "hunter2x"),
    ("A=x" + chr(0xa0) + "hunter2x cmd", "Bash: cmd", "hunter2x"),
    ("# note" + chr(0x85) + "hunter2x rest", "Bash", "hunter2x"),
    ("# note" + chr(13) + "hunter2x rest", "Bash", "hunter2x"),
    ("# note" + chr(0x2028) + "hunter2x rest", "Bash", "hunter2x"),
    (chr(0x0b) + "A=x hunter2x", "Bash", "hunter2x"),
    ("2>/dev/null make", "Bash", None),
    ("A=x" + chr(0) + " hunter2x", "Bash", "hunter2x"),
    ("cmd" + chr(0) + "hunter2x arg", "Bash", "hunter2x"),
    ("{mysql,-uroot,-phunter2x,app}", "Bash", "hunter2x"),
    ("mysql{,-phunter2x}", "Bash", "hunter2x"),
    ("X=1 {psql,-hdb,hunter2x}", "Bash", "hunter2x"),
    ("{A=1 hunter2x", "Bash", "hunter2x"),
    ("!A=1 hunter2x", "Bash", "hunter2x"),
    ("((count++))", "Bash", "count"),
    ("(( 0x1234abcd5678 ))", "Bash", "0x1234"),
    ("a[k hunter2x]=1 cmd", "Bash", "hunter2x"),
    ("{fd}>log cmd", "Bash", "fd"),
    ("ls" + chr(0x2028) + "hunter2x", "Bash", "hunter2x"),
    ("-qzx hunter2x", "Bash", "qzx"),
    ("%qzx hunter2x", "Bash", "qzx"),
    ("~qzuser/bin/qzx hunter2x", "Bash", "qzx"),
    ("qz(x hunter2x", "Bash", "qz"),
    ("(qzx hunter2x", "Bash", "qzx"),
    ("qz*x hunter2x", "Bash", "qz"),
    ("qzf(){ qzx; }; qzf", "Bash", "qzf"),
    ("qzx>qzout hunter2x", "Bash: qzx", "hunter2x"),
    ("~/bin/qzx hunter2x", "Bash: ~/bin/qzx", "hunter2x"),
    ("", "Bash", None),
    ("\n\n", "Bash", None),
], ids=["program", "variables-set-before", "a-variable-set-alone", "two-variables-set-alone", "a-quoted-value-before",
        "a-substitution-in-a-value-before", "a-substitution-set-alone", "a-comment-and-a-blank-line-first",
        "a-script-s-first-line", "a-subshell", "a-group", "a-negation", "a-variable-s-program", "a-quoted-program",
        "a-substitution-s-program", "an-escaped-program", "a-program-before-and", "a-program-before-a-redirection",
        "a-program-before-a-semicolon", "a-here-document", "a-path-s-program",
        "an-escaped-blank-in-a-value-before", "an-escaped-quote-in-a-value-before", "an-ansi-c-quoted-value-before",
        "an-expansion-with-a-blank-before", "a-backquoted-value-before", "a-vertical-tab-in-a-value-before",
        "a-no-break-space-in-a-value-before", "a-comment-past-a-next-line", "a-comment-past-a-carriage-return",
        "a-comment-past-a-line-separator", "a-vertical-tab-before-a-variable-set",
        "a-number-s-redirection-before", "a-nul-before-a-word", "a-nul-inside-the-program",
        "a-brace-expanded-program", "a-brace-after-the-program", "a-brace-expanded-program-after-a-variable",
        "a-brace-glued-to-a-variable-set", "a-bang-glued-to-a-variable-set", "arithmetic", "arithmetic-after-a-blank",
        "an-array-s-element-set",
        "a-named-descriptor-s-redirection", "a-line-separator-inside-the-program", "a-leading-dash", "a-leading-percent",
        "a-user-s-home", "a-parenthesis-inside-the-program", "an-unclosed-subshell", "a-glob-in-the-program",
        "a-function-defined-first", "a-redirection-after-the-program", "a-home-folder-s-program", "empty",
        "blank-lines"])
def test_a_command_s_line_is_its_program_alone(command, line, never):
    """A command's line is its program, the first word of its first line
    that is not a comment, past the variables set before it and an opening `(`, `{` or `!`; `Bash` alone where none
    can be told (a quoted program, a variable's, a substitution's, an escape's, a variable set with nothing after it,
    a file descriptor's number before a redirection, a value set before it that holds an escape, a `$` or a backquote,
    or a NUL character anywhere, which no shell is given), never a word of its arguments nor a value set before it.
    Its lines and words are bash's: a line ends at a newline alone, a word at a blank or a tab, so a character another
    reading ends either at never puts a value or a comment's word in the program's place."""
    got = ade_link._live_line("Bash", {"command": command})
    assert got == line, got
    assert never is None or never not in got, got


_SHELL_MISS = re.compile(r"^/bin/bash: (?:line \d+: )?(.*?): (command not found|No such file or directory|"
                         r"Not a directory)$")
_ZSH_PATH = re.compile(r"^\(eval\):\d+: (?:no such file or directory|not a directory): (.*)$")
_ZSH_HOOK = "command_not_found_handler() { print -rn -- \"RUN:$1\"$'\\0' >&2; return 127 }; "


def _shell_tried(shell, command, folder, files=()):
    """The names `shell` tried to run for `command`, in order, run as Claude Code runs a command (eval inside `-c`,
    bash's extglob off) in a folder of its own holding `files`, PATH an empty folder: bash's "command not found", zsh's
    command_not_found_handler, and either's no-such-file message for a name holding a `/` (one without is a
    redirection's file, not a run)."""
    empty = os.path.join(folder, "bin")
    os.makedirs(empty, exist_ok=True)
    work = tempfile.mkdtemp(dir=folder)
    for name in files:
        open(os.path.join(work, name), "w").close()
    env = {"HOME": os.path.expanduser("~"), "PATH": empty, "LANG": "en_US.UTF-8", "QZCMD": command}
    if shell == "bash":
        argv = ["/bin/bash", "--noprofile", "--norc", "-c", 'shopt -u extglob 2>/dev/null; eval "$QZCMD"; wait']
    else:
        argv = ["/bin/zsh", "-f", "-c", _ZSH_HOOK + 'eval "$QZCMD"; wait']
    err = subprocess.run(argv, cwd=work, env=env, capture_output=True, stdin=subprocess.DEVNULL,
                         timeout=20).stderr.decode("utf-8", "surrogateescape")
    names = []
    for record in err.split(chr(0)):
        if shell == "zsh" and "RUN:" in record:
            names.append(record[record.index("RUN:") + 4:])
        for line in record.split(chr(10)):
            m = _SHELL_MISS.match(line) if shell == "bash" else _ZSH_PATH.match(line)
            if m and (shell == "bash" and m.group(2) == "command not found" or "/" in m.group(1)):
                names.append(m.group(1))
    return names


@pytest.mark.skipif(not os.path.exists("/bin/bash"), reason="no /bin/bash to run the commands")
def test_a_command_s_program_is_the_one_bash_and_zsh_try_or_bash_alone(tmp_path):
    """Each printable ASCII character and each Unicode
    blank, at a made-up first word's start, inside it and at its end, with and without a variable set before it, run
    by /bin/bash and, where installed, /bin/zsh as Claude Code runs a command, in an empty folder and in one holding
    the made-up names as files (so a redirection from one opens): a command's line shows the first name each shell
    tried, the same in both shells and both folders (or none, in the empty one), or `Bash` alone. A job put in the
    background may report after the next, and so may a pipeline's first command, whose commands run at once: there the
    line's name need only be among those tried."""
    blanks = [chr(c) for c in range(0x110000) if chr(c).isspace() and not 0x20 <= c < 0x7f] + [chr(0x200b), chr(0xfeff)]
    chars = [chr(c) for c in range(0x20, 0x7f)] + blanks
    words = [w for c in chars for w in (c + "qzx", "qz" + c + "x", "qzx" + c)]
    commands = [pre + w + " qzarg" for w in words for pre in ("", "QZV=1 ")] + ["~/qzbin/qzx qzarg"]
    shells = ["bash"] + (["zsh"] if os.path.exists("/bin/zsh") else [])
    home = os.path.expanduser("~")
    misses = []
    for command in commands:
        ours = ade_link._live_program(command)
        if ours == "":
            continue
        named = home + ours[1:] if ours.startswith("~/") else ours
        for shell in shells:
            empty = _shell_tried(shell, command, str(tmp_path))
            full = _shell_tried(shell, command, str(tmp_path), ("qz", "qzx", "x", "qzarg"))
            background = "&" in command or "|" in command
            if not (full and (full[0] == named or background and named in full)
                    and (not empty or empty[0] == named or background)):
                misses.append((command, ours, shell, empty[:1], full[:1]))
    assert not misses, misses[:20]


_OLD_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.", re.ASCII)
_OLD_PEM = re.compile(r"-----BEGIN[^\n]*PRIVATE KEY", re.ASCII)


def test_the_two_shapes_found_in_one_pass_answer_as_their_old_patterns():
    """The JWT's and the PEM's shapes, found in one pass along a text, answer as
    the patterns they replace (`_OLD_JWT`, `_OLD_PEM`) over 30,000 texts drawn from the pieces each
    could begin, go on and end at (seeded), and over a JWT, a PEM and their near-misses; and each carries the pattern
    and flags it replaces, which test_ade_link_history.py's case holds to the server's list (link.SECRET_SHAPES)."""
    rnd = random.Random(23)
    pieces = ["eyJ", "-", ".", "a", "Z", "9", "_", " ", chr(10), "-----BEGIN", "PRIVATE KEY", "RSA ", "x", "eyJ",
              "aaaaaaaaaa", "eyJaaaaaaaaaa.", "."]
    texts = ["".join(rnd.choice(pieces) for _ in range(rnd.randrange(0, 30))) for _ in range(30000)]
    texts += ["eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIi.sig",
              "x-eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.", "aeyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.",
              "-----BEGIN RSA PRIVATE KEY-----", "-----BEGIN" + chr(10) + "PRIVATE KEY", "-----BEGINPRIVATE KEY",
              "-----BEGIN -----BEGIN PRIVATE KE", "PRIVATE KEY -----BEGIN"]
    jwt, pem = ade_link._SHAPE_JWT, ade_link._SHAPE_PEM
    assert jwt in ade_link._SECRET_SHAPES and pem in ade_link._SECRET_SHAPES
    assert (jwt.pattern, jwt.flags) == (_OLD_JWT.pattern, _OLD_JWT.flags)
    assert (pem.pattern, pem.flags) == (_OLD_PEM.pattern, _OLD_PEM.flags)
    wrong =[t for t in texts if bool(jwt.search(t)) != bool(_OLD_JWT.search(t))
             or bool(pem.search(t)) != bool(_OLD_PEM.search(t))]
    assert not wrong, wrong[:10]
    assert sum(bool(_OLD_JWT.search(t)) for t in texts) > 50 and sum(bool(_OLD_PEM.search(t)) for t in texts) > 50


def _too_long(signum, frame):
    raise TimeoutError("past a Stop's 30 seconds")


@pytest.mark.parametrize("piece", ["-eyJ", "-----BEGIN", "sk-", "eyJaaaaaaaaaa."])
def test_a_line_and_words_as_long_as_a_round_reads_take_well_under_a_stop_s_time(piece):
    """A text as long as a round reads of its transcript (MIRROR_WINDOW, 4 MiB), made of the
    piece a shape could begin at over and over, as a command, a path, a round's words and a Stop's message, takes
    well under a Stop's 30 seconds (the JWT's and the PEM's old patterns, which read on from each such
    place, took past 30)."""
    text = (piece * (ade_link.MIRROR_WINDOW // len(piece) + 1))[:ade_link.MIRROR_WINDOW]
    readers = [lambda: ade_link._live_line("Bash", {"command": text}), lambda: ade_link._live_line("Read", {"file_path": text}),
               lambda: ade_link._live_items([("u1", "session", text)], [], []), lambda: ade_link._mirror_parts(text)]
    old = signal.signal(signal.SIGALRM, _too_long)
    signal.alarm(30)
    try:
        begun = time.monotonic()
        for read in readers:
            read()
        assert time.monotonic() - begun < 15.0
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


@pytest.mark.parametrize("tool,tool_input,line", [
    ("Grep", {"pattern": "hunter2x"}, "Grep"),
    ("Grep", {"pattern": "hunter2x", "path": "src", "glob": "*.py"}, "Grep: src"),
    ("Glob", {"pattern": "**/hunter2x*", "path": "src"}, "Glob: src"),
    ("WebSearch", {"query": "hunter2x leak"}, "WebSearch"),
    ("Agent", {"subagent_type": "Explore", "description": "find hunter2x", "prompt": "hunter2x"}, "Agent"),
    ("Task", {"description": "find hunter2x", "prompt": "hunter2x"}, "Task"),
    ("NotebookEdit", {"notebook_path": "/work/n.ipynb", "new_source": "hunter2x"}, "NotebookEdit: /work/n.ipynb"),
    ("mcp__notes__search", {"query": "hunter2x"}, "mcp__notes__search"),
    ("WebFetch", {"url": "https://x.org/reset?token=hunter2x", "prompt": "hunter2x"}, "WebFetch: https://x.org"),
    ("mcp__notes__save", {"": "hunter2x"}, "mcp__notes__save"),
    ("WebFetch", {"url": "https://api.example.com" + chr(92) + "reset@hunter2x.example/x"}, "WebFetch"),
    ("WebFetch", {"url": "https://x.org" + chr(9) + "hunter2x.example/x"}, "WebFetch"),
    ("WebFetch", {"url": "https://admin:hunter2x@x.org/a"}, "WebFetch"),
    ("WebFetch", {"url": "https://X.org:8443/a?b=hunter2x"}, "WebFetch: https://x.org:8443"),
    ("Bash", {}, "Bash"),
    ("Bash", {"command": 5}, "Bash"),
], ids=["grep", "grep-path", "glob-path", "websearch", "agent", "task", "notebook", "mcp", "webfetch", "an-empty-key",
        "webfetch-a-backslash", "webfetch-a-tab", "webfetch-a-user", "webfetch-a-port",
        "bash-none", "bash-not-text"])
def test_a_call_s_line_is_its_tool_s_name_and_target_alone(tool, tool_input, line):
    """The same rule for every tool: a file tool's path, Grep's and Glob's `path`, an address's scheme and host; a
    pattern, a query, a description, a prompt or anything else it was given never, under any key, an empty one too."""
    got = ade_link._live_line(tool, tool_input)
    assert got == line and "hunter2x" not in got, got


def test_free_text_leaves_out_each_long_run():
    """The same rule's free text: each run of 16 or more printable ASCII characters with no blank among them is left
    out as `***`, a word of letters too (letters alone can be a password); a run
    of 15 stays, and words of another script are never such a run."""
    cjk = chr(0x7b2c) + "19" + chr(0x8f6e)
    text = (f"the key is Zx9Qp2Lm7Kd4Ws8Rt3Yv and the internationalization works; {cjk} done, (internationalization), "
            "well-intentioned-folks /Users/me/x/y/z.py abc123def456ghi abc123def456ghi7")
    assert ade_link._live_plain(text) == (f"the key is *** and the *** works; {cjk} done, *** *** *** "
                                          "abc123def456ghi ***")


@pytest.mark.parametrize("run,plain", [
    ("ZxQpLmKdWsRtYvBn", False),
    ("QWERTYUIOPASDFGHJ", False),
    ("wellIntentionedFolksHere", False),
    ("internationalization", True),
    ("Internationalization", True),
    ("Well-Intentioned-Folks", True),
    ("(Counterproductive),", True),
    ("don't-overthink-it", True),
    ("qwhzmrtkxbvnplsd", True),
    ("wxyz-abcd-efgh-ijkl", True),
], ids=["mixed-case", "capitals", "camel-case", "lowercase", "a-capital-first", "each-part-a-capital-first",
        "punctuation-at-its-ends", "an-apostrophe-inside",
        "an-app-password", "an-app-specific-password"])
def test_a_long_run_of_letters_is_left_out_as_any_other(run, plain):
    """Every run of 16 or more is left out, of letters too, in any case, a word or not (a Google app password is 16
    small letters, an Apple one four parts of four a `-` joins); `plain` is what an earlier reading took for a word,
    left out all the same."""
    assert ade_link._live_plain(f"see {run} here") == "see *** here"


def test_a_long_run_touching_chinese_characters_is_left_out_and_the_chinese_stays():
    """A long run of printable ASCII touching Chinese
    characters, no blank between, is left out as any other, the characters on either side staying; a run of 15 there
    stays; and a Chinese sentence, however long, holds no such run (no blank in it either)."""
    before = "".join(chr(c) for c in (0x5bc6, 0x94a5, 0x662f))
    after = "".join(chr(c) for c in (0x8bf7, 0x4fdd, 0x5b58))
    assert ade_link._live_plain(before + "sk-Zx9Qp2Lm7Kd4Ws8Rt3Yv" + after) == before + "***" + after
    assert ade_link._live_plain(before + "abc123def456ghi" + after) == before + "abc123def456ghi" + after
    sentence = "".join(chr(0x4e00 + n) for n in range(40))
    assert ade_link._live_plain(sentence) == sentence


def test_a_command_s_program_and_a_message_s_long_runs_take_time_in_proportion_to_their_length():
    """The rule's readers each read once along what they are given: a command or a message of hundreds of thousands
    of characters, in the shapes that make a reading go back over what it read, takes a fraction of a second, well
    inside a Stop's 30."""
    n = 200_000
    commands = ["A=" + "x" * n + ";", "A=1 " * n + "cmd", "#" + chr(10) * n + "cmd", "A='" + "x" * n,
                "A=" + "'a'" * n + ";", "A=" + "'" * (2 * n + 1), 'A="' + "x" * n, "x" * n + "=",
                "A=" + '"a"' * n + ";", "(" * n + "cmd"]
    for command in commands:
        begun = time.monotonic()
        ade_link._live_line("Bash", {"command": command})
        assert time.monotonic() - begun < 2.0, command[:24]
    texts = ["a-" * n, "a'" * n + "!", "Zx9Q" * n, ("abcd " * 3 + "x" * 16 + " ") * (n // 32), "(" * n,
             "a" + "'" * n, "(" * n + "a"]
    for text in texts:
        begun = time.monotonic()
        ade_link._live_plain(text)
        assert time.monotonic() - begun < 2.0, text[:24]


def test_a_round_s_words_leave_out_each_long_run_but_a_plain_word(client, owner, served, capsys, tmp_path,
                                                                  monkeypatch, spawned):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "the token is Zx9Qp2Lm7Kd4Ws8Rt3Yv please"),
                       _said("a1", _text("Saved to /Users/me/work/notes.md, internationalization done.")),
                       _bash("a2", "ls")])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] the token is *** please",
                                            "[session] Saved to *** *** done.", "[step] Bash: ls"]


def test_a_round_of_a_link_that_does_not_seal_carries_its_steps_and_no_words(client, owner, served, capsys, tmp_path,
                                                                             monkeypatch, spawned):
    """The same rule: where the link does not seal, a round carries no free text, only its calls' lines; the turn's
    Stop posts its words as the mirror does, as before the live view."""
    monkeypatch.setattr(ade_link, "_mirror_mode", lambda link: ("clear",))
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    turn = [_typed("u1", "how is the build?"), _said("a1", _text("Checking the tests.")), _bash("a2", "pytest -q")]
    _transcript(path, turn)
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "pytest -q"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[step] Bash: pytest"]
    _transcript(path, turn + [_said("a3", _text("12 passed."))])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] how is the build?", "[session] Checking the tests.",
                                            "[step] Bash: pytest", "[session] 12 passed."]


def test_a_round_of_a_link_that_does_not_seal_carries_no_count_of_its_words(client, owner, served, capsys,
                                                                           tmp_path, monkeypatch, spawned):
    """The same rule: a round where the link does not seal carries its calls' lines alone, so not its count of
    earlier messages either, which counts words no round there carries."""
    monkeypatch.setattr(ade_link, "_mirror_mode", lambda link: ("clear",))
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    turn = [_typed("u1", "go")]
    for n in range(15):                                     # 16 messages, past the most a round shows
        turn += [_said(f"a{n}", _text(f"Line {n}.")), _bash(f"c{n}", f"echo {n}")]
    _transcript(path, turn)
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "echo 14"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    shown = _shown(client, owner, "mac")
    assert shown and all(s.startswith("[step] ") for s in shown), shown


def test_a_step_comes_only_from_a_call_in_the_entries_its_round_judged(client, owner, served, capsys, tmp_path,
                                                                        monkeypatch, spawned):
    """The hook's own input never makes a step: a call reaches the timeline from the transcript the round judged, so
    one the transcript does not hold yet, as when a round read it just before the call was written, goes with the next
    round, judged with its entry."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go")])
    assert _live(monkeypatch, capsys, mac, _call(path, "Read", {"file_path": "/work/grove/plan.md"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go"]
    _transcript(path, [_typed("u1", "go"), _tool_use("a1", "Read", {"file_path": "/work/app.py"})])
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[step] Read: /work/app.py"]


def test_a_round_posts_one_event_its_words_and_calls_in_the_turn_s_order(client, owner, served, capsys, tmp_path,
                                                                          monkeypatch, spawned):
    """A round posts what it read as one note, mirror "live": the owner's words, the session's and its calls in the
    turn's order, so a busy turn costs the link one event a round."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _said("a1", _text("Reading.")), _bash("a2", "cat a.md"),
                       _said("a3", _text("Now the tests.")), _bash("a4", "pytest -q")])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "pytest -q"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    assert [e.get("mirror") for e in _events(client, owner, "mac")] == ["live"]
    assert _shown(client, owner, "mac") == ["[owner] go", "[session] Reading.", "[step] Bash: cat",
                                            "[session] Now the tests.", "[step] Bash: pytest"]


def test_a_message_of_a_round_shaped_like_a_secret_is_left_out_alone(client, owner, served, capsys, tmp_path,
                                                                     monkeypatch, spawned):
    """Each message of a round is checked for a secret's shape on its own, as a Stop's notes are: the one that holds it
    is left out, and the round's other words and calls still go (a check of the
    whole note alone kept the secret out but took the rest of the round with it)."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _said("a1", _text(f"the key is {KEY}")), _bash("a2", "ls"),
                       _said("a3", _text("Done."))])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", f"[session] {LEFT_OUT}", "[step] Bash: ls", "[session] Done."]
    assert not any(KEY[3:20] in e["text"] for e in _events(client, owner, "mac"))


def test_a_stop_posts_each_turn_whole_again_and_the_next_turn_s_rounds_start_after_it(client, owner, served, capsys,
                                                                                       tmp_path, monkeypatch, spawned):
    """A Stop posts each turn's words again as their own notes, as before the live view,
    with its calls in one note, whatever its rounds posted, so the cap deletes only rounds a Stop has posted again;
    the page shows a round's note until its turn's Stop is there. The next turn's rounds start after the Stop."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    turn = [_typed("u1", "go"), _said("a1", _text("Reading.")), _bash("a2", "cat a.md")]
    _transcript(path, turn)
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "cat a.md"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[session] Reading.", "[step] Bash: cat"]
    turn += [_said("a3", _text("Done."))]
    _transcript(path, turn)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert [e.get("mirror") for e in _events(client, owner, "mac")] == ["live", "owner", "session", "step", "session"]
    assert _shown(client, owner, "mac") == ["[owner] go", "[session] Reading.", "[step] Bash: cat",
                                            "[session] Done."]                  # each turn once
    turn += [_typed("u2", "and the tests?"), _said("a4", _text("Running them.")), _bash("a5", "pytest -q")]
    _transcript(path, turn)
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac")[4:] == ["[owner] and the tests?", "[session] Running them.",
                                                "[step] Bash: pytest"]          # the next turn alone


def test_a_stop_s_last_note_says_from_when_it_posted_its_turn_whole_and_its_rounds_go_with_it(
        client, owner, served, capsys, tmp_path, monkeypatch, spawned):
    """A Stop's last note says it posted its turn's words whole from the time of the first
    entry it read (whole_since); no other note of it says so, and no round does. The ADE then says the turn's rounds
    were posted again, and the turn shows once."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    turn = [_typed("u1", "go"), _said("a1", _text("Reading.")), _bash("a2", "cat a.md")]
    _transcript(path, turn)
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "cat a.md"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    _transcript(path, turn + [_said("a3", _text("Done."))])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[session] Reading.", "[step] Bash: cat",
                                            "[session] Done."]
    assert _whole(mac) == [("live", None), ("owner", None), ("session", None), ("step", None),
                           ("session", STAMPED_AT * 1000)]


def test_a_stop_that_leaves_words_out_says_nothing_of_whole_and_its_turn_s_rounds_stand(
        client, owner, served, capsys, tmp_path, monkeypatch, spawned):
    """A Stop that did not post its turn's words whole says nothing of whole, so the ADE says
    none of that turn's rounds was posted again, and they stand beside its notes, the only whole copy of its words: one
    that leaves its oldest messages out (more than MIRROR_NOTES), and one that cuts a message (longer than
    MIRROR_PARTS parts hold). A turn its Stop posted whole goes with it, as before."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    turn = [_typed("u1", "go"), _said("a1", _text("Reading."))]
    _transcript(path, turn)
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    turn += [_said("a2", _text("Done."))]
    _transcript(path, turn)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[session] Reading.\n\nDone."]     # posted whole: once
    for n in range(7):              # seven turns, the owner interrupting six: 14 messages, a round after each turn
        turn += [_typed(f"v{n}", f"try {n}"), _said(f"b{n}", _text(f"Tried {n}."))]
        _transcript(path, turn)
        assert _flush(capsys, mac, path) == (0, "", "")
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    long = "x" * (ade_link.MIRROR_PARTS * (ade_link.MIRROR_TEXT - 80) + 100)
    turn += [_typed("u3", "write it all"), _said("a3", _text(long))]
    _transcript(path, turn)
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    events = _events(client, owner, "mac")
    first_stop = next(e["seq"] for e in events if e.get("mirror") not in (None, "live"))
    later = [e for e in events if e.get("mirror") == "live" and e["seq"] > first_stop]
    assert later and not any(e.get("posted_again") for e in later), later
    assert [w for m, w in _whole(mac) if w is not None] == [STAMPED_AT * 1000]    # the first Stop's alone


def test_a_round_s_notes_break_where_a_turn_starts_so_a_stop_replaces_its_own_turn_alone(
        client, owner, served, capsys, tmp_path, monkeypatch, spawned):
    """A session's first Stop reads its last turn alone, so it replaces that turn's rounds and
    no earlier turn's. A round's notes break where a turn starts, so no note holds both the end of a turn the owner
    interrupted, whose Stop never ran, and the start of the next: each word shows once, none lost."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    turn = [_typed("u1", "go"), _said("a1", _text("Reading."))]
    _transcript(path, turn)
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    turn += [_said("a2", _text("Half done.")), _typed("u2", "stop that, do X"), _said("a3", _text("On X."))]
    _transcript(path, turn)
    assert _flush(capsys, mac, path) == (0, "", "")
    _transcript(path, turn + [_said("a4", _text("Done X."))])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[session] Reading.", "[session] Half done.",
                                            "[owner] stop that, do X", "[session] On X.\n\nDone X."]


def test_a_stop_of_entries_with_no_time_says_nothing_of_whole_and_its_rounds_stand(client, owner, served, capsys,
                                                                                    tmp_path, monkeypatch, spawned):
    """Where a transcript's entries carry no time, a Stop cannot say from when it posted its turn whole: it says
    nothing, and the turn's rounds stand beside its notes, shown twice and never lost."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    turn = [_typed("u1", "go"), _said("a1", _text("Reading."))]
    _bare_transcript(path, turn)
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    _bare_transcript(path, turn + [_said("a2", _text("Done."))])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[session] Reading.", "[owner] go",
                                            "[session] Reading.\n\nDone."]
    assert all(w is None for _, w in _whole(mac))


def test_a_stop_that_left_a_note_out_for_a_secret_s_shape_says_nothing_of_whole_and_its_rounds_stand(
        client, owner, served, capsys, tmp_path, monkeypatch, spawned):
    """A Stop's note of words before the last call holds every message there, and one holding
    a secret's shape leaves that whole note out, so a clean message beside it never goes with the Stop. Its round
    holds it, each message checked apart; the Stop says nothing of whole, and the round stands."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    turn = [_typed("u1", "go"), _said("a1", _text(f"the key is {KEY}")), _bash("a2", "ls"),
            _said("a3", _text("B: the clean words.")), _bash("a4", "pwd")]
    _transcript(path, turn)
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "pwd"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    _transcript(path, turn + [_said("a5", _text("Done."))])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    shown = _shown(client, owner, "mac")
    assert "[session] B: the clean words." in shown, shown
    assert all(w is None for _, w in _whole(mac))


def test_a_note_whose_cut_makes_a_secret_s_shape_in_a_part_does_not_go_whole():
    """A message cut into parts can end, where a part ends, a secret's shape the
    whole did not hold, and that part goes as the left-out sentence, so a Stop holding it does not say whole."""
    goes_whole = getattr(ade_link, "_mirror_goes_whole", lambda text: True)
    size = ade_link.MIRROR_TEXT - 80
    text = "y" * (size - 12) + " PP-ABCDEFGH" + "Z" * 10 + "y" * size        # a ticket only once the cut ends it
    assert LEFT_OUT in ade_link._mirror_parts(text)
    assert goes_whole("y" * (2 * size)) is True and goes_whole(text) is False
    # and a shape across a part's end: no part holds it, the whole does, and the Stop leaves the whole note out
    across = "y" * (size - 10) + " " + KEY + " " + "y" * size
    assert LEFT_OUT not in ade_link._mirror_parts(across) and ade_link._unsecret(across) == LEFT_OUT
    assert goes_whole(across) is False


def test_a_stop_says_its_turn_began_at_the_first_time_among_the_entries_it_read(client, owner, served, capsys,
                                                                                tmp_path, monkeypatch, spawned):
    """Claude Code writes some entries with no uuid (a file's history), some with no time; the
    time a Stop says its turn began is the first among the entries it read, by their place, never the time of whichever
    entry with no uuid came last."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")

    def at(n, e):
        return dict(e, timestamp=_stamp(n))

    turn = [at(0, _typed("u1", "go")), at(1, _said("a1", _text("Done.")))]
    _bare_transcript(path, turn)
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    turn += [{"type": "file-history-snapshot", "messageId": "m1", "snapshot": {}},          # no uuid, no time
             at(3, _typed("u2", "and the tests?")), at(4, _said("a2", _text("Running them.")))]
    _bare_transcript(path, turn)
    assert _flush(capsys, mac, path) == (0, "", "")
    turn += [at(9, {"type": "file-history-snapshot", "messageId": "m2", "snapshot": {}})]   # no uuid, a time
    _bare_transcript(path, turn)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[session] Done.", "[owner] and the tests?",
                                            "[session] Running them."]
    assert [w for _, w in _whole(mac) if w is not None] == [STAMPED_AT * 1000, (STAMPED_AT + 3) * 1000]


def test_a_round_s_notes_break_where_any_turn_starts_a_task_s_notification_too(client, owner, served, capsys,
                                                                                tmp_path, monkeypatch, spawned):
    """A turn a task's notification, a peer or a schedule starts begins with no words of the
    owner's, so a round's notes, and its run of the session's words, break where any turn starts, by the entries'
    place: a round across the owner's interruption and a notification's turn replaces nothing of the first."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    turn = [_typed("u1", "go")]
    _transcript(path, turn)
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    # words of two turns with no call between them, then calls of two turns with no words between them: each run
    # breaks where the next turn starts
    turn += [_said("a1", _text("Half done.")),
             _turn("n1", "<task-notification>a build ended</task-notification>", "task_notification"),
             _said("a2", _text("The build finished.")), _bash("c1", "ls"),
             _turn("n2", "<task-notification>a deploy ended</task-notification>", "task_notification"),
             _bash("c2", "pwd"), _said("a3", _text("Deployed."))]
    _transcript(path, turn)
    assert _flush(capsys, mac, path) == (0, "", "")
    _transcript(path, turn + [_said("a4", _text("All green."))])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[session] Half done.", "[session] The build finished.",
                                            "[step] Bash: ls", "[step] Bash: pwd",
                                            "[session] Deployed.\n\nAll green."]


def test_a_stop_says_nothing_of_whole_where_the_clock_went_back_before_its_turn(client, owner, served, capsys,
                                                                                tmp_path, monkeypatch, spawned):
    """A turn the owner interrupted, then one timed before it (this machine's clock set back):
    a session's first Stop reads its own turn alone, and would say it began before the interrupted turn's rounds were
    said. An entry before its start timed at or after that start, and it says nothing of whole; its rounds stand."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")

    def at(n, e):
        return dict(e, timestamp=_stamp(n))

    turn = [at(100, _typed("u1", "go")), at(101, _said("a1", _text("Half done.")))]
    _bare_transcript(path, turn)
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    turn += [at(95, _typed("u2", "do X instead")), at(96, _said("a2", _text("Doing X.")))]
    _bare_transcript(path, turn)
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    shown = _shown(client, owner, "mac")
    assert "[session] Half done." in shown, shown
    assert all(w is None for _, w in _whole(mac))


def test_a_stop_says_nothing_of_whole_where_its_transcript_is_ahead_of_this_machine_s_clock(
        client, owner, served, capsys, tmp_path, monkeypatch, spawned):
    """A Stop says when its turn began only where that time is not ahead of this machine's clock by more than a minute,
    as said_at goes (the ADE refuses a time in its future): here the clock is ten minutes behind the transcript."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _said("a1", _text("Done."))])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    monkeypatch.setattr(ade_link, "_mirror_now", lambda: STAMPED_AT - 600)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _whole(mac) and all(w is None for _, w in _whole(mac))


def test_a_round_s_count_of_earlier_messages_within_one_turn_keeps_its_note_whole(
        client, owner, served, capsys, tmp_path, monkeypatch, spawned):
    """A note broken after every count of earlier messages would pass the case below.
    Within one turn, the counts of earlier messages and steps and the twelve messages after them are one note."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    turn = [_typed("u1", "go")]
    _transcript(path, turn)
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    for n in range(15):                                     # 15 messages of the same turn in the next round
        turn += [_said(f"a{n}", _text(f"Line {n}.")), _bash(f"c{n}", f"echo {n}")]
    _transcript(path, turn)
    assert _flush(capsys, mac, path) == (0, "", "")
    shown = _shown(client, owner, "mac")
    assert "[mirror] 4 earlier messages of this session not mirrored" in shown, shown
    assert "[step] 3 earlier steps\nBash: echo" in shown, shown
    assert [e.get("mirror") for e in _events(client, owner, "mac")] == ["live", "live"]


def test_a_round_s_note_breaks_after_its_earlier_messages_count_where_that_crosses_a_turn_s_start(
        client, owner, served, capsys, tmp_path, monkeypatch, spawned):
    """A round of more than MIRROR_NOTES messages opens with one that counts the earlier;
    where those cross a turn's start, the note breaks after it, so the next turn's items go with that turn and are
    replaced by its Stop, shown once."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    turn = [_typed("u1", "go")]
    _transcript(path, turn)
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    for n in range(3):                                      # the first turn's tail, the owner interrupting it
        turn += [_said(f"a{n}", _text(f"First {n}.")), _bash(f"c{n}", f"echo {n}")]
    turn += [_typed("u2", "now this")]
    for n in range(11):                                     # 12 messages of the next turn, 15 in the round
        turn += [_said(f"b{n}", _text(f"Second {n}."))] + ([_bash(f"d{n}", f"echo b{n}")] if n < 10 else [])
    _transcript(path, turn)
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    shown = _shown(client, owner, "mac")
    assert "[mirror] 4 earlier messages of this session not mirrored" in shown, shown
    assert shown.count("[session] Second 10.") == 1, shown


def test_a_stop_says_nothing_of_whole_where_an_entry_before_its_turn_has_its_turn_s_first_time(
        client, owner, served, capsys, tmp_path, monkeypatch, spawned):
    """An entry before a Stop's turn timed at that turn's first time, not only
    after it, and the Stop says nothing of whole, as a round of the turn before said at that time would be taken for
    the Stop's."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")

    def at(n, e):
        return dict(e, timestamp=_stamp(n))

    turn = [at(90, _typed("u1", "go")), at(95, _said("a1", _text("Half done.")))]
    _bare_transcript(path, turn)
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    turn += [at(95, _typed("u2", "do X instead")), at(96, _said("a2", _text("Doing X.")))]
    _bare_transcript(path, turn)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _whole(mac) and all(w is None for _, w in _whole(mac))


@pytest.mark.parametrize("behind,said", [(50, True), (70, False)], ids=["50", "70"])
def test_a_stop_says_whole_where_its_transcript_is_ahead_of_this_clock_by_a_minute_at_most(
        client, owner, served, capsys, tmp_path, monkeypatch, spawned, behind, said):
    """The guard either side: this machine's clock 50 seconds behind the transcript, and the Stop says whole; 70, and
    it does not (ten minutes alone would pass any bound under 600 seconds)."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _said("a1", _text("Done."))])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    monkeypatch.setattr(ade_link, "_mirror_now", lambda: STAMPED_AT - behind)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert [w for _, w in _whole(mac) if w is not None] == ([STAMPED_AT * 1000] if said else [])


def test_a_stop_whose_message_s_cut_leaves_a_part_out_says_nothing_of_whole(client, owner, served, capsys, tmp_path,
                                                                           monkeypatch, spawned):
    """End to end: a Stop's message whose cut makes a secret's shape in a part
    goes with that part left out, and the Stop says nothing of whole."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    size = ade_link.MIRROR_TEXT - 80
    text = "y" * (size - 12) + " PP-ABCDEFGH" + "Z" * 10 + "y" * size        # a ticket only once the cut ends it
    _transcript(path, [_typed("u1", "go"), _said("a1", _text(text))])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _whole(mac) and all(w is None for _, w in _whole(mac))


def test_a_round_too_long_for_one_note_goes_as_several_in_order_and_no_word_is_lost(client, owner, served, capsys,
                                                                                    tmp_path, monkeypatch, spawned):
    """A round's note is at most the ADE's 4000 characters: words that do not fit go in the next note, and a message
    longer than one note holds goes in numbered parts, as the Stop's do, which the page joins."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    long = "\n".join(f"line {n} of the plan, with a \"quote\" and a tab\t." for n in range(200))
    _transcript(path, [_typed("u1", "go"), _said("a1", _text(long)), _bash("a2", "ls")])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    events = _events(client, owner, "mac")
    assert len(events) > 1 and {e.get("mirror") for e in events} == {"live"}, [e.get("mirror") for e in events]
    assert max(len(e["text"]) for e in events) <= 4000, [len(e["text"]) for e in events]
    shown = _shown(client, owner, "mac")
    parts = [x[len("[session] "):] for x in shown if x.startswith("[session] ")]
    assert all(x.startswith(f"({n} of {len(parts)}) ") for n, x in enumerate(parts, 1)), [x[:12] for x in parts]
    assert "".join(x.split(") ", 1)[1] for x in parts) == long
    assert shown[0] == "[owner] go" and shown[-1] == "[step] Bash: ls"


def test_a_round_s_message_holds_what_a_stop_s_does_however_json_writes_it(client, owner, served, capsys, tmp_path,
                                                                         monkeypatch, spawned):
    """A message a Stop would post whole, at most MIRROR_PARTS parts of MIRROR_TEXT - 80 characters (11,760), a round
    posts whole too, its quotes and backslashes taking twice their room as JSON."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    q, b = chr(34), chr(92)
    long = chr(10).join(f"x{n} = {q}a{b}{q}b{q} + {q}{b}{b}d{b}{b}{q}" for n in range(480))
    assert len(long) <= 11760 < len(json.dumps(long)) - 2, (len(long), len(json.dumps(long)))
    _transcript(path, [_typed("u1", "go"), _said("a1", _text(long))])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    parts = [x[len("[session] "):] for x in _shown(client, owner, "mac") if x.startswith("[session] ")]
    assert "".join(x.split(") ", 1)[1] for x in parts) == long, [x[-60:] for x in parts]


def test_an_escape_that_makes_a_secret_s_shape_leaves_its_message_out_alone(client, owner, served, capsys, tmp_path,
                                                                          monkeypatch, spawned):
    """Each piece of a round is checked as its JSON writes it: a line's end before `pm_` and 36 letters reads as an npm
    token there (the whole note was once left out, and the place moved past it). Now that run, 39 characters with no
    blank that are no plain word, is left out before the JSON is written, so the message goes with `***` in its place,
    the round's other words and its call going."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _said("a1", _text("Reading the plan." + chr(10) + "pm_" + "a" * 36)),
                       _bash("a2", "ls"), _said("a3", _text("Done."))])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[session] Reading the plan." + chr(10) + "***",
                                            "[step] Bash: ls", "[session] Done."]


def test_a_short_run_after_an_escape_that_makes_a_secret_s_shape_leaves_its_message_out_alone(
        client, owner, served, capsys, tmp_path, monkeypatch, spawned):
    """Each piece of a round is still checked as its JSON writes it where no long run is left to leave out: a
    carriage return before `k_live_`, ten characters, reads as a live key's `rk_live_` there, so that message is left
    out alone, the round's other words and its call going (pieces checked only as written sent
    the whole note as the left-out sentence, unreadable as a round's items)."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _said("a1", _text("Reading the plan." + chr(13) + "k_live_abc")),
                       _bash("a2", "ls"), _said("a3", _text("Done."))])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", f"[session] {LEFT_OUT}", "[step] Bash: ls",
                                            "[session] Done."]


def test_pieces_that_make_a_secret_s_shape_together_go_in_notes_of_their_own(client, owner, served, capsys, tmp_path,
                                                                            monkeypatch, spawned):
    """No note of a round holds a secret's shape across its pieces: a piece that would complete one starts a note of
    its own, and a run of calls that would make one together is cut in runs that do not, so each note stays a round's
    items, readable, and no word is left out for another's."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "Is there a -----BEGIN block in the repo?"),
                       _tool_use("a1", "Read", {"file_path": "-----BEGIN"}),
                       _tool_use("a2", "Read", {"file_path": "RSA PRIVATE KEY"}),
                       _said("a3", _text("Yes: an RSA PRIVATE KEY in deploy/."))])
    assert _live(monkeypatch, capsys, mac, _call(path, "Read", {"file_path": "x"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    events = _events(client, owner, "mac")
    assert [e["text"][:60] for e in events if not e["text"].startswith('{"items": ')] == []    # each a round's items
    assert [x for x in _shown(client, owner, "mac") if not x.startswith("[step] ")] == [
        "[owner] Is there a -----BEGIN block in the repo?", "[session] Yes: an RSA PRIVATE KEY in deploy/."]
    assert "\n".join(_steps(client, owner, "mac")) == "Read: -----BEGIN\nRead: RSA PRIVATE KEY"


def test_a_round_s_message_holds_what_a_stop_s_holds_however_many_parts_its_escapes_take(client, owner, served,
                                                                                         capsys, tmp_path,
                                                                                         monkeypatch, spawned):
    """A message a Stop posts whole, its 11,760 characters, a round posts whole too, in as many parts as its escapes
    take as JSON: an escape character every fourth takes six."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    long = ("abc" + chr(27)) * 2900
    assert len(long) <= 11760 and len(json.dumps(long)) - 2 > 6 * 3900, len(json.dumps(long))
    _transcript(path, [_typed("u1", "go"), _said("a1", _text(long))])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    parts = [x[len("[session] "):] for x in _shown(client, owner, "mac") if x.startswith("[session] ")]
    assert len(parts) > 6 and "".join(x.split(") ", 1)[1] for x in parts) == long, len(parts)


def test_the_client_keeps_no_budget_of_its_own_the_ade_keeps_the_live_view_s_share(client, owner, served, capsys,
                                                                                    tmp_path, monkeypatch, spawned):
    """The live view's share is counted where every post of a link arrives, on the ADE, apart from the link's 600
    events an hour: a link whose rounds posted 300 in the last hour, as
    an earlier client kept them in live_posts.json, posts its next."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    with open(os.path.join(mac, "live_posts.json"), "w") as fh:
        json.dump([time.time() - 60] * 300, fh)
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go")])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go"]


def test_a_round_refused_for_the_live_view_s_share_loses_no_words_and_holds_no_stop(client, owner, served, capsys,
                                                                                    tmp_path, monkeypatch, spawned):
    """A round the ADE refuses, its live view's share used (LIVE_PER_HOUR), keeps its place; the rounds of the next
    LIVE_REFUSED seconds ask nothing; and the turn's Stop, whose notes are the link's 600's, posts the words at once,
    never held as after an ADE that did not answer (the session's own events go on)."""
    from proofpane_open.ade import link as server_link
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _said("a1", _text("Working."))])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    monkeypatch.setattr(server_link, "LIVE_PER_HOUR", 0, raising=False)
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == []                    # refused: nothing, the place kept
    monkeypatch.setattr(server_link, "LIVE_PER_HOUR", 1200, raising=False)
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == []                    # within the minute after it, a round asks nothing
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[session] Working."]


def test_a_long_line_is_cut_and_a_step_shaped_like_a_secret_is_left_out(client, owner, served, capsys, tmp_path,
                                                                        monkeypatch, spawned):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _tool_use("a1", "Read", {"file_path": "/" + "x" * 400}),
                       _tool_use("a2", "Read", {"file_path": f"/work/{KEY}.txt"}),
                       _tool_use("a3", "Glob", {"pattern": "**/*.py", "path": "src"})])
    assert _live(monkeypatch, capsys, mac, _call(path, "Glob", {"pattern": "**/*.py"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    steps = _steps(client, owner, "mac")
    assert len(steps) == 1, steps                                # one round, one note
    lines = steps[0].split("\n")
    assert lines[1:] == [LEFT_OUT, "Glob: src"], lines
    assert lines[0] == "Read: /" + "x" * 100 + "...", lines[0]  # 110 characters
    assert not any(KEY[3:20] in e["text"] for e in _events(client, owner, "mac"))


def test_a_step_whose_secret_the_cut_would_split_is_left_out_whole(client, owner, served, capsys, tmp_path,
                                                                     monkeypatch, spawned):
    """A line is checked whole before it is cut, as the mirror checks a note before it cuts it into parts: a key that
    starts before the cut and ends after it is never posted in part."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    target = "/" + "x" * 81 + "/" + KEY
    line = "Read: " + target
    assert line.index(KEY) == 89 and len(line) > 110              # 18 of the key's characters before the cut at 107
    _transcript(path, [_typed("u1", "go"), _tool_use("a1", "Read", {"file_path": target})])
    assert _live(monkeypatch, capsys, mac, _call(path, "Read", {"file_path": target})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _steps(client, owner, "mac") == [LEFT_OUT]
    assert not any(KEY[3:13] in e["text"] for e in _events(client, owner, "mac"))


def test_a_cut_that_makes_a_line_look_like_a_secret_leaves_that_line_out_and_no_other(client, owner, served, capsys,
                                                                                       tmp_path, monkeypatch, spawned):
    """The line is checked again once cut, so a shape the cut makes leaves out that line alone, not the round's whole
    note at its post."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    target = "/" + "x" * 88 + " pp-abcdefghijklmnop"
    whole = "Read: " + target
    cut = whole[:107] + "..."
    assert ade_link._unsecret(whole) == whole and ade_link._unsecret(cut) == LEFT_OUT      # the cut makes the shape
    _transcript(path, [_typed("u1", "go"), _tool_use("a1", "Read", {"file_path": target}),
                       _tool_use("a2", "Read", {"file_path": "/work/notes.md"})])
    for given in (target, "/work/notes.md"):
        assert _live(monkeypatch, capsys, mac, _call(path, "Read", {"file_path": given})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _steps(client, owner, "mac") == [LEFT_OUT + "\nRead: /work/notes.md"]


def test_a_participant_s_turn_goes_whole_at_its_end_and_no_round_posts_any_of_it(client, owner, served, capsys,
                                                                                  tmp_path, monkeypatch, spawned):
    """history.json judges a participant's turn whole: a round never posts a part of it, so what the turn said after
    it touched an excepted project cannot go in a later round's part. Its Stop posts the note."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    work, secret = str(home / "work"), str(home / "grove" / "plan.md")
    first = [dict(_typed("u1", "what does the plan say?"), cwd=work), _said("a1", _text("Reading it.")),
             _tool_use("a2", "Read", {"file_path": secret}), _said("a3", _text("One more check."))]
    path = _in_projects(home, "s1", first)
    assert _live(monkeypatch, capsys, mac, _call(path, "Read", {"file_path": secret}, cwd=work)) == (0, "", "")
    assert _flush(capsys, mac, path, cwd=work) == (0, "", "")
    _transcript(path, first + [_bash("a4", "ls"), _said("a5", _text("The plan says hire two."))])
    assert _flush(capsys, mac, path, cwd=work) == (0, "", "")
    assert _shown(client, owner, "mac") == []
    assert _stop(monkeypatch, capsys, mac, path, cwd=work) == (0, "", "")
    assert _shown(client, owner, "mac") == [TURN_NOTE]


def test_a_participant_held_for_a_sub_agent_that_touched_gets_its_note_at_the_stop(client, owner, served, capsys,
                                                                                    tmp_path, monkeypatch, spawned):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    w, now = str(home / "work"), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w, timestamp=now),
                                     dict(_tool_use("a1", "Agent", {"description": "Read the grove plan"}),
                                          timestamp=now),
                                     dict(_said("a2", _text("Done.")), timestamp=now)])
    agent = home / ".claude" / "projects" / "-work" / "s1" / "subagents" / "agent-1.jsonl"
    agent.parent.mkdir(parents=True)
    _transcript(str(agent), [_tool_use("x1", "Read", {"file_path": str(home / "grove" / "x.md")})])
    old = time.time() - 3600
    os.utime(agent, (old, old))                                         # older than anything a pass reads
    assert _live(monkeypatch, capsys, mac, _call(path, "Agent", {"description": "Read the grove plan"}, cwd=w)) \
        == (0, "", "")
    assert _flush(capsys, mac, path, cwd=w) == (0, "", "")
    assert _shown(client, owner, "mac") == []
    assert _stop(monkeypatch, capsys, mac, path, cwd=w) == (0, "", "")
    assert _shown(client, owner, "mac") == [touched_note("s1")]


def test_a_round_that_finds_the_session_touched_an_excepted_project_posts_its_note_and_no_step(
        client, owner, served, capsys, tmp_path, monkeypatch, spawned):
    """Any session but a participant: once it touched an excepted project its turns go no more, one note saying so, and
    the call that touched it never shows as a step."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w, secret = str(home / "work"), str(home / "grove" / "x.md")
    path = _in_projects(home, "s1", [dict(_typed("u1", "what does the plan say?"), cwd=w),
                                     _tool_use("a1", "Read", {"file_path": secret}), _said("a2", _text("Hire two."))])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    assert _live(monkeypatch, capsys, mac, _call(path, "Read", {"file_path": secret}, cwd=w)) == (0, "", "")
    assert _flush(capsys, mac, path, cwd=w) == (0, "", "")
    assert _shown(client, owner, "mac") == [touched_note("s1")]
    assert not any("grove" in e["text"] for e in _events(client, owner, "mac"))


def test_a_touch_in_a_round_of_calls_alone_still_posts_its_note(client, owner, served, capsys, tmp_path, monkeypatch,
                                                                spawned):
    """A round whose entries since the last are calls alone, one of them a touch: the session is said not mirrored from
    now on, once, and only then marked told."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w, secret = str(home / "work"), str(home / "grove" / "plan.md")
    first = [dict(_typed("u1", "go"), cwd=w)]
    path = _in_projects(home, "s1", first)
    assert run(capsys, "--dir", mac, "history")[0] == 0
    assert _live(monkeypatch, capsys, mac, _call(path, "Read", {"file_path": secret}, cwd=w)) == (0, "", "")
    assert _flush(capsys, mac, path, cwd=w) == (0, "", "")
    _transcript(path, first + [_tool_use("a1", "Read", {"file_path": secret}),
                               _tool_use("a2", "Grep", {"pattern": "hire"})])
    assert _flush(capsys, mac, path, cwd=w) == (0, "", "")
    assert _stop(monkeypatch, capsys, mac, path, cwd=w) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", touched_note("s1")]


def test_a_session_history_json_excepts_shows_no_step_and_no_word_and_a_listed_one_does(client, owner, served,
                                                                                       capsys, tmp_path, monkeypatch,
                                                                                       spawned):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": ["~/work"], "except": ["~/grove"]})
    grove, work = str(home / "grove"), str(home / "work")
    held = _in_projects(home, "s-grove", [dict(_typed("u1", "a plan"), cwd=grove), _bash("a1", "cat plan.md"),
                                        _said("a2", _text("Noted."))])
    shown = _in_projects(home, "s-work", [dict(_typed("w1", "the build"), cwd=work), _bash("w2", "make test"),
                                          _said("w3", _text("Green."))])          # its own entries, none of the other's
    assert run(capsys, "--dir", mac, "history")[0] == 0
    for path, session, cwd in ((held, "s-grove", grove), (shown, "s-work", work)):
        assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"}, session=session, cwd=cwd)) \
            == (0, "", "")
        assert _flush(capsys, mac, path, session=session, cwd=cwd) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] the build", "[step] Bash: make", "[session] Green."]


def test_both_blocks_on_one_directory_give_one_line_a_call_and_the_mirror_s_rule(client, owner, served, capsys,
                                                                                 tmp_path, monkeypatch, spawned):
    """The mirror's block and the everywhere block on one directory: a call's line comes from its transcript once, and
    a flush the everywhere block started judges as the mirror's block, without `--listed-only`, once that block's hook
    ran for the session, as the two Stops do between them."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _said("a1", _text("Working.")), _bash("a2", "ls")])
    payload = _call(path, "Bash", {"command": "ls"})
    assert _live(monkeypatch, capsys, mac, payload, "--listed-only") == (0, "", "")
    assert _live(monkeypatch, capsys, mac, payload) == (0, "", "")
    assert spawned == [(mac, "s1", path, ("--listed-only",))]
    assert _flush(capsys, mac, path, "s1", None, "--listed-only") == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[session] Working.", "[step] Bash: ls"]


def test_a_mark_the_mirror_s_block_left_hours_ago_no_longer_counts(client, owner, served, capsys, tmp_path,
                                                                  monkeypatch, spawned):
    """The mirror's block refreshes its mark as its hook runs; one left by a block taken out goes stale, and the
    everywhere block's flush judges by its own flag again."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _said("a1", _text("Working."))])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    marks = [os.path.join(mac, "live", n) for n in os.listdir(os.path.join(mac, "live")) if n.endswith(".mirror")]
    assert len(marks) == 1
    # a minute either side of LIVE_MARK_AGE (three hours alone would pass any age from 600 seconds on)
    young = time.time() - (ade_link.LIVE_MARK_AGE - 60)
    os.utime(marks[0], (young, young))
    assert _flush(capsys, mac, path, "s1", None, "--listed-only") == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[session] Working."]       # not stale yet: it counts
    _transcript(path, [_typed("u1", "go"), _said("a1", _text("Working.")), _said("a2", _text("More."))])
    old = time.time() - (ade_link.LIVE_MARK_AGE + 60)
    os.utime(marks[0], (old, old))
    assert _flush(capsys, mac, path, "s1", None, "--listed-only") == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[session] Working."]


def test_the_everywhere_block_s_flush_alone_posts_only_what_history_json_lists(client, owner, served, capsys,
                                                                             tmp_path, monkeypatch, spawned):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _said("a1", _text("Working."))])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"}), "--listed-only") == (0, "", "")
    assert spawned == [(mac, "s1", path, ("--listed-only",))]
    assert _flush(capsys, mac, path, "s1", None, "--listed-only") == (0, "", "")
    assert _shown(client, owner, "mac") == []


def test_the_hook_writes_nothing_and_posts_nothing_while_a_flush_runs(client, owner, served, capsys, tmp_path,
                                                                      monkeypatch, spawned):
    """It asks nothing of the ADE, as a request recorded shows (the hook's own failure would be swallowed), and once
    a flush runs it leaves every file of the link's directory as it was."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go")])
    asked = []
    monkeypatch.setattr(ade_link.Link, "request", lambda *a, **k: asked.append(a) or {})
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    before = _files(mac)
    begun = time.monotonic()
    for command in ("pwd", "git status"):
        assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": command})) == (0, "", "")
    assert time.monotonic() - begun < 2.0
    assert (asked, _files(mac)) == ([], before)
    assert spawned == [(mac, "s1", path, ())]                    # the first started it; the flush runs for the rest
    assert not os.path.exists(os.path.join(mac, "mirror.log"))


def test_the_turn_s_end_posts_its_steps_again_in_one_note(client, owner, served, capsys, tmp_path, monkeypatch,
                                                          spawned):
    """A Stop posts the turn again, its calls in one note between its words, whatever its rounds posted: LIVE_STEPS
    lines, the newest calls after one that counts the rest (_live_note), so "all its calls" holds to LIVE_STEPS - 1 of
    them. The round's note stands until then."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _bash("a1", "ls")])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[step] Bash: ls"]
    _transcript(path, [_typed("u1", "go"), _bash("a1", "ls"), _bash("a2", "pwd"), _said("a3", _text("Done."))])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[step] Bash: ls\nBash: pwd", "[session] Done."]
    assert _shown(client, owner, "mac", rounds_too=True)[:2] == ["[owner] go", "[step] Bash: ls"]   # the round stays


def test_a_session_the_live_hook_never_ran_for_gets_its_words_alone_at_its_stop(client, owner, served, capsys,
                                                                                tmp_path, monkeypatch):
    """The calls go only for a session the live hook ran for: the Stop of any other session posts its words alone, as
    the Stop's mirror had them before the live view."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _bash("a1", "ls"), _said("a2", _text("Done."))])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[session] Done."]


def test_a_round_and_a_stop_at_once_post_each_step_once(client, owner, served, capsys, tmp_path, monkeypatch, spawned):
    """The turn's Stop posts while a round judges: the Stop takes the entries and their calls, and the round, finding
    the place moved, posts nothing; no step goes twice or not at all."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _bash("a1", "ls")])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    judged, stopped = ade_link._mirror_held, []

    def held_while_a_stop_posts(link, *a, **k):
        if not stopped:
            stopped.append(True)
            ade_link._mirror_turn(link, types.SimpleNamespace(listed_only=False),
                                  {"transcript_path": path, "session_id": "s1", "cwd": str(tmp_path)})
        return judged(link, *a, **k)

    monkeypatch.setattr(ade_link, "_mirror_held", held_while_a_stop_posts)
    assert _flush(capsys, mac, path) == (0, "", "")
    assert stopped and _shown(client, owner, "mac") == ["[owner] go", "[step] Bash: ls"]


def test_a_pass_whose_posts_stop_partway_loses_no_words_and_shows_the_calls_where_they_came(
        client, owner, served, capsys, tmp_path, monkeypatch, spawned):
    """A Stop's notes in the turn's order, the calls between the words they came between; and where its third post
    meets the ADE's hourly most, the place goes back to before the first entry of the first note not posted, so the
    next Stop posts the rest and no word is lost."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _said("a1", _text("Let me check.")), _bash("a2", "ls"),
                       _said("a3", _text("Done."))])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    real, posts = ade_link.Link.request, []

    def the_third_refused(self, method, url, *a, **k):
        if method == "POST" and url == "/events":
            posts.append(url)
            if len(posts) == 3:
                raise ade_link.Refused(429, "A link posts at most 600 events an hour.")
        return real(self, method, url, *a, **k)

    monkeypatch.setattr(ade_link.Link, "request", the_third_refused)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    monkeypatch.setattr(ade_link.Link, "request", real)
    now = time.time() + 3600
    monkeypatch.setattr(ade_link, "_mirror_now", lambda: now)  # past the wait after the ADE did not take the post
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[session] Let me check.", "[step] Bash: ls",
                                            "[session] Done."]


def test_a_round_s_steps_beyond_the_most_one_note_holds_are_counted(client, owner, served, capsys, tmp_path,
                                                                     monkeypatch, spawned):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go")] + [_bash(f"a{n}", f"tool{n} run") for n in range(25)])
    for n in range(25):
        assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": f"tool{n} run"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    steps = _steps(client, owner, "mac")
    assert len(steps) == 1, steps
    lines = steps[0].split("\n")
    assert lines[0] == "6 earlier steps" and len(lines) == 20, lines[:3]        # the newest 19, after a count
    assert lines[1] == "Bash: tool6" and lines[-1] == "Bash: tool24", lines


def test_a_round_an_older_ade_does_not_take_loses_no_words(client, owner, served, capsys, tmp_path, monkeypatch,
                                                          spawned):
    """An ADE from before the live view's rounds refuses one (422, a mirror it does not know): the round keeps its
    place, and the turn's Stop posts the words and the calls as its own notes, which that ADE takes."""
    from proofpane_open.ade import link as server_link
    monkeypatch.setattr(server_link, "MIRRORS", ("owner", "session", "info", "step"))
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _said("a1", _text("Working.")), _bash("a2", "ls")])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == []                    # refused: nothing, the place kept
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[session] Working.", "[step] Bash: ls"]


def test_a_round_an_older_ade_refused_keeps_its_place_for_a_round_after_the_minute(client, owner, served, capsys,
                                                                                 tmp_path, monkeypatch, spawned):
    """The same refusal, a 422 for a mirror that ADE does not know, is a round's refusal as a 429 is: the rounds of
    the next LIVE_REFUSED seconds ask nothing, and the refused round's place stays where it was, so a round after
    them, where the ADE takes rounds again, posts the words it held."""
    from proofpane_open.ade import link as server_link
    mirrors = server_link.MIRRORS
    monkeypatch.setattr(server_link, "MIRRORS", ("owner", "session", "info", "step"))
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _said("a1", _text("Working.")), _bash("a2", "ls")])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == []                    # refused: nothing
    monkeypatch.setattr(server_link, "MIRRORS", mirrors)        # the ADE takes rounds again
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == []                    # within the minute, a round asks nothing
    later = ade_link._mirror_now() + ade_link.LIVE_REFUSED + 1
    monkeypatch.setattr(ade_link, "_mirror_now", lambda: later)
    assert _flush(capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[session] Working.", "[step] Bash: ls"]


def test_a_refusal_for_the_link_s_hourly_most_loses_no_words(client, owner, served, capsys, tmp_path, monkeypatch,
                                                              spawned):
    """A 429 is the ADE not taking the post now: the place goes back to what went, as when it does not answer, and a
    later pass posts the words."""
    from proofpane_open.ade import link as server_link
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _said("a1", _text("Done."))])
    monkeypatch.setattr(server_link, "EVENTS_PER_HOUR", 0)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    monkeypatch.setattr(server_link, "EVENTS_PER_HOUR", 600)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == []                    # within the minute after it: nothing, the place kept
    now = time.time() + 3600
    monkeypatch.setattr(ade_link, "_mirror_now", lambda: now)  # past the wait after the ADE did not take the post
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[session] Done."]


def test_a_flush_runs_a_round_every_10_seconds_and_ends_90_seconds_after_its_last_post(client, owner, served, capsys,
                                                                                      tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go")])
    clock, rounds = [1000.0], []
    monkeypatch.setattr(ade_link, "_mirror_clock", lambda: clock[0])
    monkeypatch.setattr(ade_link, "_live_sleep", lambda s: clock.__setitem__(0, clock[0] + s), raising=False)
    real = ade_link._mirror_round

    def counted(*a, **k):
        rounds.append(clock[0] - 1000.0)
        return real(*a, **k)

    monkeypatch.setattr(ade_link, "_mirror_round", counted)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))           # as the hook starts it: nothing on its input
    assert _cli(capsys, "--dir", mac, "mirror", "flush", "--session", "s1", "--transcript", path) == (0, "", "")
    assert rounds == [10.0 * n for n in range(11)]               # every 10 seconds; the last 100 after the post
    assert _shown(client, owner, "mac") == ["[owner] go"]


def test_a_flush_where_the_link_does_not_seal_and_words_alone_come_ends_90_seconds_after_it_began(
        client, owner, served, capsys, tmp_path, monkeypatch):
    """Where the link does not seal, a round that finds words alone posts nothing, and
    is no post, so the flush ends 90 seconds after its start, not at the hour, though new words come at each round."""
    monkeypatch.setattr(ade_link, "_mirror_mode", lambda link: ("clear",))
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    entries = [_typed("u1", "go")]
    _transcript(path, entries)
    clock, rounds = [1000.0], []
    monkeypatch.setattr(ade_link, "_mirror_clock", lambda: clock[0])
    monkeypatch.setattr(ade_link, "_live_sleep", lambda s: clock.__setitem__(0, clock[0] + s), raising=False)
    real = ade_link._mirror_round

    def with_words(*a, **k):
        rounds.append(clock[0] - 1000.0)
        entries.append(_said(f"a{len(rounds)}", _text(f"Line {len(rounds)}.")))
        _transcript(path, entries)
        return real(*a, **k)

    monkeypatch.setattr(ade_link, "_mirror_round", with_words)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))           # as the hook starts it: nothing on its input
    assert _cli(capsys, "--dir", mac, "mirror", "flush", "--session", "s1", "--transcript", path) == (0, "", "")
    assert rounds == [10.0 * n for n in range(11)], (len(rounds), rounds[-2:])
    assert _shown(client, owner, "mac") == []


def test_a_flush_that_goes_on_posting_ends_after_an_hour(client, owner, served, capsys, tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go")])
    clock, rounds = [0.0], []
    monkeypatch.setattr(ade_link, "_mirror_clock", lambda: clock[0])
    monkeypatch.setattr(ade_link, "_live_sleep", lambda s: clock.__setitem__(0, clock[0] + s), raising=False)
    monkeypatch.setattr(ade_link, "_mirror_round", lambda *a, **k: rounds.append(clock[0]) or True)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))           # as the hook starts it: nothing on its input
    assert _cli(capsys, "--dir", mac, "mirror", "flush", "--session", "s1", "--transcript", path) == (0, "", "")
    assert len(rounds) == 362 and rounds[-1] == 3610.0, (len(rounds), rounds[-3:])


def test_the_mirror_block_s_hook_gives_its_mark_the_time_again_once_an_hour(client, owner, served, capsys, tmp_path,
                                                                          monkeypatch, spawned):
    """The mirror block's hook gives its mark the time again once it is LIVE_MARK_FRESH old, and leaves a younger one
    as it is."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go")])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    mark = os.path.join(mac, ade_link._live_mark_name("s1"))
    old = time.time() - 2 * 3600
    os.utime(mark, (old, old))
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "pwd"})) == (0, "", "")
    assert time.time() - os.stat(mark).st_mtime < 60, "given the time again"
    younger = time.time() - 600
    os.utime(mark, (younger, younger))
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "pwd"})) == (0, "", "")
    assert abs(os.stat(mark).st_mtime - younger) < 1, "left as it is"


def test_a_flush_s_end_takes_away_locks_and_marks_untouched_for_a_week(client, owner, served, capsys, tmp_path,
                                                                      monkeypatch):
    """When a flush ends it takes away the locks and marks in live/ untouched for LIVE_KEPT_DAYS days, their sessions
    gone, and leaves the rest."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    folder = os.path.join(mac, "live")
    os.makedirs(folder, exist_ok=True)
    stale = [os.path.join(folder, "a" * 32 + end) for end in (".lock", ".mirror")]
    # an hour either side of LIVE_KEPT_DAYS (eight days alone would pass any number of days under it)
    young = [os.path.join(folder, "c" * 32 + end) for end in (".lock", ".mirror")]
    fresh = os.path.join(folder, "b" * 32 + ".mirror")
    for p in stale + young + [fresh]:
        open(p, "w").close()
    old = time.time() - (ade_link.LIVE_KEPT_DAYS * 86400 + 3600)
    for p in stale:
        os.utime(p, (old, old))
    nearly = time.time() - (ade_link.LIVE_KEPT_DAYS * 86400 - 3600)
    for p in young:
        os.utime(p, (nearly, nearly))
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go")])
    clock = [0.0]
    monkeypatch.setattr(ade_link, "_mirror_clock", lambda: clock[0])
    monkeypatch.setattr(ade_link, "_live_sleep", lambda s: clock.__setitem__(0, clock[0] + s), raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert _cli(capsys, "--dir", mac, "mirror", "flush", "--session", "s1", "--transcript", path) == (0, "", "")
    assert [os.path.exists(p) for p in stale + young + [fresh]] == [False, False, True, True, True]


def test_a_stop_s_note_of_calls_is_timed_by_its_first_call_s_entry_never_by_when_it_posts(
        client, owner, served, capsys, tmp_path, monkeypatch, spawned):
    """A Stop's note of calls carries its first call's time from the transcript, as its notes of words carry theirs,
    never the time this machine posts it: here the client's clock is moved an hour past its transcript's, where a note
    timed at its post would be refused by the ADE, which takes no time more than 300 seconds past its own, and the
    turn's calls lost. A machine whose clock runs ahead writes
    its transcript ahead too, which this case does not pin."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _transcript(path, [dict(_typed("u1", "go"), timestamp=now), dict(_bash("a1", "ls"), timestamp=now),
                       dict(_said("a2", _text("Done.")), timestamp=now)])
    assert _live(monkeypatch, capsys, mac, _call(path, "Bash", {"command": "ls"})) == (0, "", "")
    ahead = time.time() + 3600
    monkeypatch.setattr(ade_link, "_mirror_now", lambda: ahead)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _shown(client, owner, "mac") == ["[owner] go", "[step] Bash: ls", "[session] Done."]


def test_a_flush_that_finds_another_running_for_its_session_ends_at_once(client, owner, served, capsys, tmp_path,
                                                                         monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go")])
    name = _lock_name("s1") or "live-held.lock"
    held = ade_link._try_lock(ade_link.Link(mac), name)
    clock, rounds = [0.0], []
    monkeypatch.setattr(ade_link, "_mirror_clock", lambda: clock[0])
    monkeypatch.setattr(ade_link, "_live_sleep", lambda s: clock.__setitem__(0, clock[0] + s), raising=False)
    monkeypatch.setattr(ade_link, "_mirror_round", lambda *a, **k: rounds.append(1) or False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))               # as the hook starts it: nothing on its input
    try:
        assert _cli(capsys, "--dir", mac, "mirror", "flush", "--session", "s1", "--transcript", path) == (0, "", "")
    finally:
        os.close(held)
    assert rounds == []


def test_the_hook_starts_a_flush_as_a_process_of_its_own(monkeypatch, tmp_path):
    started = []

    class Started:
        pid = 4321

    def popen(argv, **kw):
        started.append((argv, kw))
        return Started()

    monkeypatch.setattr(subprocess, "Popen", popen)
    assert ade_link._live_spawn(str(tmp_path), "s1", "/t/s1.jsonl", "/work", "--listed-only") == 4321
    (argv, kw), = started
    assert argv == [sys.executable, "-I", "-X", "utf8", os.path.abspath(ade_link.__file__), "--dir", str(tmp_path),
                    "mirror", "flush", "--session", "s1", "--transcript", "/t/s1.jsonl", "--listed-only",
                    "--cwd", "/work"]
    assert kw["start_new_session"] is True and kw["close_fds"] is True
    assert kw["stdin"] == kw["stdout"] == kw["stderr"] == subprocess.DEVNULL


def test_the_mirror_s_templates_run_the_live_hook_before_each_call_and_not_after():
    for name, flag in (("ade_link.mirror.settings.json", ""), ("ade_link.everywhere.settings.json", " --listed-only")):
        hooks = json.load(open(os.path.join(ROOT, name)))["hooks"]
        live = {event: [h for m in hooks.get(event, []) for h in m.get("hooks", [])
                        if " mirror live" in h.get("command", "")] for event in ("PreToolUse", "PostToolUse")}
        assert len(live["PreToolUse"]) == 1 and live["PostToolUse"] == [], (name, live)
        hook = live["PreToolUse"][0]
        assert hook["command"].endswith(f" mirror live{flag} || true") and hook.get("timeout", 60) <= 5, hook
