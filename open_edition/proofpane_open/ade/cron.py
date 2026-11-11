"""A schedule's next times, from a cron line and a timezone: five fields (minute, hour, day of the month, month, day of
the week), each `*`, a number, a range `a-b`, a step `*/n` or `a-b/n`, or a list of those; months and days of the
week by number (a day of the week 0 to 7, both 0 and 7 Sunday). As cron reads them, a day of the month and a day of
the week both given match either.

The next time is found in the schedule's own timezone and given back in UTC: a local time the clock skips (the hour
lost when summer time starts) is not a firing; one it passes twice (the hour gained when it ends) fires once, at its
first.
"""

import datetime
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FIELDS = (("minute", 0, 59), ("hour", 0, 23), ("day of the month", 1, 31), ("month", 1, 12), ("day of the week", 0, 7))
_PART = re.compile(r"^(\*|\d{1,2}(?:-\d{1,2})?)(?:/(\d{1,2}))?$")
MAX_LINE = 100
SEARCH_DAYS = 4 * 366           # a line that matches no time in four years (31 February) has none


class CronError(ValueError):
    pass


def _field(text, name, low, high):
    out = set()
    for part in text.split(","):
        m = _PART.match(part)
        if not m:
            raise CronError(f"The {name} is a number, a range, a step or *, or a list of them.")
        span, step = m.group(1), int(m.group(2) or 1)
        if step < 1:
            raise CronError(f"A step of the {name} is at least 1.")
        if span == "*":
            first, last = low, high
        elif "-" in span:
            first, last = (int(x) for x in span.split("-"))
        else:
            first = last = int(span)
            if m.group(2):
                last = high
        if not (low <= first <= last <= high):
            raise CronError(f"The {name} is from {low} to {high}.")
        out.update(range(first, last + 1, step))
    return out


class Schedule:
    """A cron line in a timezone, checked."""

    def __init__(self, line, tz):
        if not isinstance(line, str) or len(line) > MAX_LINE:
            raise CronError("A schedule is a cron line of five fields.")
        parts = line.split()
        if len(parts) != 5:
            raise CronError("A schedule is a cron line of five fields: minute, hour, day of the month, month, day of "
                            "the week.")
        try:
            self.zone = ZoneInfo(tz) if isinstance(tz, str) and tz else None
        except (ZoneInfoNotFoundError, ValueError):
            self.zone = None
        if self.zone is None:
            raise CronError("A schedule's timezone is one the IANA database names, as Pacific/Auckland.")
        self.line, self.tz = " ".join(parts), tz
        self.minutes, self.hours, self.days, self.months, weekdays = (
            _field(text, *spec) for text, spec in zip(parts, FIELDS))
        self.weekdays = {d % 7 for d in weekdays}
        self.any_day, self.any_weekday = parts[2] == "*", parts[4] == "*"

    def _day_matches(self, d):
        weekday = (d.weekday() + 1) % 7           # cron's Sunday is 0; Python's Monday is 0
        if self.any_day or self.any_weekday:
            return d.day in self.days and weekday in self.weekdays
        return d.day in self.days or weekday in self.weekdays

    def next_after(self, when):
        """The first firing strictly after `when` (a UTC datetime, or a unix time), as a unix time; None when the line
        matches no time in four years."""
        if not isinstance(when, datetime.datetime):
            when = datetime.datetime.fromtimestamp(when, datetime.timezone.utc)
        after = when.timestamp()
        local = when.astimezone(self.zone).replace(second=0, microsecond=0, tzinfo=None) + datetime.timedelta(minutes=1)
        end = local + datetime.timedelta(days=SEARCH_DAYS)
        hours, minutes = sorted(self.hours), sorted(self.minutes)
        day = local.date()
        while datetime.datetime.combine(day, datetime.time()) <= end:
            if day.month in self.months and self._day_matches(day):
                for h in hours:
                    for m in minutes:
                        t = datetime.datetime.combine(day, datetime.time(h, m))
                        if t < local:
                            continue
                        if not self._exists(t):
                            continue
                        instant = int(t.replace(tzinfo=self.zone, fold=0).timestamp())
                        if instant <= after:
                            # its first pass is behind: in the hour summer time repeats, `when` may be in the second
                            continue
                        return instant
            day += datetime.timedelta(days=1)
        return None

    def _exists(self, t):
        """Whether the local time `t` is on the clock: not in the hour summer time skips."""
        there = t.replace(tzinfo=self.zone, fold=0)
        back = there.astimezone(datetime.timezone.utc).astimezone(self.zone).replace(tzinfo=None)
        return back == t
