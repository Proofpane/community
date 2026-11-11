"""The link client's side of a linked Manager
(open_edition/ade_link.py manager and manager-act), against the app served on a local port. manager prints what the
holder reads as information, between two copies of the data rule: each goal by its instruction's seq, with whether its
signature verifies against the owner keys the session pinned, and never a goal's words, which only take prints, under
the rule take keeps. manager-act sends one act, which the app checks before the session does it, and says the app's
why where it refuses."""

import re
import time
import uuid

import pytest

import ade_link
from proofpane_open import store
from test_ade_link_client import link_row, run, served  # noqa: F401  (served: a fixture)
from test_ade_link_client_commands import device, owner, pinned_dir, signed  # noqa: F401  (the fixtures)

C = "/api/ade/controller"
GOAL = "Make the README say hello.\n\nKeep the tests passing."
PART = "Make the README say hello."
WEEK = 7 * 86400


@pytest.fixture(autouse=True)
def workspace(monkeypatch):
    """The owner has a workspace, as on the hosted site (the local provider gives an admin one, and runs none here)."""
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)


def client_run(capsys, *argv):
    """The client's exit code and output; an argument it does not know is argparse's exit, its code returned."""
    try:
        return run(capsys, *argv)
    except SystemExit as e:
        out, err = capsys.readouterr()
        return e.code, out, err


def nonce(client, owner, purpose):
    n = client.post("/api/ade/link/nonce", headers=owner, json={"purpose": purpose})
    assert n.status_code == 200, n.text
    return n.json()


def make_holder(client, owner, device, link_id):
    n = nonce(client, owner, "manager")
    digest = client.get(f"{C}/holders", headers=owner).json()["settings_digest"]
    p = {"v": 2, "deployment": n["deployment"], "purpose": "manager", "account": n["account"], "nonce": n["nonce"],
         "id": uuid.uuid4().hex, "to": link_id, "settings": digest, "created_at": n["server_time"],
         "expires_at": n["server_time"] + WEEK}
    r = client.post(f"{C}/holder", headers=owner, json={"payload": p, "credential": signed(device, p)})
    assert r.status_code == 200, r.text


def send_goal(client, owner, device, link_id, text=GOAL):
    n = nonce(client, owner, "instruction")
    p = {"v": 2, "deployment": n["deployment"], "purpose": "instruction", "account": n["account"], "nonce": n["nonce"],
         "id": uuid.uuid4().hex, "to": link_id, "text": text, "created_at": n["server_time"],
         "expires_at": n["server_time"] + 3600}
    r = client.post(f"{C}/requests", headers=owner, json={"payload": p, "credential": signed(device, p)})
    assert r.status_code == 200, r.text
    return r.json()


def holding(client, owner, served, device, capsys, tmp_path, take=True):
    """A pinned link that holds the role, a goal signed to it and taken with the client's own take, as a session takes
    one before it plans from it, and the Manager on: (its directory, the goal's request)."""
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    lane = link_row("mac")
    make_holder(client, owner, device, lane["id"])
    request = send_goal(client, owner, device, lane["id"])
    if take:
        code, out, err = client_run(capsys, "--dir", d, "take", request["instruction"])
        assert code == 0, (out, err)
    assert client.post(f"{C}/on", headers=owner).status_code == 200
    return d, request


def test_manager_prints_each_goal_by_its_seq_verified_and_never_its_words(client, owner, served, device, capsys,
                                                                         tmp_path):
    d, request = holding(client, owner, served, device, capsys, tmp_path)

    code, out, err = client_run(capsys, "--dir", d, "manager")

    assert code == 0, err
    lines = out.splitlines()
    assert (lines[0], lines[-1]) == (ade_link.RULE, ade_link.RULE)
    assert all(line.startswith(ade_link.MANAGER_MARK) for line in lines[1:-1])
    [goal] = [line for line in lines if f"goal [{request['instruction']}]" in line]
    assert "verified from the owner" in goal and f"ade_link.py take {request['instruction']}" in goal
    assert "README" not in out                          # a goal's words: take's alone to print
    assert "own machine" in out


def test_a_goal_whose_stored_payload_was_changed_is_not_verified(client, owner, served, device, capsys, tmp_path):
    d, request = holding(client, owner, served, device, capsys, tmp_path, take=False)
    seq = request["instruction"]
    with store.connect() as db:
        p = db.execute("SELECT payload FROM ade_link_events WHERE seq = ?", (seq,)).fetchone()[0]
        db.execute("UPDATE ade_link_events SET payload = ? WHERE seq = ?", (p.replace("README", "Makefile"), seq))

    code, out, err = client_run(capsys, "--dir", d, "manager")

    assert code == 0, err
    [goal] = [line for line in out.splitlines() if f"goal [{seq}]" in line]
    assert "NOT VERIFIED" in goal and "verified from the owner" not in goal


