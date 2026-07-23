"""Fiscal calendar arithmetic — the boring code that silently breaks year ends."""

from __future__ import annotations

from datetime import date
from itertools import pairwise

from intus_gen.fiscal import period_for, periods_between, quarter_for


def test_period_key_format():
    assert period_for(date(2026, 3, 9)) == "FY2026-M03"
    assert period_for(date(2025, 12, 31)) == "FY2025-M12"


def test_quarter_boundaries():
    assert quarter_for(date(2026, 1, 1)) == "FY2026-Q1"
    assert quarter_for(date(2026, 3, 31)) == "FY2026-Q1"
    assert quarter_for(date(2026, 4, 1)) == "FY2026-Q2"
    assert quarter_for(date(2026, 12, 31)) == "FY2026-Q4"


def test_periods_span_partial_months_whole():
    """Clipping the edges would read as underspend rather than truncation."""
    periods = periods_between(date(2026, 1, 20), date(2026, 3, 5))
    assert [period.key for period in periods] == ["FY2026-M01", "FY2026-M02", "FY2026-M03"]
    assert periods[0].start_date == date(2026, 1, 1)
    assert periods[-1].end_date == date(2026, 3, 31)


def test_periods_cross_a_year_boundary():
    periods = periods_between(date(2025, 11, 15), date(2026, 2, 1))
    assert [period.key for period in periods] == [
        "FY2025-M11",
        "FY2025-M12",
        "FY2026-M01",
        "FY2026-M02",
    ]


def test_month_end_is_correct_including_february():
    periods = {
        period.key: period for period in periods_between(date(2024, 1, 1), date(2024, 12, 31))
    }
    assert periods["FY2024-M02"].end_date == date(2024, 2, 29)  # leap year
    assert periods["FY2024-M04"].end_date == date(2024, 4, 30)
    assert periods["FY2024-M12"].end_date == date(2024, 12, 31)

    non_leap = {
        period.key: period for period in periods_between(date(2026, 2, 1), date(2026, 2, 28))
    }
    assert non_leap["FY2026-M02"].end_date == date(2026, 2, 28)


def test_single_month_range():
    periods = periods_between(date(2026, 6, 5), date(2026, 6, 20))
    assert len(periods) == 1
    assert periods[0].fiscal_quarter == 2


def test_periods_are_contiguous():
    from datetime import timedelta

    periods = periods_between(date(2025, 1, 1), date(2026, 6, 30))
    for earlier, later in pairwise(periods):
        assert earlier.end_date + timedelta(days=1) == later.start_date
