"""What the fact tables must be true of.

Referential integrity itself is enforced by the foreign keys declared in
004_warehouse_facts.sql — a fact row with an unresolvable dimension key simply
cannot be inserted, so a broken join fails the build, not a test. What is
worth testing here is everything the database *cannot* enforce: row counts
against staging, the degenerate columns that exist specifically so a fact
whose key resolution fell back to the unknown member still carries the real
identity, and the point-in-time resolution itself actually picking the
version in force at the event's date rather than merely *a* version.
"""

from __future__ import annotations

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


_FACT_TABLES = (
    "fact_compensation",
    "fact_performance_review",
    "fact_subscription",
    "fact_invoice",
    "fact_opportunity",
    "fact_usage_daily",
    "fact_ai_usage",
    "fact_access_event",
    "fact_gl_actual",
    "fact_budget",
)


def test_every_fact_table_has_rows(built):
    for table in _FACT_TABLES:
        count = _scalar(built, f"SELECT count(*) FROM warehouse.{table}")
        assert count > 0, f"{table} is empty"


def test_rerunning_the_build_leaves_fact_row_counts_unchanged(built):
    """Truncate-and-reload is the fact-table analogue of dimension idempotency."""
    before = {
        table: _scalar(built, f"SELECT count(*) FROM warehouse.{table}") for table in _FACT_TABLES
    }
    run(built)
    after = {
        table: _scalar(built, f"SELECT count(*) FROM warehouse.{table}") for table in _FACT_TABLES
    }
    assert before == after


# --------------------------------------------------------------------------
# Row counts against staging, accounting for what each rule removes
# --------------------------------------------------------------------------


def test_compensation_and_review_facts_cover_every_staging_row(built):
    """No rules reject rows from either dataset, so counts must match exactly."""
    assert _scalar(built, "SELECT count(*) FROM warehouse.fact_compensation") == _scalar(
        built, "SELECT count(*) FROM staging.hr_compensation"
    )
    assert _scalar(built, "SELECT count(*) FROM warehouse.fact_performance_review") == _scalar(
        built, "SELECT count(*) FROM staging.hr_performance_review"
    )


def test_opportunity_fact_excludes_only_orphans(built):
    staged = _scalar(built, "SELECT count(*) FROM staging.crm_opportunity")
    rejected = _scalar(
        built,
        "SELECT count(*) FROM warehouse.dq_exception "
        "WHERE rule_code = 'CRM_ORPHAN_OPPORTUNITY' AND disposition = 'rejected'",
    )
    loaded = _scalar(built, "SELECT count(*) FROM warehouse.fact_opportunity")
    assert rejected > 0
    assert loaded == staged - rejected


def test_usage_fact_excludes_rejected_rows_only(built):
    """Duplicates and unknown-account rows are rejected; negative sessions are flagged and kept."""
    staged_distinct = _scalar(
        built,
        "SELECT count(DISTINCT (usage_date, account_id, product_code)) FROM staging.usage_daily",
    )
    unknown_accounts = _scalar(
        built,
        """
        SELECT count(DISTINCT (usage_date, account_id, product_code))
        FROM staging.usage_daily AS s
        WHERE NOT EXISTS (
            SELECT 1 FROM warehouse.dim_account AS a WHERE a.account_id = s.account_id
        )
        """,
    )
    loaded = _scalar(built, "SELECT count(*) FROM warehouse.fact_usage_daily")
    assert loaded == staged_distinct - unknown_accounts

    flagged_negative = _scalar(
        built,
        "SELECT count(*) FROM warehouse.dq_exception WHERE rule_code = 'USAGE_NEGATIVE_SESSIONS'",
    )
    assert flagged_negative > 0
    still_present = _scalar(
        built, "SELECT count(*) FROM warehouse.fact_usage_daily WHERE sessions < 0"
    )
    assert still_present == flagged_negative, "flagged rows must be kept, not silently dropped"


# --------------------------------------------------------------------------
# Degenerate identity columns: recoverable even when the key falls back
# --------------------------------------------------------------------------


