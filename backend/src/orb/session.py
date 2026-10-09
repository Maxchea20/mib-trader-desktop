"""America/New_York session clock.

9:30 is never converted with a fixed UTC offset. zoneinfo applies the
US daylight-saving rules for the specific local date.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UTC = timezone.utc

SESSION_OPEN = time(9, 30, 0)
DEFAULT_ENTRY_CUTOFF = time(11, 0, 0)

# Full NYSE cash closures. Early-close days still open at 09:30 and remain
# eligible: the ORB entry window ends at 11:00, before the 13:00 early close.
# Source: NYSE Group holiday calendar announced for 2025, 2026 and 2027.
NYSE_FULL_CLOSURES = frozenset(
    {
        date(2025, 1, 1),
        date(2025, 1, 20),
        date(2025, 2, 17),
        date(2025, 4, 18),
        date(2025, 5, 26),
        date(2025, 6, 19),
        date(2025, 7, 4),
        date(2025, 9, 1),
        date(2025, 11, 27),
        date(2025, 12, 25),
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
        date(2027, 1, 1),
        date(2027, 1, 18),
        date(2027, 2, 15),
        date(2027, 3, 26),
        date(2027, 5, 31),
        date(2027, 6, 18),
        date(2027, 7, 5),
        date(2027, 9, 6),
        date(2027, 11, 25),
        date(2027, 12, 24),
    }
)

ORB_MINUTES = {"ORB-5": 5, "ORB-15": 15, "ORB-30": 30}


def is_nyse_session_day(day: date) -> bool:
    """Weekends and full NYSE closures are not a stock-market opening session."""
    return day.weekday() < 5 and day not in NYSE_FULL_CLOSURES


def session_open_local(day: date) -> datetime:
    return datetime.combine(day, SESSION_OPEN, tzinfo=NY)


def session_open_utc(day: date) -> datetime:
    return session_open_local(day).astimezone(UTC)


def to_utc_ts(moment: datetime) -> int:
    return int(round(moment.timestamp()))


def ny_date_of_utc_ts(ts: int) -> date:
    return datetime.fromtimestamp(int(ts), UTC).astimezone(NY).date()


def range_bounds_ts(day: date, orb_minutes: int) -> tuple[int, int]:
    start = session_open_local(day)
    end = start + timedelta(minutes=int(orb_minutes))
    return to_utc_ts(start), to_utc_ts(end)


def entry_cutoff_ts(day: date, cutoff: time = DEFAULT_ENTRY_CUTOFF) -> int:
    return to_utc_ts(datetime.combine(day, cutoff, tzinfo=NY))
