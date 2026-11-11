"""Community's approval loop: a request a hook keeps sending stays open while it does, as a device's hold is renewed: an
identical send renews it to that send's own end (an hour on, or the end of the window it names), never back, and never
past a day from when it was made; one past its end is not renewed, and the next send makes a request of its own."""

import pytest

from proofpane_open.ade import approvals

T0 = 1_790_000_000
HOUR = 3600


@pytest.fixture
def hook():
    _, token = approvals.create_token("u1", "the Mac session", now=T0)
    return approvals.hook_identity(token, now=T0)


def test_a_request_sent_again_within_its_hour_is_renewed_and_stays_open_past_its_first_end(hook):
    first = approvals.request(hook, "git push origin work", now=T0)

    again = approvals.request(hook, "git push origin work", now=T0 + 50 * 60)
    later = approvals.request(hook, "git push origin work", now=T0 + 70 * 60)

    assert (again["id"], again["until"]) == (first["id"], T0 + 50 * 60 + HOUR), again
    assert (later["id"], later["state"]) == (first["id"], "pending"), later


def test_a_renewal_never_takes_a_request_past_a_day_from_when_it_was_made(hook):
    first = approvals.request(hook, "git push origin work", now=T0)
    t = T0
    while t + 50 * 60 < T0 + 24 * HOUR:
        t += 50 * 60
        assert approvals.request(hook, "git push origin work", now=t)["id"] == first["id"]

    last = approvals.request(hook, "git push origin work", now=t)
    after = approvals.request(hook, "git push origin work", now=T0 + 24 * HOUR + 1)

    assert last["until"] == T0 + 24 * HOUR, last
    assert after["id"] != first["id"], after


def test_a_renewal_never_moves_a_request_back(hook):
    """A send naming a window that ends sooner leaves the request's end where it was."""
    first = approvals.request(hook, "git push origin work", now=T0)

    again = approvals.request(hook, "git push origin work", until=T0 + 600, now=T0 + 60)

    assert (again["id"], again["until"]) == (first["id"], T0 + HOUR), again


def test_a_request_past_its_end_is_not_renewed_and_the_next_send_is_a_request_of_its_own(hook):
    first = approvals.request(hook, "git push origin work", now=T0)

    after = approvals.request(hook, "git push origin work", now=T0 + HOUR + 1)

    assert after["id"] != first["id"]
    assert [r["state"] for r in approvals.list_for_owner("u1", now=T0 + HOUR + 1)
            if r["id"] == first["id"]] == ["expired"]


# ---- guards of what a renewal does -----------------------------------------------------------------------------------

def test_a_renewed_request_is_still_the_owners_to_approve_past_its_first_end_and_runs_once(hook):
    """The renewal from the owner's side: renewed, the request is his to approve after its first end, and
    the next send takes that approval once."""
    first = approvals.request(hook, "git push origin work", now=T0)
    approvals.request(hook, "git push origin work", now=T0 + 50 * 60)

    approvals.decide("u1", first["id"], True, "go", now=T0 + 70 * 60)

    taken = approvals.request(hook, "git push origin work", now=T0 + 71 * 60)
    again = approvals.request(hook, "git push origin work", now=T0 + 72 * 60)
    assert (taken["id"], taken["state"]) == (first["id"], "approved"), taken
    assert again["id"] != first["id"] and again["state"] == "pending", again


def test_what_is_not_pending_is_never_renewed(hook):
    """A denied request, a read of its state by the hook, and a send at its very end renew nothing."""
    denied = approvals.request(hook, "rm -rf build", now=T0)
    approvals.decide("u1", denied["id"], False, now=T0 + 10)
    read = approvals.request(hook, "git push origin work", now=T0)

    after_denial = approvals.request(hook, "rm -rf build", now=T0 + 20)
    approvals.read_for_hook(hook, read["id"], now=T0 + 30)
    at_end = approvals.request(hook, "git push origin work", now=T0 + HOUR)

    rows = {r["id"]: r for r in approvals.list_for_owner("u1", now=T0 + HOUR)}
    assert after_denial["id"] != denied["id"] and rows[denied["id"]]["until"] == T0 + HOUR
    assert at_end["id"] != read["id"] and rows[read["id"]]["until"] == T0 + HOUR


def test_a_request_sent_in_a_window_ends_with_it_and_a_later_window_extends_it(hook):
    """The hook names its window each time it sends; a send in the same window leaves the request at that
    window's end, and one in the next window takes it to the next window's end, within the day."""
    first = approvals.request(hook, "git push origin work", until=T0 + 1800, now=T0)

    same = approvals.request(hook, "git push origin work", until=T0 + 1800, now=T0 + 600)
    next_window = approvals.request(hook, "git push origin work", until=T0 + 1800 + 5 * HOUR, now=T0 + 1700)

    assert (same["id"], same["until"]) == (first["id"], T0 + 1800), same
    assert (next_window["id"], next_window["until"]) == (first["id"], T0 + 1800 + 5 * HOUR), next_window
