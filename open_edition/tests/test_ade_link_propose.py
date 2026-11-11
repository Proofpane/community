"""The owner's inbox, a linked session's proposal end to end (open_edition/ade_link.py propose): a manager session the
owner lets propose sends a message for another linked
session, sealed to the owner; the owner opens it with the account's keys and signs it as his own instruction to the
session it names, its first line naming the proposer; that session's watcher wakes it, its take verifies the owner's
signature, and it replies done. Against the app served on a local port, with encryption turned on as the page turns
it on (test_ade_link_permission's owner_page: the manager is its session, mac)."""

import ade_link
from e2e_link_stand_in import slot
from proofpane_core.canonical import canonicalize
from proofpane_open import store
from test_ade_link_client import run, served  # noqa: F401  (served: a fixture)
from test_ade_link_permission import Clock, command, inbox, owner_page  # noqa: F401  (owner_page: a fixture)
from test_e2e_end_to_end import opened as page_opened, paired
from test_passkeys import as_step

WORDS = "Rebase the release branch on main and run the unit tests,\nthen reply with how many passed."


def instruction(o, to, text, payload_id):
    """The owner's instruction, signed with his passkey as the page signs one; the route's answer."""
    n = o.client.post("/api/ade/link/nonce", headers=o.headers, json={"purpose": "instruction"}).json()
    p = {"v": 2, "deployment": n["deployment"], "purpose": "instruction", "account": o.page.account,
         "nonce": n["nonce"], "id": payload_id, "to": to, "text": text, "created_at": n["server_time"],
         "expires_at": n["server_time"] + 3600}
    return o.client.post("/api/ade/link/commands", headers=o.headers,
                         json={"payload": p, "credential": o.device.get(as_step(canonicalize(p)))})


def test_a_manager_s_proposal_reaches_the_owner_sealed_and_signed_it_wakes_the_session_it_names(
        owner_page, served, capsys, tmp_path, monkeypatch):  # noqa: F811
    o = owner_page
    worker, worker_id, _, _ = paired(o.page, served, capsys, tmp_path, "worker")
    words = tmp_path / "words.txt"
    words.write_text(WORDS)

    # the owner has not let the manager propose: the ADE refuses it, and nothing waits for him
    code, out, err = command(capsys, "--dir", o.dir, "propose", "--to", "worker", "--file", words)
    assert code == 2 and "The owner has not let this session propose." in err, (out, err)
    assert inbox(o).get("proposals") == []

    r = o.client.put(f"/api/ade/link/links/{o.link_id}/proposes", headers=o.headers, json={"allowed": True})
    assert r.status_code == 200 and r.json() == {"proposes": True}, r.text
    code, out, err = command(capsys, "--dir", o.dir, "propose", "--to", "worker", "--file", words)
    assert code == 0 and out.startswith("ADE: proposed as seq "), (out, err)
    seq = int(out.split()[-1])

    # it reaches the owner sealed, naming its proposer and the session it is for; the page opens it with his keys
    (proposal,) = [p for p in inbox(o)["proposals"] if p["seq"] == seq]
    assert proposal["link_id"] == o.link_id and proposal["for"] == "worker" and proposal["for_id"] == worker_id
    assert proposal["text"].startswith("e2e1.") and "Rebase" not in proposal["text"]
    opened = page_opened(o.page.unlock(), proposal["text"], slot(o.page.account, o.link_id, "event", "proposal",
                                                                 None, None, proposal["client_id"]))
    assert opened == {"text": WORDS, "session": None, "said_at": None}

    # signed for another session than the one it names, or without its proposer on its first line: refused
    signed_text = "Proposed by mac:\n" + opened["text"]
    r = instruction(o, o.link_id, signed_text, proposal["client_id"])
    assert r.status_code == 409 and "for another session" in r.json()["detail"], r.text
    r = instruction(o, worker_id, opened["text"], proposal["client_id"])
    assert r.status_code == 409, r.text
    # signed as the page signs it: the owner's own instruction to the worker, which closes the proposal
    r = instruction(o, worker_id, signed_text, proposal["client_id"])
    assert r.status_code == 200, r.text
    signed = r.json()["seq"]
    with store.connect() as db:
        closed = db.execute("SELECT closed_reason, closed_by FROM ade_link_events WHERE seq = ?", (seq,)).fetchone()
    assert (closed["closed_reason"], closed["closed_by"]) == ("signed", signed)

    # the worker's watcher wakes it for the owner's command; its take verifies it; it replies done
    clock = Clock()
    monkeypatch.setattr(ade_link, "clock", clock)
    monkeypatch.setattr(ade_link, "sleep", clock.sleep)
    code, out, err = run(capsys, "--dir", worker, "watch", "--max", 60, "--every", 30)
    assert code == 0 and f"ADE: command [{signed}] for you." in out, (out, err)
    code, out, err = command(capsys, "--dir", worker, "take", signed)
    assert code == 0 and "TAKEN BY YOU AND VERIFIED FROM THE OWNER: instruction " in out, (out, err)
    assert "  | Proposed by mac:\n  | Rebase the release branch on main and run the unit tests,\n" in out
    reply = tmp_path / "reply.txt"
    reply.write_text("212 passed.")
    code, out, err = command(capsys, "--dir", worker, "reply", signed, "--status", "done", "--file", reply)
    assert code == 0 and out.startswith("ADE: posted as seq "), (out, err)
    with store.connect() as db:
        done = db.execute("SELECT kind, ref, link_id FROM ade_link_events WHERE seq = ?",
                          (int(out.split()[-1]),)).fetchone()
    assert (done["kind"], done["ref"], done["link_id"]) == ("done", signed, worker_id)


def test_a_proposal_for_the_proposer_itself_or_no_live_session_is_refused(owner_page, served, capsys,
                                                                          tmp_path):  # noqa: F811
    o = owner_page
    paired(o.page, served, capsys, tmp_path, "worker")
    # the owner's switch on (the test above asserts its route)
    o.client.put(f"/api/ade/link/links/{o.link_id}/proposes", headers=o.headers, json={"allowed": True})
    words = tmp_path / "words.txt"
    words.write_text(WORDS)
    for to, why in (("mac", "A proposal is for another session than the one that proposes it."),
                    ("nobody", "A proposal is for one of the account's live linked sessions, by its name.")):
        code, out, err = command(capsys, "--dir", o.dir, "propose", "--to", to, "--file", words)
        assert code == 2 and f"refused (422): {why}" in err, (to, out, err)
    assert inbox(o).get("proposals") == []
    code, out, err = command(capsys, "--dir", o.dir, "propose", "--to", "worker", "--file", words)
    assert code == 0, (out, err)
    (proposal,) = inbox(o)["proposals"]
    assert proposal["for"] == "worker" and len(proposal["client_id"]) == 32
