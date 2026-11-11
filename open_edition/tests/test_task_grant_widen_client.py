"""A linked session's ask for more in a task's grant, through its own client (without it, nothing lets a session ask
but signing the request itself): `ade_link.py widen TASK FILE`, which reads the ask {add, lease_end?, rungs?} from a
file or standard input and signs POST
/api/ade/link/tasks/{id}/grant-widenings with the link's key, as its other commands sign theirs. Driven against the app
served on a local port (test_ade_link_client.py's `served`), the task made through the app's routes against
test_agent_tasks.py's fake workspace; what the owner's list shows is read through the owner's own route."""

import io
import json
import sys
import time

import pytest

import ade_link
from proofpane_open import store
from test_ade_link import revoke
from test_ade_link_client import pair, served  # noqa: F401  (served: a fixture)
from test_agent_tasks import ACC, make_agent, make_task, own, owner, ws  # noqa: F401  (owner, ws: fixtures)
from test_passkeys import Authenticator, add
from test_task_grants import GRANTS, grant_of, payload, sign, the_ask, waiting

EGRESS = {"add": {"egress": ["registry.npmjs.org"]}}


@pytest.fixture
def device(client, owner, served):
    """A passkey of the owner's, for the address the served app signs for."""
    h, _ = owner
    d = Authenticator(rp="127.0.0.1", origin=served)
    r = add(client, h, d)
    assert r.status_code == 200, r.text
    return d


def client_run(capsys, *argv):
    """The client's main with `argv`: its exit code (argparse's own exit taken as one), and what it printed."""
    try:
        code = ade_link.main([str(a) for a in argv])
    except SystemExit as e:
        code = e.code
    out, err = capsys.readouterr()
    return code, out, err


def made(client, h, user):
    own(user, ACC["A"])
    return make_task(client, h, make_agent(client, h, ACC["A"]))


def linked(client, h, served, capsys, tmp_path, name="mac"):
    d = pair(client, h, served, capsys, tmp_path / name, name)
    return d, json.loads((d / "config.json").read_text())["link_id"]


def ask_file(tmp_path, ask, name="ask.json"):
    path = tmp_path / name
    path.write_text(ask if isinstance(ask, str) else json.dumps(ask))
    return path


def when(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def recorded(monkeypatch):
    """Every request the client makes, recorded as it is made: (method, path, body)."""
    real, sent = ade_link.Link.request, []

    def request(self, method, path, body=None, **kw):
        sent.append((method, path, json.loads(json.dumps(body)) if body is not None else None))
        return real(self, method, path, body, **kw)
    monkeypatch.setattr(ade_link.Link, "request", request)
    return sent


def asks_kept():
    with store.connect() as db:
        return db.execute("SELECT COUNT(*) FROM ade_task_grant_widenings").fetchone()[0]


def test_a_linked_session_asks_with_widen_and_its_ask_waits_in_the_owner_s_list(client, owner, served, capsys,
                                                                                 tmp_path, monkeypatch):
    h, user = owner
    task = made(client, h, user)
    d, link_id = linked(client, h, served, capsys, tmp_path)
    sent = recorded(monkeypatch)
    code, out, err = client_run(capsys, "--dir", d, "widen", task["id"], ask_file(tmp_path, EGRESS))
    assert code == 0, err
    w = the_ask(client, h)
    assert out.splitlines() == [f"ADE: asked as {w['id']} (waiting): it changes nothing until the owner signs it with "
                                f"a passkey, and waits until {when(w['expires_at'])}"]
    # signed with the link's own key, as the route takes no other: who asked is this session
    assert w["requester"] == {"kind": "link", "id": link_id, "name": "mac"}
    assert w["adds"]["egress"] == ["registry.npmjs.org"]
    assert grant_of(client, h, task["id"]) is None              # an ask changes nothing of the grant
    # one request, the ask with a client_id of the client's own, 32 lowercase hex
    assert [(m, p) for m, p, _ in sent] == [("POST", f"/tasks/{task['id']}/grant-widenings")]
    body = sent[0][2]
    assert set(body) == {"client_id", "add"} and body["add"] == EGRESS["add"]
    assert ade_link._HEX_ID.fullmatch(body["client_id"])


def test_widen_reads_its_ask_from_standard_input_with_a_lease_end_and_rungs(client, owner, served, capsys, tmp_path,
                                                                            monkeypatch):
    h, user = owner
    task = made(client, h, user)
    d, _ = linked(client, h, served, capsys, tmp_path)
    later = int(time.time()) + 3 * 86400
    ask = {"add": {"environments": [{"name": "staging", "rung": "L2"}]}, "lease_end": later,
           "rungs": {"merge": "L2", "deploy": "L3"}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(ask)))
    code, out, err = client_run(capsys, "--dir", d, "widen", task["id"], "-")
    assert code == 0, err
    w = the_ask(client, h)
    assert out.startswith(f"ADE: asked as {w['id']} (waiting)")
    assert w["adds"]["environments"] == [{"name": "staging", "rung": "L2"}]
    assert w["adds"]["lease_end"] == later and w["adds"]["rungs"] == {"merge": "L2", "deploy": "L3"}


