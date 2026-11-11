"""A move held, end to end: the app served on a local port (ade/moves.py, ade/holds.py) and the machine's link client
against it (ade_link.py move), unchanged. While the move's hold runs, the client's move run reads its feed to its end
and finds nothing of the move; once the hold has ended, by the server's clock, and the page's next read has settled it,
the next run finds the move past where its looks had read, takes it and sends the session, which starts in the
workspace. The server's clock, the client's and every signature's are the test's."""

import json
import time
import uuid

import pytest

import ade_link
from proofpane_open import paths, store  # noqa: F401  (paths puts the open packages on sys.path)
from proofpane_core.canonical import canonicalize
from test_ade_link_client import pair, run, served  # noqa: F401  (served: a fixture)
from test_ade_link_client_commands import account, command, device, host, owner  # noqa: F401  fixtures
from test_ade_link_client_move import a_repository, a_session, home, listed, quiet_machine  # noqa: F401  fixtures
from test_ade_link_client_move import with_work_not_committed
from test_ade_link_moves import workspace  # noqa: F401  fixture
from test_passkeys import as_step


@pytest.fixture
def at(monkeypatch):
    """The server's clock, the client's and the signers': [now]."""
    t = [int(time.time())]
    monkeypatch.setattr(time, "time", lambda: t[0])
    return t


def signed_move(client, owner, served, device, link_id, session):
    """A move as the page signs one, with the life the page gives it, posted: the answer."""
    now = int(time.time())
    p = {"v": 2, "deployment": host(served), "purpose": "move", "account": account(client, owner),
         "nonce": client.post("/api/ade/link/nonce", headers=owner, json={"purpose": "move"}).json()["nonce"],
         "id": uuid.uuid4().hex, "to": link_id, "session": session, "created_at": now, "expires_at": now + 3600}
    credential = device.get(as_step(canonicalize(p)))
    r = client.post("/api/ade/link/commands", headers=owner, json={"payload": p, "credential": credential})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_machine_finds_nothing_of_a_move_while_its_hold_runs_then_takes_and_sends_it(client, owner, served, device,
                                                                                         workspace, home, at, capsys,
                                                                                         tmp_path):
    d = pair(client, owner, served, capsys, tmp_path / "links" / "mac", "mac")
    assert run(capsys, "--dir", d, "pin")[0] == 0
    link_id = json.loads((d / "config.json").read_text())["link_id"]
    work, _ = a_repository(tmp_path, home)
    with_work_not_committed(work)
    transcript = a_session(home, "s-moved", work)
    listed(d)                                                     # the app's History list has the session
    before = command(client, owner, served, device, link_id)      # something in the feed for a look to read past
    hold = signed_move(client, owner, served, device, link_id, "s-moved")["held"]
    at[0] = hold["takes_effect_at"] - 1
    assert run(capsys, "--dir", d, "move")[:2] == (0, "ADE: no move for this link is waiting\n")
    link = ade_link.Link(str(d))
    assert link.number(ade_link.MOVES_SEQ, 0) == before and ade_link._moves(link) == {}    # read to its end
    at[0] += 1
    (v,) = client.get("/api/ade/link/moves", headers=owner).json()["moves"]     # the page's read settles the hold
    assert v["state"] == "asked" and v["seq"] > before
    code, out, err = run(capsys, "--dir", d, "move")
    assert code == 0, (out, err)
    assert out.startswith(f"ADE: move [{v['seq']}] of session s-moved: sent to the ADE: its transcript "
                          f"({len(transcript.read_bytes())} bytes)")
    with store.connect() as db:
        row = db.execute("SELECT * FROM ade_link_moves WHERE seq = ?", (v["seq"],)).fetchone()
    assert (row["state"], row["workspace_session"]) == ("started", "0123456789ab")
    assert workspace.agent.files[(str(v["seq"]), "transcript")] == transcript.read_bytes()
    assert workspace.agent.started[0]["moved"]["session"] == "s-moved"