def test_manager_is_refused_for_a_session_that_does_not_hold_the_role(client, owner, served, device, capsys,
                                                                     tmp_path):
    d = pinned_dir(client, owner, served, capsys, tmp_path)

    code, out, err = client_run(capsys, "--dir", d, "manager")

    assert (code, "refused (403)" in err) == (2, True), (code, out, err)


def test_manager_act_plan_is_taken_and_names_the_requirement_it_made(client, owner, served, device, capsys,
                                                                    tmp_path):
    d, request = holding(client, owner, served, device, capsys, tmp_path)
    words = tmp_path / "words.txt"
    words.write_text(PART, encoding="utf-8")

    code, out, err = client_run(capsys, "--dir", d, "manager-act", "plan", "--request", request["id"], "--words",
                                words, "--title", "Say hello", "--repos", "demo")

    assert code == 0, err
    assert out.startswith("ADE: taken: plan ") and ", its requirement " in out
    rid = out.strip().rsplit(" ", 1)[-1]
    assert client.get(f"{C}/requirements/{rid}", headers=owner).status_code == 200


def test_manager_act_refused_says_the_apps_why(client, owner, served, device, capsys, tmp_path):
    d, request = holding(client, owner, served, device, capsys, tmp_path)
    words = tmp_path / "words.txt"
    words.write_text(PART, encoding="utf-8")
    planned = client_run(capsys, "--dir", d, "manager-act", "plan", "--request", request["id"], "--words", words,
                         "--title", "Say hello", "--repos", "demo")
    assert planned[0] == 0, planned
    rid = planned[1].strip().rsplit(" ", 1)[-1]

    code, out, err = client_run(capsys, "--dir", d, "manager-act", "start", "--requirement", rid, "--repo", "demo",
                                "--model", "sonnet", "--effort", "max")

    assert (code, "refused (409)" in err, "a model the owner did not allow" in err) == (2, True, True), (out, err)


def test_manager_act_sends_nothing_without_what_its_kind_needs(client, owner, served, device, capsys, tmp_path):
    d, request = holding(client, owner, served, device, capsys, tmp_path)

    code, out, err = client_run(capsys, "--dir", d, "manager-act", "plan", "--request", request["id"])

    assert (code, "needs --words, --title, --repos" in err) == (2, True), (out, err)
    assert client.get(C, headers=owner).json()["requirements"] == []


def test_a_session_plans_from_what_manager_printed_alone(client, owner, served, device, capsys, tmp_path):
    """A plan names the goal's request by its id, which manager prints beside its seq; a
    session that follows the guide plans from what manager printed, with nothing the owner's page saw."""
    d, request = holding(client, owner, served, device, capsys, tmp_path)
    code, out, err = client_run(capsys, "--dir", d, "manager")
    assert code == 0, err
    [goal] = [line for line in out.splitlines() if f"goal [{request['instruction']}]" in line]
    named = re.findall(r"\(its id ([0-9a-f]{32})\)", goal)
    words = tmp_path / "words.txt"
    words.write_text(PART, encoding="utf-8")

    code, out, err = client_run(capsys, "--dir", d, "manager-act", "plan", "--request", (named or ["none"])[0],
                                "--words", words, "--title", "Say hello", "--repos", "demo")

    assert code == 0, (goal, out, err)
    assert named == [request["id"]]


def test_manager_says_end_all_was_pressed_while_the_session_held_the_role(client, owner, served, device, capsys,
                                                                          tmp_path):
    """End all stops a linked Manager's session through its link, and manager says End all
    was pressed while it held the role, so that the session, once the owner resumes it, stops what it started."""
    d, request = holding(client, owner, served, device, capsys, tmp_path)
    assert client.post(f"{C}/end-all", headers=owner).status_code == 200

    code, out, err = client_run(capsys, "--dir", d, "manager")

    assert code == 0, err
    said = [line for line in out.splitlines() if "End all" in line]
    assert (len(said), "stop each task" in "".join(said)) == (1, True), out


def test_manager_says_nothing_of_an_end_all_pressed_before_the_session_held_the_role(client, owner, served, device,
                                                                                      capsys, tmp_path):
    """A control: End all pressed before this hold, a minute before here, is none of this session's to act
    on."""
    from proofpane_open.ade import controller
    user = {"id": client.get("/api/auth/me", headers=owner).json()["id"]}
    controller.end_all(user, now=int(time.time()) - 60)
    d, request = holding(client, owner, served, device, capsys, tmp_path)

    code, out, err = client_run(capsys, "--dir", d, "manager")

    assert code == 0, err
    assert "End all" not in out, out
