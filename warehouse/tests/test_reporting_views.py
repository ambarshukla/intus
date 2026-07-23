"""What the reporting views must be true of.

These are the views a persona actually queries, so the tests here check the
window-function arithmetic itself — not just "the view runs" — because a view
that executes without error and returns a wrong number is worse than one that
errors: it looks trustworthy. `rpt_attrition_by_department` scored an
extraction of 4883% attrition on its first version, which is exactly the kind
of wrong that only shows up if something checks the actual value.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from itertools import pairwise

import pytest

from intus_gen.domains import all_datasets
from intus_gen.sensitivity import Tier
from intus_warehouse.load import load_directory
from intus_warehouse.transform import run

_VIEWS = (
    "rpt_headcount_trend",
    "rpt_attrition_by_department",
    "rpt_sales_pipeline_by_rep",
    "rpt_revenue_trend",
    "rpt_product_usage_trend",
    "rpt_ai_cost_by_department",
    "rpt_budget_variance",
)


@pytest.fixture
def built(migrated_connection, extract):
    load_directory(migrated_connection, extract)
    run(migrated_connection)
    return migrated_connection


def _rows(connection, sql: str, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [description.name for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _scalar(connection, sql: str, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


def test_every_view_exists_and_returns_rows(built):
    for view in _VIEWS:
        rows = _rows(built, f"SELECT * FROM reporting.{view} LIMIT 1000")
        assert rows, f"reporting.{view} returned no rows"


def test_reporting_schema_still_holds_only_views(built):
    """Guards the invariant these views depend on: reporting has no storage of
    its own, so a query against it can never disagree with the star schema
    underneath — re-checked here specifically against the seven new views,
    complementing test_staging_schema.py's general-purpose version.
    """
    with built.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'reporting' AND table_type = 'BASE TABLE'"
        )
        assert cursor.fetchall() == []
    with built.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.views WHERE table_schema = 'reporting'"
        )
        found = {row[0] for row in cursor.fetchall()}
    assert set(_VIEWS) <= found


# --------------------------------------------------------------------------
# rpt_headcount_trend
# --------------------------------------------------------------------------


def test_headcount_matches_a_direct_point_in_time_count(built):
    """Cross-checked against the same logic written out by hand, independent
    of the view's own month-spine machinery — the view could have an
    off-by-one at a month boundary that a self-referential check would miss.
    """
    with built.cursor() as cursor:
        cursor.execute(
            "SELECT department_code, month_start, headcount FROM reporting.rpt_headcount_trend "
            "ORDER BY random() LIMIT 15"
        )
        samples = cursor.fetchall()

    assert samples
    for department_code, month_start, headcount in samples:
        month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(
            days=1
        )
        direct_count = _scalar(
            built,
            """
            SELECT count(DISTINCT employee_id) FROM warehouse.dim_employee
            WHERE employee_key <> -1 AND department_code = %s
              AND valid_from <= %s AND (valid_to IS NULL OR valid_to > %s)
            """,
            (department_code, month_end, month_end),
        )
        assert headcount == direct_count, (department_code, month_start)


def test_headcount_change_mom_is_null_only_for_each_department_first_month(built):
    rows = _rows(
        built,
        "SELECT department_code, month_start, headcount_change_mom "
        "FROM reporting.rpt_headcount_trend ORDER BY department_code, month_start",
    )
    seen: set[str] = set()
    for row in rows:
        is_first = row["department_code"] not in seen
        seen.add(row["department_code"])
        if is_first:
            assert row["headcount_change_mom"] is None
        else:
            assert row["headcount_change_mom"] is not None


# --------------------------------------------------------------------------
# rpt_attrition_by_department — the regression case
# --------------------------------------------------------------------------


def test_attrition_rate_is_a_plausible_percentage(built):
    """Regression guard for the bug this view actually shipped with: averaging
    per-row indicators across dim_employee's many SCD2-span rows per employee
    (instead of counting distinct employees) produced rates over 1000%. A
    generator with ~14%/year attrition should never show anything close.
    """
    rows = _rows(
        built,
        "SELECT department_code, attrition_rate_pct FROM reporting.rpt_attrition_by_department",
    )
    assert rows
    for row in rows:
        assert 0 <= row["attrition_rate_pct"] < 100, row


def test_attrition_rank_follows_rank_semantics(built):
    """RANK()'s actual contract: ties share a rank, and the *next* rank skips
    ahead by the tie's size (1, 1, 3, ...) rather than being dense (1, 1, 2).
    An earlier version of this test assumed a dense 1..N sequence, which only
    happens to hold when nothing ties — true at full scale with hundreds of
    terminations, false at the small-scale fixture used here, where a handful
    of departments and few terminations make ties routine.
    """
    rows = _rows(
        built,
        "SELECT department_code, attrition_rate_pct, attrition_rank "
        "FROM reporting.rpt_attrition_by_department ORDER BY attrition_rank",
    )
    assert rows
    assert rows[0]["attrition_rank"] == 1

    for seen_before, (earlier, later) in enumerate(pairwise(rows), start=1):
        if later["attrition_rate_pct"] == earlier["attrition_rate_pct"]:
            assert later["attrition_rank"] == earlier["attrition_rank"], (earlier, later)
        else:
            assert later["attrition_rank"] == seen_before + 1, (earlier, later)


# --------------------------------------------------------------------------
# rpt_sales_pipeline_by_rep
# --------------------------------------------------------------------------


def test_pipeline_running_total_reaches_the_rep_total(built):
    """The last row per rep's cumulative sum must equal that rep's own total —
    otherwise the running total and the denominator it's checked against
    (used for the rank) have silently diverged.
    """
    rows = _rows(
        built,
        "SELECT owner_employee_id, cumulative_pipeline_usd, total_open_pipeline_usd, created_date "
        "FROM reporting.rpt_sales_pipeline_by_rep ORDER BY owner_employee_id, created_date",
    )
    last_per_rep: dict[str, dict] = {}
    for row in rows:
        last_per_rep[row["owner_employee_id"]] = row

    for owner, last_row in last_per_rep.items():
        assert last_row["cumulative_pipeline_usd"] == last_row["total_open_pipeline_usd"], owner


def test_pipeline_rank_is_consistent_within_a_rep(built):
    """Every row for one rep must carry the same rank — it describes the rep,
    not the individual opportunity, and is repeated across their rows by
    design (a denormalised annotation, not a per-row computation).
    """
    rows = _rows(
        built, "SELECT owner_employee_id, pipeline_rank FROM reporting.rpt_sales_pipeline_by_rep"
    )
    ranks_by_rep: dict[str, set[int]] = {}
    for row in rows:
        ranks_by_rep.setdefault(row["owner_employee_id"], set()).add(row["pipeline_rank"])
    for owner, ranks in ranks_by_rep.items():
        assert len(ranks) == 1, f"{owner} has inconsistent ranks: {ranks}"


# --------------------------------------------------------------------------
# rpt_revenue_trend
# --------------------------------------------------------------------------


def test_revenue_trend_first_month_has_no_growth_figure(built):
    rows = _rows(built, "SELECT * FROM reporting.rpt_revenue_trend ORDER BY month_end LIMIT 1")
    assert rows[0]["net_new_arr_usd"] is None
    assert rows[0]["mom_growth_pct"] is None
    assert rows[0]["cumulative_net_new_arr_usd"] == rows[0]["total_arr_usd"]


def test_revenue_trend_cumulative_matches_a_running_sum(built):
    """Every row after the first: cumulative = previous cumulative + this row's delta."""
    rows = _rows(
        built,
        "SELECT month_end, net_new_arr_usd, cumulative_net_new_arr_usd "
        "FROM reporting.rpt_revenue_trend ORDER BY month_end",
    )
    assert len(rows) >= 2
    prior_cumulative = rows[0]["cumulative_net_new_arr_usd"]
    for row in rows[1:]:
        assert row["cumulative_net_new_arr_usd"] == prior_cumulative + row["net_new_arr_usd"]
        prior_cumulative = row["cumulative_net_new_arr_usd"]


