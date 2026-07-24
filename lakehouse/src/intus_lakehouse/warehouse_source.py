"""Reads ``reporting.*`` views over a direct Postgres connection.

The legacy-side half of the parity check — ``databricks_source.py`` is the
lakehouse-side half. Deliberately thin: psycopg's cursor already gives typed
Python values (``Decimal`` for numeric, ``date`` for date columns), so there
is no conversion layer to write here, unlike the Databricks side where every
value arrives as a string.
"""

from __future__ import annotations

from typing import Any

import psycopg

#: Same seven views as databricks_source.GOLD_VIEWS — used here only to
#: reject an unknown view name before it reaches an f-string, since this
#: module builds SQL text rather than using a parameterised identifier
#: (Postgres has no placeholder for a table/view name).
REPORTING_VIEWS = (
    "rpt_headcount_trend",
    "rpt_attrition_by_department",
    "rpt_sales_pipeline_by_rep",
    "rpt_revenue_trend",
    "rpt_product_usage_trend",
    "rpt_ai_cost_by_department",
    "rpt_budget_variance",
)


class WarehouseSourceError(RuntimeError):
    """A reporting view could not be read safely."""


def fetch_view(
    connection: psycopg.Connection, view: str
) -> tuple[tuple[str, ...], tuple[tuple[Any, ...], ...]]:
    if view not in REPORTING_VIEWS:
        raise WarehouseSourceError(f"not a known reporting view: {view}")
    with connection.cursor() as cursor:
        # view name is allow-listed above, not user input
        cursor.execute(f"SELECT * FROM reporting.{view}")
        columns = tuple(column.name for column in cursor.description)
        rows = tuple(tuple(row) for row in cursor.fetchall())
    return columns, rows
