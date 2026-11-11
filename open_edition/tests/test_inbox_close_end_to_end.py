"""The owner inbox's Close, end to end:
the prompt hook run as Claude Code runs it, against the app served on a local port, with the owner acting
in the page while it waits (test_ade_link_permission's owner_page). The owner's Close hides a session's prompt and
gives nothing up: the prompt leaves the open list for the list of closed and hidden prompts, the hook goes on waiting,
and an answer given from that list is taken as before; a prompt nobody answers is refused at its deadline with fixed
words naming it, and the session's turn goes on. What each act sees is kept and asserted after the hook returns,
since an assertion that failed inside an act would be swallowed by the hook, which prints nothing on any error."""

import time

import ade_link
from test_ade_link_client import served  # noqa: F401  (served: a fixture)
from test_ade_link_permission import (at_the_hook, decision, inbox, owner_answers, owner_page,  # noqa: F401
                                      prompt_asking, row, set_deadline)


def closed_list(o):
    """The page's list of closed and hidden prompts: (status, body)."""
    r = o.client.get("/api/ade/link/prompts/closed", headers=o.headers)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text


def listed(body, seq):
    """The closed list's item for `seq`, or None."""
    items = [p for p in body.get("prompts") or [] if p.get("seq") == seq] if isinstance(body, dict) else []
    return items[0] if len(items) == 1 else None


def the_owner_closes(o, seen):
    """The owner's Close on the newest prompt in his open list, and what the page reads right after it."""
    def act():
        (p,) = inbox(o)["prompts"]
        seen["prompt"] = p
        r = o.client.post(f"/api/ade/link/questions/{p['seq']}/close", headers=o.headers)
        seen["close"] = (r.status_code, r.json())
        listed_open = inbox(o)
        seen["open_after"] = [q["seq"] for q in listed_open.get("prompts") or []]
        seen["hidden_waiting_after"] = listed_open.get("hidden_waiting")
        seen["closed_after"] = closed_list(o)
        seen["sent_at_close"] = len(o.sent)
    return act


def test_a_closed_prompt_leaves_the_open_list_the_hook_goes_on_waiting_and_an_answer_from_the_closed_list_is_taken(
        owner_page, monkeypatch, capsys):  # noqa: F811
    o = owner_page
    seen = {}

    def the_owner_answers_from_the_closed_list():
        seen["sent_at_answer"] = len(o.sent)
        _, body = closed_list(o)
        p = listed(body, seen["prompt"]["seq"])
        r = owner_answers(o, p["seq"], p["text"], "Approve")
        seen["answer"] = r.status_code
    o.acts += [the_owner_closes(o, seen), the_owner_answers_from_the_closed_list]
    code, out, err = at_the_hook(o, monkeypatch, capsys, prompt_asking(o))
    seq = seen["prompt"]["seq"]
    held = row(seq)
    # the Close decided nothing and closed nothing: it recorded who hid the prompt and when
    assert seen["close"] == (200, {"seq": seq, "hidden_at": held.get("hidden_at")})
    assert isinstance(held["hidden_at"], int) and held["hidden_by"] == held["user_id"]
    # out of the open list, named among the hidden prompts still waiting, and in the closed list as hidden and
    # waiting until its deadline
    assert seq not in seen["open_after"] and seen["hidden_waiting_after"] == [seq]
    status, body = seen["closed_after"]
    item = listed(body, seq)
    assert status == 200 and item is not None, body
    assert (item["open"], item["ended"], item["closed_reason"], item["hidden_at"]) == (True, "waiting", None,
                                                                                       held["hidden_at"])
    # the hook read its prompt again after the Close, still waiting, and took the answer given from the closed list
    reads = [i for i, sent in enumerate(o.sent) if sent == ("GET", f"/events/{seq}")]
    assert any(seen["sent_at_close"] <= i < seen["sent_at_answer"] for i in reads), o.sent
    assert seen["answer"] == 200
    assert (code, err) == (0, "") and decision(out) == {"behavior": "allow"}
    assert held["closed_reason"] == "answer" and row(held["closed_by"])["taken_by"] == o.link_id
    status, body = closed_list(o)
    item = listed(body, seq)
    assert (item["open"], item["ended"], item["closed_reason"]) == (False, "approved", "answer")
    assert inbox(o).get("hidden_waiting") == []


def test_a_closed_prompt_nobody_answers_is_refused_at_its_deadline_with_words_naming_it_and_the_turn_goes_on(
        owner_page, monkeypatch, capsys):  # noqa: F811
    o = owner_page
    seen = {}

    def its_deadline_comes():
        seen["deadline_at"] = seen["prompt"]["deadline_at"]
        set_deadline(seen["prompt"]["seq"], int(time.time()) - 1)
    o.acts += [the_owner_closes(o, seen), its_deadline_comes]
    code, out, err = at_the_hook(o, monkeypatch, capsys, prompt_asking(o))
    seq = seen["prompt"]["seq"]
    held = row(seq)
    assert seen["close"] == (200, {"seq": seq, "hidden_at": held.get("hidden_at")})
    assert (code, err) == (0, "")
    got = decision(out)
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(seen["deadline_at"]))
    # refused with words naming the prompt's deadline, and without interrupt: the session's turn goes on
    assert got["behavior"] == "deny" and "interrupt" not in got and when in got["message"], got
    assert got == {"behavior": "deny", "message": ade_link.PROMPT_UNANSWERED.format(when=when)}
    assert held["closed_reason"] == "deadline" and held["hidden_at"] is not None
    status, body = closed_list(o)
    item = listed(body, seq)
    assert status == 200 and (item["open"], item["ended"]) == (False, "deadline"), body
