"""Unit tests for parity.py's comparison core — pure functions, no network or
database calls, so these run in every environment including one with no
DATABRICKS_HOST or live Postgres at all.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from intus_lakehouse.parity import compare_view

_COLUMNS = ("department_code", "headcount", "as_of")


def test_identical_rows_match():
    rows = (
        ("ENG", 42, date(2026, 1, 1)),
        ("SLS", 17, date(2026, 1, 1)),
    )
    result = compare_view("v", _COLUMNS, rows, _COLUMNS, rows)
    assert result.ok


def test_decimal_and_int_compare_equal_when_numerically_equal():
    """Postgres numeric arrives as Decimal; Databricks' typed converter can
    hand back int or float for the same logical value — parity must not
    report a mismatch purely because of which Python type carried it.
    """
    warehouse_rows = (("ENG", Decimal("42"), date(2026, 1, 1)),)
    gold_rows = (("ENG", 42, date(2026, 1, 1)),)
    result = compare_view("v", _COLUMNS, warehouse_rows, _COLUMNS, gold_rows)
    assert result.ok


def test_row_order_does_not_matter():
    warehouse_rows = (
        ("ENG", 42, date(2026, 1, 1)),
        ("SLS", 17, date(2026, 1, 1)),
    )
    gold_rows = (
        ("SLS", 17, date(2026, 1, 1)),
        ("ENG", 42, date(2026, 1, 1)),
    )
    result = compare_view("v", _COLUMNS, warehouse_rows, _COLUMNS, gold_rows)
    assert result.ok


def test_tiny_float_rounding_noise_is_tolerated():
    """The exact failure mode this tolerance exists for: Databricks'
    round(DOUBLE, 1) landing on 33.30000000000001 instead of 33.3.
    """
    result = compare_view(
        "v",
        ("pct",),
        ((Decimal("33.3"),),),
        ("pct",),
        ((33.30000000000001,),),
    )
    assert result.ok


def test_a_real_numeric_disagreement_is_caught():
    result = compare_view(
        "v",
        ("pct",),
        ((Decimal("33.3"),),),
        ("pct",),
        ((99.9,),),
    )
    assert not result.ok
    assert len(result.diffs) == 1


def test_null_only_matches_null():
    result = compare_view(
        "v",
        ("headcount",),
        ((None,),),
        ("headcount",),
        ((0,),),
    )
    assert not result.ok


def test_row_count_mismatch_is_not_ok_even_with_a_matching_prefix():
    warehouse_rows = (("ENG", 42, date(2026, 1, 1)),)
    gold_rows = (
        ("ENG", 42, date(2026, 1, 1)),
        ("SLS", 17, date(2026, 1, 1)),
    )
    result = compare_view("v", _COLUMNS, warehouse_rows, _COLUMNS, gold_rows)
    assert not result.ok
    assert result.warehouse_row_count == 1
    assert result.gold_row_count == 2


def test_column_mismatch_is_reported_without_comparing_rows():
    result = compare_view(
        "v",
        ("a", "b"),
        ((1, 2),),
        ("a", "c"),
        ((1, 2),),
    )
    assert not result.ok
    assert result.column_mismatch


def test_matching_dates_from_both_platforms_compare_equal():
    """psycopg hands back a real ``date``; databricks_source's DATE converter
    (``date.fromisoformat``) hands back the same type — this is the realistic
    pairing, not a date-vs-timestamp mix that none of the seven views produce.
    """
    result = compare_view(
        "v",
        ("as_of",),
        ((date(2026, 1, 1),),),
        ("as_of",),
        ((date(2026, 1, 1),),),
    )
    assert result.ok
