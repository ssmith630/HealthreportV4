"""Quarter windows for the report.

IBM's fiscal year equals the calendar year, so Q1 = Jan-Mar and Q4 = Oct-Dec.

The report covers two windows: Q4 of year Y and Q1 of year Y+1.
The default Y is the current year, or the previous year during Jan-Mar so that
the "current" Q1 is always paired with the Q4 that precedes it.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Window:
    label: str      # e.g. "Q4 2026"
    start: date
    end: date

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end


def _quarter(year: int, q: int) -> Window:
    first_month = (q - 1) * 3 + 1
    last_month = first_month + 2
    last_day = calendar.monthrange(year, last_month)[1]
    return Window(f"Q{q} {year}", date(year, first_month, 1), date(year, last_month, last_day))


def default_year(today: date | None = None) -> int:
    today = today or date.today()
    return today.year if today.month >= 4 else today.year - 1


def report_windows(year: int | None = None) -> list[Window]:
    """Return [Q4 of `year`, Q1 of `year + 1`]."""
    y = year if year is not None else default_year()
    return [_quarter(y, 4), _quarter(y + 1, 1)]


ROLES = ("Current", "Next")


def three_quarter_windows(today: date | None = None) -> list[Window]:
    """Return [previous, current, next] quarter relative to `today`, crossing year ends correctly."""
    today = today or date.today()
    index = today.year * 4 + (today.month - 1) // 3
    return [_quarter(i // 4, i % 4 + 1) for i in (index - 1, index, index + 1)]


def current_next_windows(today: date | None = None) -> list[Window]:
    """Return [current, next] quarter relative to `today`: the quarters the opportunity view covers."""
    return three_quarter_windows(today)[1:]


def merge_windows(*groups: list[Window]) -> list[Window]:
    """Union of window lists, de-duplicated and sorted by start date."""
    unique = {w.label: w for g in groups for w in g}
    return sorted(unique.values(), key=lambda w: w.start)


def quarter_label(d: date, windows: list[Window]) -> str:
    """Label of the window holding `d`; any other date gets its own calendar-quarter label."""
    for w in windows:
        if w.contains(d):
            return w.label
    return f"Q{(d.month - 1) // 3 + 1} {d.year}"
