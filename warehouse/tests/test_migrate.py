"""The migration runner: ordering, immutability, and transactional application."""

from __future__ import annotations

import pytest

from intus_warehouse.migrate import MigrationError, discover, pending, run


def _versions(migrations) -> list[str]:
    return [migration.version for migration in migrations]


# --------------------------------------------------------------------------
# Discovery (no database needed, but the suite marks everything `db`)
# --------------------------------------------------------------------------


def test_discovers_migrations_in_order():
    migrations = discover()
    assert migrations, "no migrations found"
    assert _versions(migrations) == sorted(_versions(migrations))
    assert migrations[0].name == "schemas"


def test_rejects_badly_named_files(tmp_path):
    (tmp_path / "001_ok.sql").write_text("SELECT 1", encoding="utf-8")
    (tmp_path / "nope.sql").write_text("SELECT 1", encoding="utf-8")
    with pytest.raises(MigrationError, match="NNN_lower_snake_case"):
        discover(tmp_path)


def test_rejects_duplicate_versions(tmp_path):
    (tmp_path / "001_one.sql").write_text("SELECT 1", encoding="utf-8")
    (tmp_path / "001_two.sql").write_text("SELECT 1", encoding="utf-8")
    with pytest.raises(MigrationError, match="duplicate migration version"):
        discover(tmp_path)


def test_checksum_ignores_line_endings(tmp_path):
    """A CRLF checkout must not look like a tampered migration."""
    (tmp_path / "001_lf.sql").write_text("SELECT 1;\nSELECT 2;\n", encoding="utf-8", newline="\n")
    lf = discover(tmp_path)[0]
    (tmp_path / "001_lf.sql").write_text("SELECT 1;\r\nSELECT 2;\r\n", encoding="utf-8", newline="")
    crlf = discover(tmp_path)[0]
    assert lf.checksum == crlf.checksum


# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------


def test_applies_everything_to_a_blank_database(blank_connection):
    applied = run(blank_connection)
    assert _versions(applied) == _versions(discover())
    assert pending(blank_connection) == ()


def test_running_twice_is_a_no_op(blank_connection):
    run(blank_connection)
    assert run(blank_connection) == ()


def test_creates_the_expected_schemas(migrated_connection):
    with migrated_connection.cursor() as cursor:
        cursor.execute(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name IN ('staging', 'warehouse', 'reporting')"
        )
        found = {row[0] for row in cursor.fetchall()}
    assert found == {"staging", "warehouse", "reporting"}


def test_records_what_it_applied(migrated_connection):
    with migrated_connection.cursor() as cursor:
        cursor.execute(
            "SELECT version, name, checksum FROM public.schema_migration ORDER BY version"
        )
        rows = cursor.fetchall()

    on_disk = discover()
    assert [row[0] for row in rows] == _versions(on_disk)
    assert [row[2] for row in rows] == [migration.checksum for migration in on_disk]


def test_editing_an_applied_migration_is_an_error(blank_connection, tmp_path):
    """Immutability: the schema in front of you must match the file that built it."""
    migration = tmp_path / "001_probe.sql"
    migration.write_text("CREATE TABLE probe (id int);", encoding="utf-8")
    run(blank_connection, tmp_path)

    migration.write_text("CREATE TABLE probe (id bigint);", encoding="utf-8")
    with pytest.raises(MigrationError, match="has changed since it was applied"):
        pending(blank_connection, tmp_path)


def test_database_ahead_of_the_checkout_is_an_error(blank_connection, tmp_path):
    (tmp_path / "001_probe.sql").write_text("CREATE TABLE probe (id int);", encoding="utf-8")
    run(blank_connection, tmp_path)

    (tmp_path / "001_probe.sql").unlink()
    with pytest.raises(MigrationError, match="no file"):
        pending(blank_connection, tmp_path)


def test_a_failing_migration_leaves_nothing_behind(blank_connection, tmp_path):
    """Postgres has transactional DDL; a half-applied migration must be impossible."""
    (tmp_path / "001_good.sql").write_text("CREATE TABLE kept (id int);", encoding="utf-8")
    (tmp_path / "002_bad.sql").write_text(
        "CREATE TABLE gone (id int); SELECT this_function_does_not_exist();",
        encoding="utf-8",
    )

    with pytest.raises(Exception):  # noqa: B017 - psycopg raises a driver-specific error
        run(blank_connection, tmp_path)
    blank_connection.rollback()

    with blank_connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.kept'), to_regclass('public.gone')")
        kept, gone = cursor.fetchone()
        cursor.execute("SELECT version FROM public.schema_migration ORDER BY version")
        recorded = [row[0] for row in cursor.fetchall()]

    assert kept is not None, "the first migration should have committed"
    assert gone is None, "the failing migration must have rolled back entirely"
    assert recorded == ["001"]
