"""The bronze SQL is hand-written; this is what stops it drifting.

Same rationale as ``warehouse/tests/test_staging_schema.py``: generating the
SQL from the registry would make drift impossible but would also hide the
SQL itself, which is the artefact this phase exists to show. Writing it by
hand and checking it against the registry gets both — real, readable SQL in
the repo, and a failing test the moment a generator grows a column that
bronze does not have.

Static, not live: unlike the warehouse tests, this never touches Databricks.
The bronze file's shape (one ``read_files()`` schema string per table) is
checkable from the text alone, so the test suite stays fast and runnable
with no cloud credentials — the live end-to-end check is a manual step
(see BUILD_LOG), not something CI can do without workspace secrets it does
not have configured yet.
"""

from __future__ import annotations

from pathlib import Path

from intus_gen.domains import all_datasets
from intus_lakehouse.bronze import parse_bronze_tables

_BRONZE_SQL = Path(__file__).parents[2] / "lakehouse" / "sql" / "10_bronze.sql"


def test_every_dataset_has_a_bronze_table():
    tables = parse_bronze_tables(_BRONZE_SQL)
    expected = {dataset.name for dataset in all_datasets()}
    assert expected <= tables.keys(), f"missing bronze table(s): {sorted(expected - tables.keys())}"


def test_bronze_columns_match_the_generator_exactly():
    """Names and order both: this is the same correspondence COPY relies on in staging."""
    tables = parse_bronze_tables(_BRONZE_SQL)
    for dataset in all_datasets():
        assert tables[dataset.name] == list(dataset.header()), (
            f"{dataset.name}: bronze SQL has diverged from the generator schema"
        )


def test_bronze_has_no_extra_tables():
    """A stale table nobody generates anymore is exactly the drift this test exists to catch."""
    tables = parse_bronze_tables(_BRONZE_SQL)
    expected = {dataset.name for dataset in all_datasets()}
    assert tables.keys() <= expected, (
        f"bronze table(s) with no matching dataset: {sorted(tables.keys() - expected)}"
    )
