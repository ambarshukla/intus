"""What the dimensions must be true of, regardless of the data behind them."""

from __future__ import annotations

from datetime import date, timedelta

import psycopg
import pytest

from intus_warehouse.load import load_directory
from intus_warehouse.transform import run


@pytest.fixture
def built(migrated_connection, extract):
    load_directory(migrated_connection, extract)
    run(migrated_connection)
    return migrated_connection


def _scalar(connection, sql: str, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


# --------------------------------------------------------------------------
# dim_date
# --------------------------------------------------------------------------


def test_date_dimension_is_contiguous(built):
    """A generated calendar has no holes; one derived from facts would."""
    gaps = _scalar(
        built,
        """
        SELECT count(*) FROM (
            SELECT full_date, lead(full_date) OVER (ORDER BY full_date) AS next_date
            FROM warehouse.dim_date
        ) AS windowed
        WHERE next_date IS NOT NULL AND next_date <> full_date + 1
        """,
    )
    assert gaps == 0


def test_date_key_matches_the_date(built):
    key = _scalar(
        built, "SELECT date_key FROM warehouse.dim_date WHERE full_date = %s", (date(2026, 6, 30),)
    )
    assert key == 20260630


def test_fiscal_attributes_match_the_generator(built):
    """dim_date must agree with intus_gen.fiscal, or finance facts join to nothing."""
    from intus_gen.fiscal import period_for, quarter_for

    with built.cursor() as cursor:
        cursor.execute(
            "SELECT full_date, fiscal_period, fiscal_quarter FROM warehouse.dim_date "
            "WHERE full_date BETWEEN %s AND %s",
            (date(2025, 11, 1), date(2026, 3, 31)),
        )
        rows = cursor.fetchall()

    assert rows
    for full_date, period, quarter in rows:
        assert period == period_for(full_date)
        assert quarter == quarter_for(full_date)


def test_weekend_flag_and_iso_weekday(built):
    day_of_week, is_weekend = None, None
    with built.cursor() as cursor:
        cursor.execute(
            "SELECT day_of_week, is_weekend, day_name FROM warehouse.dim_date WHERE full_date = %s",
            (date(2026, 6, 28),),  # a Sunday
        )
        day_of_week, is_weekend, day_name = cursor.fetchone()

    assert day_of_week == 7, "ISO weekday: Monday is 1, Sunday is 7"
    assert is_weekend is True
    assert day_name == "Sunday"


# --------------------------------------------------------------------------
# dim_employee — the SCD2 invariants
# --------------------------------------------------------------------------


def test_no_employee_has_overlapping_versions(built):
    overlaps = _scalar(
        built,
        """
        SELECT count(*)
        FROM warehouse.dim_employee AS a
        JOIN warehouse.dim_employee AS b
          ON a.employee_id = b.employee_id
         AND a.employee_key <> b.employee_key
         AND daterange(a.valid_from, a.valid_to, '[)')
          && daterange(b.valid_from, b.valid_to, '[)')
        """,
    )
    assert overlaps == 0


def test_the_database_refuses_an_overlapping_version(built):
    """The exclusion constraint is a backstop; prove it actually bites.

    The inserted span starts a day *after* an existing one so it has a distinct
    (employee_id, valid_from) — otherwise the unique constraint fires first and
    the test proves nothing about overlap detection.
    """
    with built.cursor() as cursor:
        cursor.execute(
            "SELECT employee_id, valid_from, valid_to FROM warehouse.dim_employee "
            "WHERE valid_to IS NOT NULL AND valid_to > valid_from + 2 LIMIT 1"
        )
        employee_id, valid_from, valid_to = cursor.fetchone()

    with (
        pytest.raises(psycopg.errors.ExclusionViolation),
        built.transaction(),
        built.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO warehouse.dim_employee "
            "(employee_id, valid_from, valid_to, is_current) VALUES (%s, %s, %s, false)",
            (employee_id, valid_from + timedelta(days=1), valid_to),
        )
    built.rollback()


def test_exactly_one_current_version_per_employee(built):
    bad = _scalar(
        built,
        """
        SELECT count(*) FROM (
            SELECT employee_id FROM warehouse.dim_employee
            GROUP BY employee_id
            HAVING count(*) FILTER (WHERE is_current) <> 1
        ) AS offenders
        """,
    )
    assert bad == 0


def test_current_version_is_the_latest_one(built):
    """`is_current` means latest version, not "still employed" — leavers have one too."""
    bad = _scalar(
        built,
        """
        SELECT count(*) FROM warehouse.dim_employee AS d
        WHERE d.is_current
          AND EXISTS (
              SELECT 1 FROM warehouse.dim_employee AS later
              WHERE later.employee_id = d.employee_id
                AND later.valid_from > d.valid_from
          )
        """,
    )
    assert bad == 0

    terminated_current = _scalar(
        built,
        "SELECT count(*) FROM warehouse.dim_employee "
        "WHERE is_current AND termination_date IS NOT NULL",
    )
    assert terminated_current > 0, "leavers should still have a current version"


def test_rejected_spans_are_absent_from_the_dimension(built):
    """What the DQ layer rejected must not have been loaded anyway."""
    missing = _scalar(
        built,
        """
        SELECT count(*)
        FROM warehouse.dq_exception AS e
        JOIN warehouse.dim_employee AS d
          ON d.employee_id || '|' || d.valid_from = e.target_key
        WHERE e.rule_code = 'HR_OVERLAPPING_SPAN' AND e.disposition = 'rejected'
        """,
    )
    assert missing == 0


def test_repaired_manager_pointers_are_null(built):
    """Repaired, not rejected: the employee is kept, the dangling pointer cleared."""
    with built.cursor() as cursor:
        cursor.execute(
            """
            SELECT d.manager_employee_id
            FROM warehouse.dq_exception AS e
            JOIN warehouse.dim_employee AS d
              ON d.employee_id || '|' || d.valid_from = e.target_key
            WHERE e.rule_code = 'HR_ORPHAN_MANAGER'
            """
        )
        rows = cursor.fetchall()

    assert rows, "the orphan-manager rule should have repaired at least one row"
    assert all(row[0] is None for row in rows)


def test_surviving_manager_pointers_all_resolve(built):
    dangling = _scalar(
        built,
        """
        SELECT count(*) FROM warehouse.dim_employee AS d
        WHERE d.manager_employee_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM warehouse.dim_employee AS m
              WHERE m.employee_id = d.manager_employee_id
          )
        """,
    )
    assert dangling == 0


def test_every_staging_employee_reaches_the_dimension(built):
    """Rejections remove versions, never people."""
    staged = _scalar(built, "SELECT count(DISTINCT employee_id) FROM staging.hr_employee_history")
    loaded = _scalar(built, "SELECT count(DISTINCT employee_id) FROM warehouse.dim_employee")
    assert loaded == staged


# --------------------------------------------------------------------------
# dim_account, dim_department, dim_product
# --------------------------------------------------------------------------


def test_duplicate_accounts_are_collapsed(built):
    staged = _scalar(built, "SELECT count(*) FROM staging.crm_account")
    distinct = _scalar(built, "SELECT count(DISTINCT account_id) FROM staging.crm_account")
    loaded = _scalar(built, "SELECT count(*) FROM warehouse.dim_account")

    assert staged > distinct, "the fixture should contain seeded duplicates"
    assert loaded == distinct


def test_account_activity_is_derived_from_the_churn_date(built):
    inconsistent = _scalar(
        built,
        "SELECT count(*) FROM warehouse.dim_account WHERE is_active <> (churn_date IS NULL)",
    )
    assert inconsistent == 0


def test_departments_carry_their_cost_centre(built):
    """The conformed join: HR supplies the name, finance the cost centre."""
    without = _scalar(
        built, "SELECT count(*) FROM warehouse.dim_department WHERE cost_center IS NULL"
    )
    total = _scalar(built, "SELECT count(*) FROM warehouse.dim_department")
    assert total > 0
    assert without == 0


def test_product_codes_are_unique(built):
    total = _scalar(built, "SELECT count(*) FROM warehouse.dim_product")
    distinct = _scalar(built, "SELECT count(DISTINCT product_code) FROM warehouse.dim_product")
    assert total == distinct > 0
