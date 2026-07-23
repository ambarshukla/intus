"""Silver's structural parity with the legacy warehouse, checked, not assumed.

The fourth use of the D-010 pattern ("duplicate small reference data, test
for drift") this project has reached for: staging DDL vs. the generator,
the AI pricing table, the country-region lookup, and now the Postgres
warehouse schema vs. this SQL-dialect port of it. Column *names* are the
part of the contract every hand-written transform statement relies on lining
up; types are not compared (see silver.py's docstring for why).

Static, not live — same reasoning as test_bronze_schema.py: this never
touches Databricks, so it runs with no cloud credentials.
"""

from __future__ import annotations

from pathlib import Path

from intus_lakehouse.silver import parse_sql_file

_WAREHOUSE_SQL = Path(__file__).parents[2] / "warehouse" / "sql"
_SILVER_SCHEMA_SQL = Path(__file__).parents[2] / "lakehouse" / "sql" / "20_silver_schema.sql"

#: dq_exception is deliberately not compared: the Postgres version carries a
#: run_id FK to warehouse.transform_run, which silver drops entirely (D-025).
#: transform_run itself has no silver counterpart at all, for the same
#: reason. Comparing either here would fail on a difference this project
#: chose on purpose, not one that drifted.
_SKIP = {"dq_exception", "transform_run"}


def _warehouse_tables() -> dict[str, list[str]]:
    tables: dict[str, list[str]] = {}
    for filename in ("003_warehouse_dimensions.sql", "004_warehouse_facts.sql"):
        tables.update(parse_sql_file(_WAREHOUSE_SQL / filename))
    for name in _SKIP:
        tables.pop(name, None)
    return tables


def _silver_tables() -> dict[str, list[str]]:
    tables = parse_sql_file(_SILVER_SCHEMA_SQL)
    for name in _SKIP:
        tables.pop(name, None)
    return tables


def test_every_warehouse_table_has_a_silver_counterpart():
    warehouse_tables = _warehouse_tables()
    silver_tables = _silver_tables()
    assert warehouse_tables.keys() <= silver_tables.keys(), (
        f"missing silver table(s): {sorted(warehouse_tables.keys() - silver_tables.keys())}"
    )


def test_silver_has_no_extra_tables():
    warehouse_tables = _warehouse_tables()
    silver_tables = _silver_tables()
    assert silver_tables.keys() <= warehouse_tables.keys(), (
        f"silver table(s) with no warehouse counterpart: "
        f"{sorted(silver_tables.keys() - warehouse_tables.keys())}"
    )


def test_silver_columns_match_the_warehouse_exactly():
    warehouse_tables = _warehouse_tables()
    silver_tables = _silver_tables()
    for name, warehouse_columns in warehouse_tables.items():
        assert silver_tables[name] == warehouse_columns, (
            f"{name}: silver schema has diverged from the warehouse schema\n"
            f"  warehouse: {warehouse_columns}\n"
            f"  silver:    {silver_tables[name]}"
        )