# --------------------------------------------------------------------------
# rpt_product_usage_trend
# --------------------------------------------------------------------------


def test_usage_7day_average_matches_manual_computation(built):
    rows = _rows(
        built,
        "SELECT full_date, active_users, active_users_7day_avg "
        "FROM reporting.rpt_product_usage_trend "
        "WHERE product_code = 'HAL-CORE' ORDER BY full_date",
    )
    assert len(rows) >= 10

    values = [row["active_users"] for row in rows]
    for index in range(min(10, len(rows))):
        window = values[max(0, index - 6) : index + 1]
        expected = round(sum(window) / len(window), 1)
        assert float(rows[index]["active_users_7day_avg"]) == pytest.approx(expected, abs=0.1), (
            index
        )


def test_usage_week_over_week_is_null_for_the_first_six_days_of_each_product(built):
    rows = _rows(
        built,
        "SELECT product_code, full_date, active_users_change_vs_last_week "
        "FROM reporting.rpt_product_usage_trend ORDER BY product_code, full_date",
    )
    seen: dict[str, int] = {}
    for row in rows:
        index = seen.get(row["product_code"], 0)
        if index < 7:
            assert row["active_users_change_vs_last_week"] is None, (row["product_code"], index)
        else:
            assert row["active_users_change_vs_last_week"] is not None
        seen[row["product_code"]] = index + 1


