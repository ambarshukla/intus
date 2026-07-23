"""The transform runner: ordering, idempotency, and failure handling."""

from __future__ import annotations

import pytest

from intus_warehouse.load import load_directory
from intus_warehouse.transform import TransformError, discover, run


@pytest.fixture
def loaded(migrated_connection, extract):
    load_directory(migrated_connection, extract)
    return migrated_connection


def _counts(connection) -> dict[str, int]:
    tables = ("dim_date", "dim_department", "dim_employee", "dim_account", "dim_product")
    counts = {}
    with connection.cursor() as cursor:
        for table in tables:
            cursor.execute(f"SELECT count(*) FROM warehouse.{table}")
            counts[table] = cursor.fetchone()[0]
    return counts


def test_discovers_transforms_in_order():
    steps = discover()
    orders = [step.order for step in steps]
    assert orders == sorted(orders)
    assert [step.name for step in steps][:2] == ["dim_date", "dim_department"]


def test_rejects_badly_named_transforms(tmp_path):
    (tmp_path / "10_ok.sql").write_text("SELECT 1", encoding="utf-8")
    (tmp_path / "bad.sql").write_text("SELECT 1", encoding="utf-8")
    with pytest.raises(TransformError, match="NN_lower_snake_case"):
        discover(tmp_path)


def test_empty_transform_directory_is_an_error(tmp_path):
    with pytest.raises(TransformError, match="no transform files"):
        discover(tmp_path)


def test_build_populates_every_dimension(loaded):
    run(loaded)
    counts = _counts(loaded)
    for table, count in counts.items():
        assert count > 0, f"{table} is empty after the transform"


def test_run_is_recorded(loaded, extract):
    result = run(loaded)
    with loaded.cursor() as cursor:
        cursor.execute(
            "SELECT status, source_scale, source_as_of, finished_at "
            "FROM warehouse.transform_run WHERE run_id = %s",
            (result.run_id,),
        )
        status, scale, as_of, finished = cursor.fetchone()

    assert status == "succeeded"
    assert scale == "small"
    assert as_of is not None
    assert finished is not None


def test_rerunning_changes_nothing(loaded):
    """Idempotency is the defining property of a transform, as against a migration."""
    run(loaded)
    first = _counts(loaded)
    run(loaded)
    assert _counts(loaded) == first


def test_surrogate_keys_survive_a_rerun(loaded):
    """The entire reason these transforms MERGE instead of truncate-and-rebuild.

    Facts will reference `employee_key`; reissuing keys on every load would
    orphan every fact that pointed at them.
    """
    run(loaded)
    with loaded.cursor() as cursor:
        cursor.execute("SELECT employee_id, valid_from, employee_key FROM warehouse.dim_employee")
        before = dict(((row[0], row[1]), row[2]) for row in cursor.fetchall())

    run(loaded)
    with loaded.cursor() as cursor:
        cursor.execute("SELECT employee_id, valid_from, employee_key FROM warehouse.dim_employee")
        after = dict(((row[0], row[1]), row[2]) for row in cursor.fetchall())

    assert before == after


def test_a_failing_transform_rolls_back_and_is_recorded(loaded, tmp_path):
    """A half-built star schema must never be observable."""
    (tmp_path / "10_ok.sql").write_text(
        "INSERT INTO warehouse.dim_product (product_code, product_name) "
        "VALUES ('X', 'Probe') ON CONFLICT DO NOTHING;",
        encoding="utf-8",
    )
    (tmp_path / "20_bad.sql").write_text("SELECT no_such_function();", encoding="utf-8")

    with pytest.raises(Exception):  # noqa: B017 - psycopg raises a driver-specific error
        run(loaded, tmp_path)

    with loaded.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM warehouse.dim_product WHERE product_code = 'X'")
        assert cursor.fetchone()[0] == 0, "the successful step must roll back with the failed one"

        cursor.execute("SELECT status FROM warehouse.transform_run ORDER BY run_id DESC LIMIT 1")
        assert cursor.fetchone()[0] == "failed", "the failure must leave a trace"


def test_run_id_is_visible_to_the_sql(loaded):
    """Exceptions are attributed to the run that found them."""
    result = run(loaded)
    with loaded.cursor() as cursor:
        cursor.execute("SELECT DISTINCT run_id FROM warehouse.dq_exception")
        assert [row[0] for row in cursor.fetchall()] == [result.run_id]