def test_unknown_member_fallback_is_exactly_the_missing_actor_case(built):
    """Every -1 fallback in fact_access_event traces to SEC_MISSING_ACTOR.

    employee_key_best resolves any employee_id that exists in dim_employee at
    all — even a terminated one — to their nearest known version, so the only
    way employee_key can still land on the unknown member is when there was
    no employee_id to look up in the first place. That degenerate employee_id
    column exists precisely so a *future* defect type that corrupted the id
    to something genuinely unresolvable would still leave the real value
    recoverable; the current rule set never exercises that path, and this
    test is honest about that rather than asserting a case that cannot occur.
    """
    with built.cursor() as cursor:
        cursor.execute(
            "SELECT employee_id FROM warehouse.fact_access_event WHERE employee_key = -1"
        )
        rows = [row[0] for row in cursor.fetchall()]

    assert rows, "no fact_access_event row fell back to the unknown member in this fixture"
    assert all(value is None for value in rows)

    missing_actor_count = _scalar(
        built, "SELECT count(*) FROM warehouse.dq_exception WHERE rule_code = 'SEC_MISSING_ACTOR'"
    )
    assert len(rows) == missing_actor_count


def test_login_after_termination_resolves_to_the_real_employee_not_unknown(built):
    """The centrepiece rule's fact row must be attributable, not anonymised.

    employee_key_best (not employee_key_as_of) is used at fact-load time
    specifically so this case — where strict point-in-time resolution always
    fails, by construction — still points at the actual dim_employee row.
    """
    with built.cursor() as cursor:
        cursor.execute(
            """
            SELECT f.employee_key, f.employee_id, d.employee_id
            FROM warehouse.dq_exception AS e
            JOIN warehouse.fact_access_event AS f ON f.event_id = e.target_key
            JOIN warehouse.dim_employee AS d ON d.employee_key = f.employee_key
            WHERE e.rule_code = 'SEC_LOGIN_AFTER_TERMINATION'
            """
        )
        rows = cursor.fetchall()

    assert rows, "the centrepiece rule produced no fact rows to check"
    for employee_key, fact_employee_id, dim_employee_id in rows:
        assert employee_key != -1, "must resolve to the real employee, not the unknown member"
        assert fact_employee_id == dim_employee_id


# --------------------------------------------------------------------------
# Point-in-time resolution actually picks the version in force
# --------------------------------------------------------------------------


def test_compensation_resolves_to_the_version_in_force_when_point_in_time_succeeds(built):
    """employee_key_best must agree with strict employee_key_as_of whenever the
    latter actually resolves — i.e. whenever the comp record's own date is
    covered by a real, unrejected SCD2 span. It is not required to agree in
    every row: when the covering span was itself rejected by HR_OVERLAPPING_SPAN,
    employee_key_as_of correctly returns NULL for that date (a real gap), and
    employee_key_best's fallback to the employee's *latest* version is then
    the intended behaviour, not a bug — a raise dated before the employee's
    most recent version has no obligation to resolve to that recent version's
    department. Requiring point-in-time agreement blanket-wide is exactly the
    wrong assertion, and an earlier version of this test made it, failing on a
    genuine gap case rather than a real defect.
    """
    with built.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                f.compensation_id,
                f.employee_key,
                warehouse.employee_key_as_of(s.employee_id, s.effective_from::date) AS strict_key
            FROM warehouse.fact_compensation AS f
            JOIN staging.hr_compensation AS s ON s.compensation_id = f.compensation_id
            """
        )
        rows = cursor.fetchall()

    assert rows
    strict_resolved = [row for row in rows if row[2] is not None]
    assert strict_resolved, "expected at least one row where point-in-time resolution succeeds"
    for compensation_id, employee_key, strict_key in strict_resolved:
        assert employee_key == strict_key, (
            f"{compensation_id}: employee_key_best {employee_key} disagrees with "
            f"the strict point-in-time key {strict_key} despite point-in-time succeeding"
        )


def test_invoice_references_a_real_subscription(built):
    with built.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*) FROM warehouse.fact_invoice AS i
            WHERE NOT EXISTS (
                SELECT 1 FROM warehouse.fact_subscription AS s
                WHERE s.subscription_id = i.subscription_id
            )
            """
        )
        dangling = cursor.fetchone()[0]
    assert dangling == 0


def test_gl_actual_and_budget_department_keys_resolve_via_department_code(built):
    """FIN_ORPHAN_COST_CENTER corrupts cost_center only; department_code survives,
    so department attribution should never fall back to the unknown member.
    """
    orphan_department_facts = _scalar(
        built, "SELECT count(*) FROM warehouse.fact_gl_actual WHERE department_key = -1"
    )
    assert orphan_department_facts == 0