# --------------------------------------------------------------------------
# rpt_ai_cost_by_department
# --------------------------------------------------------------------------


def test_ai_cost_percentages_sum_to_roughly_100_per_period(built):
    rows = _rows(
        built,
        "SELECT fiscal_period, sum(pct_of_month_total) AS total_pct "
        "FROM reporting.rpt_ai_cost_by_department GROUP BY fiscal_period",
    )
    assert rows
    for row in rows:
        # Rounding to 1 decimal place per row means the sum can drift by a
        # few tenths across nine departments; a wide tolerance still catches
        # a genuinely wrong denominator (e.g. summing over the wrong partition).
        assert float(row["total_pct"]) == pytest.approx(100.0, abs=1.0), row["fiscal_period"]


def test_ai_cost_rank_has_no_gaps_within_a_period(built):
    rows = _rows(
        built,
        "SELECT fiscal_period, department_rank_in_month FROM reporting.rpt_ai_cost_by_department "
        "WHERE fiscal_period = "
        "(SELECT max(fiscal_period) FROM reporting.rpt_ai_cost_by_department)",
    )
    ranks = sorted(row["department_rank_in_month"] for row in rows)
    assert ranks == list(range(1, len(ranks) + 1))


# --------------------------------------------------------------------------
# rpt_budget_variance
# --------------------------------------------------------------------------


def test_budget_variance_cumulative_matches_a_running_sum(built):
    rows = _rows(
        built,
        "SELECT department_code, fiscal_period, variance_usd, cumulative_variance_usd "
        "FROM reporting.rpt_budget_variance ORDER BY department_code, fiscal_period",
    )
    running: dict[str, Decimal] = {}
    for row in rows:
        department = row["department_code"]
        running[department] = running.get(department, Decimal(0)) + row["variance_usd"]
        assert row["cumulative_variance_usd"] == running[department], row


def test_budget_variance_percentile_is_bounded(built):
    rows = _rows(built, "SELECT overspend_percentile_in_period FROM reporting.rpt_budget_variance")
    assert rows
    for row in rows:
        assert Decimal("0") <= row["overspend_percentile_in_period"] <= Decimal("1")


# --------------------------------------------------------------------------
# The governance boundary: no restricted-tier data exposed raw
# --------------------------------------------------------------------------


def test_no_reporting_view_exposes_a_restricted_column_raw(built):
    """Phase 4 (RLS/masking) owns individual-level restricted data. Until it
    exists, these views simply must not select it — this checks the exposed
    column names against every RESTRICTED-tier column name declared across
    the generator's datasets.
    """
    restricted_names = {
        column for dataset in all_datasets() for column in dataset.columns_at(Tier.RESTRICTED)
    }

    with built.cursor() as cursor:
        cursor.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'reporting'"
        )
        exposed = cursor.fetchall()

    offenders = [(view, column) for view, column in exposed if column in restricted_names]
    assert offenders == [], f"restricted-tier column(s) exposed in reporting views: {offenders}"
