"""Loading extracts into staging: correctness, provenance, and idempotency."""

from __future__ import annotations

import json
import shutil

import pytest

from intus_gen.domains import all_datasets
from intus_warehouse.load import LoadError, Manifest, load_directory


def _count(connection, table: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT count(*) FROM staging.{table}")
        return cursor.fetchone()[0]


def test_loads_every_dataset(migrated_connection, extract):
    results = load_directory(migrated_connection, extract)
    assert {result.dataset for result in results} == {d.name for d in all_datasets()}
    assert all(result.ok for result in results)


def test_row_counts_match_the_manifest(migrated_connection, extract):
    load_directory(migrated_connection, extract)
    manifest = Manifest.read(extract)
    for entry in manifest.files:
        assert _count(migrated_connection, entry.dataset) == entry.rows, entry.dataset


def test_values_survive_the_round_trip(migrated_connection, extract):
    """Spot-check that COPY landed real content, not just the right row count."""
    load_directory(migrated_connection, extract)
    with migrated_connection.cursor() as cursor:
        cursor.execute(
            "SELECT employee_id, work_email, department_code FROM staging.hr_employee_history "
            "ORDER BY employee_id, valid_from LIMIT 1"
        )
        employee_id, email, department = cursor.fetchone()

    assert employee_id.startswith("E")
    assert email.endswith("@halcyon.example")
    assert department


def test_nulls_land_as_nulls_not_empty_strings(migrated_connection, extract):
    """The generator writes NULL as an empty field; COPY must read it back as NULL.

    Getting this wrong is quiet and expensive: every `IS NULL` in the transform
    layer would silently match nothing.
    """
    load_directory(migrated_connection, extract)
    with migrated_connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM staging.hr_employee_history WHERE valid_to IS NULL")
        current_rows = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM staging.hr_employee_history WHERE valid_to = ''")
        empty_strings = cursor.fetchone()[0]

    assert current_rows > 0, "every employee should have one open-ended span"
    assert empty_strings == 0


def test_reloading_is_idempotent(migrated_connection, extract):
    """Truncate-and-reload: running twice leaves the same rows, not double."""
    first = load_directory(migrated_connection, extract)
    second = load_directory(migrated_connection, extract)
    assert {r.dataset: r.rows_loaded for r in first} == {r.dataset: r.rows_loaded for r in second}


def test_load_audit_records_provenance(migrated_connection, extract):
    load_directory(migrated_connection, extract)
    manifest = Manifest.read(extract)

    with migrated_connection.cursor() as cursor:
        cursor.execute(
            "SELECT dataset, source_sha256, manifest_seed, manifest_scale, "
            "as_of_date, rows_expected, rows_loaded FROM staging.load_audit"
        )
        rows = cursor.fetchall()

    assert len(rows) == len(manifest.files)
    by_dataset = {row[0]: row for row in rows}
    for entry in manifest.files:
        _, sha, seed, scale, as_of, expected, loaded = by_dataset[entry.dataset]
        assert sha == entry.sha256
        assert seed == manifest.seed
        assert scale == manifest.scale
        assert as_of == manifest.as_of_date
        assert expected == loaded == entry.rows


def test_audit_accumulates_across_loads(migrated_connection, extract):
    """Staging is replaced each run; its audit trail is not."""
    load_directory(migrated_connection, extract)
    load_directory(migrated_connection, extract)
    with migrated_connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM staging.load_audit")
        assert cursor.fetchone()[0] == 2 * len(all_datasets())


# --------------------------------------------------------------------------
# Failure modes
# --------------------------------------------------------------------------


def test_missing_manifest_is_a_clear_error(migrated_connection, tmp_path):
    with pytest.raises(LoadError, match="make generate"):
        load_directory(migrated_connection, tmp_path)


def test_corrupted_file_is_rejected(migrated_connection, extract, tmp_path):
    """A file that disagrees with its manifest must not reach the warehouse."""
    corrupted = tmp_path / "corrupted"
    shutil.copytree(extract, corrupted)
    target = corrupted / "crm_account.csv"
    target.write_text(target.read_text(encoding="utf-8") + "tampered,,,,,,,,\n", encoding="utf-8")

    with pytest.raises(LoadError, match="does not match its manifest"):
        load_directory(migrated_connection, corrupted)


def test_missing_file_is_rejected(migrated_connection, extract, tmp_path):
    incomplete = tmp_path / "incomplete"
    shutil.copytree(extract, incomplete)
    (incomplete / "crm_invoice.csv").unlink()

    with pytest.raises(LoadError, match="missing"):
        load_directory(migrated_connection, incomplete)


def test_unknown_dataset_is_rejected(migrated_connection, extract, tmp_path):
    """A manifest naming a dataset with no staging table means schema drift."""
    drifted = tmp_path / "drifted"
    shutil.copytree(extract, drifted)
    manifest_path = drifted / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["files"].append(
        {"dataset": "not_a_table", "path": "nope.csv", "rows": 0, "bytes": 0, "sha256": "0" * 64}
    )
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(LoadError, match="no staging table"):
        load_directory(migrated_connection, drifted)


def test_a_failed_load_leaves_the_previous_extract_intact(migrated_connection, extract, tmp_path):
    """One transaction for the whole run, so a late failure is not half a load."""
    load_directory(migrated_connection, extract)
    before = _count(migrated_connection, "crm_account")

    broken = tmp_path / "broken"
    shutil.copytree(extract, broken)
    (broken / "usage_daily.csv").write_text("garbage\n", encoding="utf-8")

    with pytest.raises(LoadError):
        load_directory(migrated_connection, broken)
    migrated_connection.rollback()

    assert _count(migrated_connection, "crm_account") == before
