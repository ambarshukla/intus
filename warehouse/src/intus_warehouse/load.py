"""Bulk-loading the generated extracts into staging.

Truncate-and-reload, via ``COPY``, inside one transaction per run.

**Why COPY rather than INSERT.** The largest extract is roughly a million rows.
Row-by-row inserts would take minutes and generate a million round trips;
``COPY`` streams the file into the server in one statement and finishes in
seconds. It is also what a real warehouse load uses, which matters for a phase
whose purpose is to be a credible legacy system.

**Why truncate-and-reload.** Staging holds *the current extract*, not a
history — the warehouse layer is where history lives. Reloading wholesale means
a rerun is idempotent by construction, with no delete-then-insert window in
which staging holds a partial picture. The whole run is one transaction, so a
failure on the last file leaves the previous extract intact rather than an
empty schema.

**Provenance.** Each file's SHA-256 comes from the generator's manifest and is
recorded in ``staging.load_audit``, along with the seed and as-of date. The
loader verifies the hash before loading: an extract that does not match its
manifest is a corrupted or partial file, and loading it would put data in the
warehouse that no seed can reproduce.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg
from psycopg import sql

MANIFEST_FILENAME = "manifest.json"

#: Staging tables are named exactly as the generator's datasets, so no mapping
#: table is needed — and a missing table is a schema drift error rather than a
#: silently skipped file.
STAGING_SCHEMA = "staging"


class LoadError(RuntimeError):
    """The extract directory is unusable, or a file disagrees with its manifest."""


@dataclass(frozen=True, slots=True)
class ManifestFile:
    dataset: str
    path: str
    rows: int
    sha256: str
    bytes: int


@dataclass(frozen=True, slots=True)
class Manifest:
    seed: int
    scale: str
    as_of_date: date
    files: tuple[ManifestFile, ...]

    @classmethod
    def read(cls, directory: Path) -> Manifest:
        path = directory / MANIFEST_FILENAME
        if not path.is_file():
            raise LoadError(f"no {MANIFEST_FILENAME} in {directory} — run `make generate` first")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            seed=payload["seed"],
            scale=payload["scale"],
            as_of_date=date.fromisoformat(payload["as_of_date"]),
            files=tuple(
                ManifestFile(
                    dataset=entry["dataset"],
                    path=entry["path"],
                    rows=entry["rows"],
                    sha256=entry["sha256"],
                    bytes=entry["bytes"],
                )
                for entry in payload["files"]
            ),
        )


@dataclass(frozen=True, slots=True)
class LoadResult:
    dataset: str
    rows_expected: int
    rows_loaded: int

    @property
    def ok(self) -> bool:
        return self.rows_expected == self.rows_loaded


def _verify(directory: Path, entry: ManifestFile) -> Path:
    path = directory / entry.path
    if not path.is_file():
        raise LoadError(f"{entry.dataset}: {path} listed in the manifest but missing")

    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != entry.sha256:
        raise LoadError(
            f"{entry.dataset}: {path.name} does not match its manifest "
            f"(expected {entry.sha256[:12]}, found {digest[:12]}). "
            "The extract is corrupt or was partially written."
        )
    return path


def _staging_tables(connection: psycopg.Connection) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            (STAGING_SCHEMA,),
        )
        return {row[0] for row in cursor.fetchall()}


def load_directory(connection: psycopg.Connection, directory: Path) -> tuple[LoadResult, ...]:
    """Truncate and reload every dataset in ``directory``, in one transaction."""
    manifest = Manifest.read(directory)
    existing = _staging_tables(connection)

    missing = sorted(entry.dataset for entry in manifest.files if entry.dataset not in existing)
    if missing:
        raise LoadError(f"no staging table for dataset(s) {missing} — run `intus-wh migrate` first")

    results: list[LoadResult] = []

    # One transaction for the whole run: a failure on the last file leaves the
    # previous extract in place rather than an empty staging schema.
    with connection.transaction():
        for entry in sorted(manifest.files, key=lambda file: file.dataset):
            path = _verify(directory, entry)
            table = sql.Identifier(STAGING_SCHEMA, entry.dataset)

            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("TRUNCATE TABLE {}").format(table))

                copy_statement = sql.SQL(
                    "COPY {} FROM STDIN WITH (FORMAT csv, HEADER true)"
                ).format(table)
                with cursor.copy(copy_statement) as copy, path.open("rb") as handle:
                    # Streamed in chunks rather than read whole: the largest
                    # extract is hundreds of megabytes, and there is no reason
                    # for the loader's memory use to scale with the dataset.
                    while chunk := handle.read(1 << 20):
                        copy.write(chunk)

                cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(table))
                row = cursor.fetchone()
                loaded = row[0] if row else 0

                cursor.execute(
                    """
                    INSERT INTO staging.load_audit (
                        dataset, source_file, source_sha256, manifest_seed,
                        manifest_scale, as_of_date, rows_expected, rows_loaded
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        entry.dataset,
                        entry.path,
                        entry.sha256,
                        manifest.seed,
                        manifest.scale,
                        manifest.as_of_date,
                        entry.rows,
                        loaded,
                    ),
                )

            results.append(
                LoadResult(dataset=entry.dataset, rows_expected=entry.rows, rows_loaded=loaded)
            )

    # Commit explicitly rather than leaving it to the caller. On a
    # non-autocommit connection the block above is a *savepoint* inside the
    # surrounding transaction, not a transaction of its own — so without this
    # a completed load stays invisible to everyone else and a later rollback
    # anywhere on the connection would silently discard it.
    connection.commit()

    mismatched = [result for result in results if not result.ok]
    if mismatched:
        # Raised after the transaction commits, deliberately: the audit rows
        # recording the discrepancy are more useful on disk than rolled back.
        detail = ", ".join(
            f"{result.dataset} expected {result.rows_expected} loaded {result.rows_loaded}"
            for result in mismatched
        )
        raise LoadError(f"row count mismatch after load: {detail}")

    return tuple(results)