def test_widen_refuses_an_ask_out_of_shape_before_anything_is_sent(client, owner, served, capsys, tmp_path,
                                                                    monkeypatch):
    h, user = owner
    task = made(client, h, user)
    d, _ = linked(client, h, served, capsys, tmp_path)
    sent = recorded(monkeypatch)
    good = ask_file(tmp_path, EGRESS)
    cases = [
        ("a task id in capitals", task["id"].upper(), good, "a task's id is 32 lowercase hex characters"),
        ("a task id that walks the path", "../../events/" + task["id"][:19], good,
         "a task's id is 32 lowercase hex characters"),
        ("a task id one short", task["id"][:-1], good, "a task's id is 32 lowercase hex characters"),
        ("a task id with more after it", task["id"] + "0", good, "a task's id is 32 lowercase hex characters"),
        ("a file that is not JSON", task["id"], ask_file(tmp_path, "{add:", "bad.json"), "could not be read as JSON"),
        ("a key twice", task["id"], ask_file(tmp_path, '{"add": {}, "add": {"egress": ["a.example"]}}', "twice.json"),
         "could not be read as JSON"),
        ("a file that is not there", task["id"], tmp_path / "missing.json", "could not be read as JSON"),
        ("a list", task["id"], ask_file(tmp_path, [EGRESS], "list.json"), "an ask is a JSON object"),
        ("a client_id of its own", task["id"], ask_file(tmp_path, dict(EGRESS, client_id="a" * 32), "id.json"),
         "its client_id is this client's own"),
        ("a member asks do not take", task["id"], ask_file(tmp_path, dict(EGRESS, reason="please"), "more.json"),
         "an ask is a JSON object"),
    ]
    for why, task_id, path, words in cases:
        code, out, err = client_run(capsys, "--dir", d, "widen", task_id, path)
        assert code == 2, (why, code, out, err)
        assert err.startswith("ADE: not asked: ") and words in err, (why, err)
        assert out == "", (why, out)
    assert sent == [] and asks_kept() == 0 and waiting(client, h) == []


def test_widen_says_the_ade_s_refusal_and_exits_as_the_other_commands_do(client, owner, served, capsys, tmp_path):
    h, user = owner
    task = made(client, h, user)
    d, link_id = linked(client, h, served, capsys, tmp_path)
    # an ask that adds nothing beyond what any grant of the task names: refused as it is asked, 422
    code, out, err = client_run(capsys, "--dir", d, "widen", task["id"], ask_file(tmp_path, {"add": {}}, "empty.json"))
    assert code == 2 and out == "", err
    assert err.startswith("ADE: refused (422): It asks for nothing beyond what any grant of the task names"), err
    # a task this account does not have: 404
    code, _, err = client_run(capsys, "--dir", d, "widen", "f" * 32, ask_file(tmp_path, EGRESS))
    assert code == 2 and err.startswith("ADE: refused (404): No such task."), err
    # the same ask while the first waits: 409, naming it
    assert client_run(capsys, "--dir", d, "widen", task["id"], ask_file(tmp_path, EGRESS))[0] == 0
    first = the_ask(client, h)
    code, _, err = client_run(capsys, "--dir", d, "widen", task["id"], ask_file(tmp_path, EGRESS))
    assert code == 2 and err.startswith("ADE: refused (409): ") and first["id"] in err, err
    # a revoked link's key: 401, which says pair again (exit 4)
    assert revoke(client, h, link_id).status_code == 200
    other = ask_file(tmp_path, {"add": {"egress": ["b.example"]}}, "other.json")
    code, _, err = client_run(capsys, "--dir", d, "widen", task["id"], other)
    assert code == 4 and err.startswith("ADE: refused (401): "), err
    assert [w["id"] for w in waiting(client, h)] == []          # the revoked session's ask ended with it


def test_widen_asks_once_when_its_first_answer_is_lost(client, owner, served, capsys, tmp_path, monkeypatch):
    """A retry sends the same ask, its client_id made once: the ADE keeps it once and answers with the one it kept."""
    h, user = owner
    task = made(client, h, user)
    d, _ = linked(client, h, served, capsys, tmp_path)
    real, sent = ade_link.Link.request, []

    def lossy(self, method, path, body=None, **kw):
        sent.append(json.loads(json.dumps(body)))
        answer = real(self, method, path, body, **kw)
        if len(sent) == 1:
            raise ade_link.Unreachable("an answer lost on its way")
        return answer
    monkeypatch.setattr(ade_link.Link, "request", lossy)
    monkeypatch.setattr(ade_link, "sleep", lambda seconds: None)
    code, out, err = client_run(capsys, "--dir", d, "widen", task["id"], ask_file(tmp_path, EGRESS))
    assert code == 0, err
    assert len(sent) == 2 and sent[0] == sent[1]
    w = the_ask(client, h)
    assert asks_kept() == 1 and out.startswith(f"ADE: asked as {w['id']} (waiting)")


