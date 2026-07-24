"""Gold's structural parity with the legacy warehouse's reporting views, checked, not assumed.

The fifth use of the D-010 pattern ("duplicate small reference data, test for
drift") this project has reached for: staging DDL vs. the generator, the AI
pricing table, the country-region lookup, the silver schema vs. the Postgres
warehouse schema, and now gold's seven ported views vs. the seven originals
they port. Column *names* are what every consumer of these views (a BI tool,
a parity check) actually depends on; types are not compared, same reasoning
as test_silver_schema.py.

Static, not live — same reasoning as the bronze and silver drift tests: this
never touches Databricks, so it runs with no cloud credentials, which matters
because there is no live Databricks CI smoke test yet.
"""

from __future__ import annotations

from pathlib import Path

from intus_gen.domains import all_datasets
from intus_gen.sensitivity import Tier
from intus_lakehouse.gold import parse_sql_file

_WAREHOUSE_REPORTING_SQL = (
    Path(__file__).parents[2] / "warehouse" / "sql" / "005_reporting_views.sql"
)
_GOLD_VIEWS_SQL = Path(__file__).parents[2] / "lakehouse" / "sql" / "30_gold_views.sql"


def _reporting_views() -> dict[str, list[str]]:
    return parse_sql_file(_WAREHOUSE_REPORTING_SQL)


def _gold_views() -> dict[str, list[str]]:
    return parse_sql_file(_GOLD_VIEWS_SQL)


def test_every_reporting_view_has_a_gold_counterpart():
    reporting_views = _reporting_views()
    gold_views = _gold_views()
    assert reporting_views.keys() <= gold_views.keys(), (
        f"missing gold view(s): {sorted(reporting_views.keys() - gold_views.keys())}"
    )


def test_gold_has_no_extra_views():
    reporting_views = _reporting_views()
    gold_views = _gold_views()
    assert gold_views.keys() <= reporting_views.keys(), (
        f"gold view(s) with no reporting counterpart: "
        f"{sorted(gold_views.keys() - reporting_views.keys())}"
    )


def test_gold_columns_match_reporting_exactly():
    reporting_views = _reporting_views()
    gold_views = _gold_views()
    for name, reporting_columns in reporting_views.items():
        assert gold_views[name] == reporting_columns, (
            f"{name}: gold view has diverged from the reporting view\n"
            f"  reporting: {reporting_columns}\n"
            f"  gold:      {gold_views[name]}"
        )


def test_no_gold_view_exposes_a_restricted_column_raw():
    """Same D-020 boundary as reporting.*, checked a second time here since
    the SQL was hand-ported and a copy-paste slip could reintroduce a
    RESTRICTED-tier column the Postgres side never had.
    """
    restricted_names = {
        column for dataset in all_datasets() for column in dataset.columns_at(Tier.RESTRICTED)
    }
    gold_views = _gold_views()
    offenders = [
        (view, column)
        for view, columns in gold_views.items()
        for column in columns
        if column in restricted_names
    ]
    assert offenders == [], f"restricted-tier column(s) exposed in gold views: {offenders}"


def test_parser_finds_all_seven_views_in_each_file():
    """Guards the parser itself: a regex that silently matched zero views
    would make every test above vacuously pass.
    """
    assert len(_reporting_views()) == 7
    assert len(_gold_views()) == 7
