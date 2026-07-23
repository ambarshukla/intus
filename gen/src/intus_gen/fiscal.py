"""Halcyon's fiscal calendar.

Halcyon's fiscal year matches the calendar year. That is a deliberate
simplification: an offset fiscal year (many of Halcyon's real-world peers close
in January or September) would add a translation layer to every finance query
without exercising anything the warehouse phase is trying to demonstrate. The
period key is kept explicit and opaque (``FY2026-M03``) rather than derived at
query time from a date, so that if a later phase *does* introduce an offset
year, exactly one function changes and no report has to be rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

_MONTHS_PER_QUARTER = 3
_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class Period:
    """One fiscal month — the grain budgets and actuals are posted at."""

    key: str  # e.g. FY2026-M03
    fiscal_year: int
    fiscal_quarter: int  # 1-4
    month: int  # 1-12
    start_date: date
    end_date: date  # inclusive


def period_for(day: date) -> str:
    """The fiscal period key a date falls in."""
    return f"FY{day.year}-M{day.month:02d}"


def quarter_for(day: date) -> str:
    """The fiscal quarter key a date falls in."""
    return f"FY{day.year}-Q{(day.month - 1) // _MONTHS_PER_QUARTER + 1}"


def _last_day_of_month(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - _ONE_DAY


def periods_between(start: date, end: date) -> tuple[Period, ...]:
    """Every fiscal period touching the inclusive range ``[start, end]``.

    Partial months at either edge are included whole. A budget is set for a
    month or not at all, so clipping the first and last period would produce
    variance figures that look like underspend but are only truncation.
    """
    periods: list[Period] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        periods.append(
            Period(
                key=f"FY{year}-M{month:02d}",
                fiscal_year=year,
                fiscal_quarter=(month - 1) // _MONTHS_PER_QUARTER + 1,
                month=month,
                start_date=date(year, month, 1),
                end_date=_last_day_of_month(year, month),
            )
        )
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return tuple(periods)
