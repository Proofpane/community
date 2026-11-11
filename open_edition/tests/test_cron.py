"""A schedule's next times (cron.py): cron's fields, its day rule, and a timezone's summer time."""

import datetime

import pytest

from proofpane_open.ade.cron import CronError, Schedule

UTC = datetime.timezone.utc


def after(line, tz, at):
    t = Schedule(line, tz).next_after(datetime.datetime.fromisoformat(at).replace(tzinfo=UTC))
    return datetime.datetime.fromtimestamp(t, UTC).isoformat() if t else None


@pytest.mark.parametrize("line, tz, at, want", [
    ("0 9 * * 1-5", "Pacific/Auckland", "2026-10-03T00:00:00", "2026-10-04T20:00:00+00:00"),   # Monday 09:00 NZDT
    ("*/15 * * * *", "UTC", "2026-10-03T00:07:30", "2026-10-03T00:15:00+00:00"),
    ("*/15 * * * *", "UTC", "2026-10-03T00:15:00", "2026-10-03T00:30:00+00:00"),            # strictly after
    ("5/20 3 * * *", "UTC", "2026-10-03T00:00:00", "2026-10-03T03:05:00+00:00"),
    ("5/20 3 * * *", "UTC", "2026-10-03T03:06:00", "2026-10-03T03:25:00+00:00"),            # 5, 25, 45
    ("30 2 * * *", "Pacific/Auckland", "2026-09-26T12:00:00", "2026-09-27T13:30:00+00:00"),  # 02:30 skipped on 27 Sep
    ("30 1 * * *", "America/New_York", "2026-11-01T04:00:00", "2026-11-01T05:30:00+00:00"),  # twice: the first
    ("30 1 * * *", "America/New_York", "2026-11-01T05:30:00", "2026-11-02T06:30:00+00:00"),  # and never the second
    # in the hour's second pass (01:10 EST), and late in its first (01:50 EDT): the next is after it, never before
    ("*/15 * * * *", "America/New_York", "2026-11-01T06:10:00", "2026-11-01T07:00:00+00:00"),
    ("*/15 * * * *", "America/New_York", "2026-11-01T05:50:00", "2026-11-01T07:00:00+00:00"),
    ("0 12 1 * 0", "UTC", "2026-10-01T13:00:00", "2026-10-04T12:00:00+00:00"),               # the 1st or a Sunday
    ("0 12 * * 7", "UTC", "2026-10-01T13:00:00", "2026-10-04T12:00:00+00:00"),               # 7 is Sunday too
    ("0 0 29 2 *", "UTC", "2026-03-01T00:00:00", "2028-02-29T00:00:00+00:00"),
    ("0 0 31 2 *", "UTC", "2026-01-01T00:00:00", None),
])
def test_the_next_time(line, tz, at, want):
    assert after(line, tz, at) == want


@pytest.mark.parametrize("line, tz", [("* * * *", "UTC"), ("61 * * * *", "UTC"), ("* * * * 8", "UTC"),
                                      ("a * * * *", "UTC"), ("*/0 * * * *", "UTC"), ("5-1 * * * *", "UTC"),
                                      ("* * * * *", "Mars/Olympus"), ("* * * * *", ""), (None, "UTC"),
                                      ("* " * 60, "UTC")])
def test_what_is_not_a_schedule(line, tz):
    with pytest.raises(CronError):
        Schedule(line, tz)