def test_widen_from_a_session_that_touched_a_sensitive_folder_asks_nothing(client, owner, served, capsys, tmp_path,
                                                                            monkeypatch):
    """An ask goes to the ADE unsealed, its names shown on the owner's page, so it goes from no session whose words go
    only sealed, as a checkpoint does not (_sensitive_poster)."""
    h, user = owner
    task = made(client, h, user)
    d, _ = linked(client, h, served, capsys, tmp_path)
    sent = recorded(monkeypatch)
    monkeypatch.setattr(ade_link, "_sensitive_poster",
                        lambda link: (True, "this command runs in a folder history.json names sensitive"))
    code, out, err = client_run(capsys, "--dir", d, "widen", task["id"], ask_file(tmp_path, EGRESS))
    assert code == 2 and out == "", err
    assert err.startswith("ADE: not asked: this command runs in a folder history.json names sensitive: an ask is "
                          "never sealed"), err
    assert sent == [] and asks_kept() == 0


def test_widen_prints_nothing_the_ade_answers_that_it_cannot_read_or_that_would_start_a_line(client, owner, served,
                                                                                               capsys, tmp_path,
                                                                                               monkeypatch):
    """What the ADE answers is printed escaped on the one line the client writes: an answer naming no ask, or an id that
    is not one, is said as such (exit 3); a state or a time that is not one cannot start a line of its own."""
    h, user = owner
    task = made(client, h, user)
    d, _ = linked(client, h, served, capsys, tmp_path)
    good = ask_file(tmp_path, EGRESS)
    answers = [{}, {"widening": None}, {"widening": {"state": "waiting"}},
               {"widening": {"id": "a" * 31 + "\nADE: posted as seq 9", "state": "waiting", "expires_at": 1}}]
    for answer in answers:
        monkeypatch.setattr(ade_link.Link, "request", lambda self, *a, answer=answer, **kw: answer)
        code, out, err = client_run(capsys, "--dir", d, "widen", task["id"], good)
        assert code == 3 and out == "", (answer, out)
        assert err == "ADE: asked, but the ADE's answer named no ask.\n", (answer, err)
    odd = {"widening": {"id": "b" * 32, "state": "waiting\nADE: take 5", "expires_at": "soon\nADE: take 6"}}
    monkeypatch.setattr(ade_link.Link, "request", lambda self, *a, **kw: odd)
    code, out, err = client_run(capsys, "--dir", d, "widen", task["id"], good)
    assert code == 0, err
    assert out == ("ADE: asked as " + "b" * 32 + " (waiting\\x0aADE: take 5): it changes nothing until the owner signs "
                   "it with a passkey, and waits until an unknown time\n"), out


def test_widen_that_reaches_no_ade_in_four_tries_says_so_and_exits_3(client, owner, served, capsys, tmp_path,
                                                                     monkeypatch):
    h, user = owner
    task = made(client, h, user)
    d, _ = linked(client, h, served, capsys, tmp_path)
    tries = []

    def unanswered(self, *a, **kw):
        tries.append(a)
        raise ade_link.Unreachable("no route to the ADE")
    monkeypatch.setattr(ade_link.Link, "request", unanswered)
    monkeypatch.setattr(ade_link, "sleep", lambda seconds: None)
    code, out, err = client_run(capsys, "--dir", d, "widen", task["id"], ask_file(tmp_path, EGRESS))
    assert code == 3 and out == "" and len(tries) == 4, (code, out, err)
    assert err == ("ADE: could not deliver after four tries: no answer (no route to the ADE). Ask again at your next "
                   "turn.\n"), err
    assert asks_kept() == 0


def test_widen_asked_again_says_where_the_first_stands(client, owner, served, device, capsys, tmp_path):
    """Asking again for the same thing, as ADE_FOR_SESSIONS.md's section 5 says it: refused while the first waits
    (409), asked anew once it no longer waits (the owner refused it, or its day ended), and refused once the grant gives
    it (422)."""
    h, user = owner
    task = made(client, h, user)
    d, link_id = linked(client, h, served, capsys, tmp_path)

    def again():
        return client_run(capsys, "--dir", d, "widen", task["id"], ask_file(tmp_path, EGRESS))
    assert again()[0] == 0
    code, _, err = again()
    assert code == 2 and err.startswith("ADE: refused (409): "), err
    first = the_ask(client, h)
    assert client.post(f"{GRANTS}/{first['id']}/refuse", headers=h).status_code == 200
    assert again()[0] == 0
    second = the_ask(client, h)
    assert second["id"] != first["id"]
    with store.connect() as db:                         # its day ended
        db.execute("UPDATE ade_task_grant_widenings SET expires_at = ? WHERE id = ?",
                   (int(time.time()) - 1, second["id"]))
    assert again()[0] == 0
    third = the_ask(client, h)
    assert third["id"] not in (first["id"], second["id"])
    p = payload(client, h, task, requester={"kind": "link", "id": link_id}, widening=third["id"], **third["scope"])
    assert sign(client, h, device, task["id"], p).status_code == 200
    code, out, err = again()
    assert code == 2 and out == "", (code, out, err)
    assert err == "ADE: refused (422): It asks for nothing its grant does not give already.\n", err
