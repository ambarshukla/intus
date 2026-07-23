"""The staging DDL is hand-written; this is what stops it drifting.

Generating staging DDL from the generators' `Dataset` registry would make drift
impossible, and would also hide the SQL — which is the artefact this phase
exists to show. Writing it by hand and checking it against the registry gets
both: real DDL in the repo, and a build failure the moment a generator grows a
column that staging does not have.

Checked here rather than in a code comment because "mirrors the generator
exactly" is precisely the kind of claim that is true when written and false six
months later.
"""

from __future__ import annotations

from intus_gen.domains import all_datasets
from intus_warehouse.load import STAGING_SCHEMA


def _columns(connection, table: str) -> list[tuple[str, str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (STAGING_SCHEMA, table),
        )
        return cursor.fetchall()


def test_every_dataset_has_a_staging_table(migrated_connection):
    with migrated_connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            (STAGING_SCHEMA,),
        )
        tables = {row[0] for row in cursor.fetchall()}

    expected = {dataset.name for dataset in all_datasets()}
    assert expected <= tables, f"missing staging table(s): {sorted(expected - tables)}"


def test_staging_columns_match_the_generator_exactly(migrated_connection):
    """Names and order both: COPY relies on positional correspondence."""
    for dataset in all_datasets():
        observed = [name for name, _type in _columns(migrated_connection, dataset.name)]
        assert observed == list(dataset.header()), (
            f"{dataset.name}: staging DDL has diverged from the generator schema"
        )


def test_every_staging_column_is_text(migrated_connection):
    """Typing staging would make a malformed row fail the load instead of the transform."""
    for dataset in all_datasets():
        types = {data_type for _name, data_type in _columns(migrated_connection, dataset.name)}
        assert types == {"text"}, f"{dataset.name} has non-text columns: {sorted(types)}"


def test_staging_tables_have_no_constraints(migrated_connection):
    """Two seeded defects are duplicate rows; a key here would reject them at the door."""
    dataset_names = tuple(dataset.name for dataset in all_datasets())
    with migrated_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tc.table_name, tc.constraint_type
            FROM information_schema.table_constraints tc
            WHERE tc.table_schema = %s
              AND tc.table_name = ANY(%s)
              AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY', 'CHECK')
            """,
            (STAGING_SCHEMA, list(dataset_names)),
        )
        found = cursor.fetchall()

    assert found == [], f"staging must be constraint-free, found: {found}"


def test_reporting_schema_holds_no_tables(migrated_connection):
    """Reporting is views only, so a report cannot disagree with the facts beneath it."""
    with migrated_connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'reporting' AND table_type = 'BASE TABLE'"
        )
        assert cursor.fetchall() == []
