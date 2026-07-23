"""End-to-end: the command line, the files it writes, and the manifest."""

from __future__ import annotations

import json

import pytest

from intus_gen.catalog import render_markdown
from intus_gen.cli import main
from intus_gen.domains import all_datasets
from intus_gen.manifest import MANIFEST_FILENAME


def _generate(out, seed: int = 99, extra: list[str] | None = None) -> dict:
    argv = ["generate", "--scale", "small", "--seed", str(seed), "--out", str(out)]
    assert main(argv + (extra or [])) == 0
    return json.loads((out / MANIFEST_FILENAME).read_text(encoding="utf-8"))


def test_generate_writes_a_file_per_dataset(tmp_path):
    manifest = _generate(tmp_path)
    written = {file["dataset"] for file in manifest["files"]}
    assert written == {dataset.name for dataset in all_datasets()}
    for file in manifest["files"]:
        assert (tmp_path / file["path"]).exists()


def test_manifest_hashes_match_the_files_on_disk(tmp_path):
    import hashlib

    manifest = _generate(tmp_path)
    for file in manifest["files"]:
        raw = (tmp_path / file["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == file["sha256"]
        assert len(raw) == file["bytes"]


def test_two_runs_produce_identical_manifests(tmp_path):
    """The core reproducibility claim, checked the cheap way: compare manifests."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    assert _generate(first) == _generate(second)
    assert (first / MANIFEST_FILENAME).read_bytes() == (second / MANIFEST_FILENAME).read_bytes()


def test_a_different_seed_changes_the_output(tmp_path):
    first = _generate(tmp_path / "a", seed=1)
    second = _generate(tmp_path / "b", seed=2)
    assert first["files"] != second["files"]


def test_manifest_records_no_wall_clock_time(tmp_path):
    """Deliberate: a timestamp would defeat comparing manifests for equality."""
    manifest = _generate(tmp_path)
    serialised = json.dumps(manifest)
    assert "generated_at" not in serialised
    assert "timestamp" not in serialised
    assert set(manifest) == {
        "generator_version",
        "seed",
        "scale",
        "as_of_date",
        "start_date",
        "defects_enabled",
        "total_rows",
        "files",
        "classification",
        "injections",
    }


def test_no_defects_flag_produces_clean_data(tmp_path):
    dirty = _generate(tmp_path / "dirty")
    clean = _generate(tmp_path / "clean", extra=["--no-defects"])

    assert dirty["injections"]
    assert clean["injections"] == []
    assert clean["defects_enabled"] is False
    assert clean["total_rows"] < dirty["total_rows"]  # duplicates add rows


def test_manifest_carries_the_classification(tmp_path):
    manifest = _generate(tmp_path)
    by_name = {entry["dataset"]: entry for entry in manifest["classification"]}

    assert by_name["hr_compensation"]["max_tier"] == "restricted"
    assert "annual_salary_usd" in by_name["hr_compensation"]["columns_by_tier"]["restricted"]
    assert by_name["sec_access_event"]["retention_days"] == 180
    assert by_name["crm_account"]["primary_key"] == ["account_id"]


def test_as_of_date_is_honoured(tmp_path):
    manifest = _generate(tmp_path, extra=["--as-of", "2026-03-31"])
    assert manifest["as_of_date"] == "2026-03-31"


def test_bad_as_of_date_is_a_usage_error(tmp_path):
    with pytest.raises(SystemExit):
        main(["generate", "--scale", "small", "--out", str(tmp_path), "--as-of", "31/03/2026"])


def test_catalog_command_matches_the_renderer(tmp_path):
    target = tmp_path / "nested" / "data-catalog.md"
    assert main(["catalog", "--out", str(target)]) == 0
    assert target.read_text(encoding="utf-8") == render_markdown(all_datasets())


def test_committed_catalog_is_current():
    """The docs copy is generated; a stale one would misstate the classification."""
    from pathlib import Path

    committed = Path(__file__).resolve().parents[2] / "docs" / "data-catalog.md"
    assert committed.exists(), "docs/data-catalog.md has not been generated"
    assert committed.read_text(encoding="utf-8") == render_markdown(all_datasets())


def test_a_subcommand_is_required():
    with pytest.raises(SystemExit):
        main([])
