"""Turning generated records into files, reproducibly.

The output contract is narrow on purpose: UTF-8 CSV, LF line endings, ISO-8601
dates, empty string for NULL. Every one of those is a decision that a defaulted
implementation would get wrong for this project:

- **LF, always.** ``csv.writer`` defaults to ``\\r\\n``, so the same generator
  would produce different bytes — and therefore a different SHA-256 — on
  Windows and Linux. A manifest hash that only matches on the machine that
  wrote it verifies nothing.
- **Empty string for NULL.** This is what Postgres ``COPY ... WITH (FORMAT
  csv)`` reads back as NULL by default, and the warehouse phase loads these
  files with exactly that.
- **ISO-8601 dates.** Unambiguous across the two engines this data has to
  survive (Postgres and Spark), unlike any locale-dependent format.

CSV rather than Parquet because the legacy warehouse is the first consumer, and
a flat delimited extract is what a legacy warehouse is actually fed. Parquet
arrives with the lakehouse phase, where columnar storage does something.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from intus_gen.sensitivity import Dataset

_NULL = ""


def money(value: float | Decimal, places: int = 2) -> Decimal:
    """Quantise an amount to a fixed number of decimal places, as ``Decimal``.

    Monetary columns are ``Decimal``, never ``float``. Binary floating point
    cannot represent most decimal fractions exactly, so a float pipeline
    accumulates error that shows up precisely where it is least welcome — a
    budget-variance report that misses by a cent, or a parity check between
    the legacy warehouse and the lakehouse that fails on rounding rather than
    on logic. Postgres ``numeric`` and Spark ``decimal`` both preserve this
    exactly; ``double`` does not.

    Going via ``str`` is deliberate: ``Decimal(0.1)`` captures the binary
    approximation in full, while ``Decimal("0.1")`` is the value meant.
    """
    exponent = Decimal(1).scaleb(-places)
    return Decimal(str(value)).quantize(exponent, rounding=ROUND_HALF_UP)


@dataclass(slots=True)
class Table:
    """A dataset paired with the rows generated for it.

    Mutable, because defect injection rewrites rows in place after generation
    — the clean table is built first, then corrupted, which is what keeps the
    defect manifest an honest record of the difference between the two.
    """

    dataset: Dataset
    rows: list

    @property
    def name(self) -> str:
        return self.dataset.name


@dataclass(frozen=True, slots=True)
class WrittenFile:
    """One output file, as recorded in the run manifest."""

    dataset: str
    path: str
    rows: int
    sha256: str
    bytes: int


def format_value(value: object) -> str:
    """Render one field for CSV.

    ``bool`` is checked before ``int`` deliberately: in Python ``bool`` is a
    subclass of ``int``, so the obvious ordering silently writes ``1``/``0``
    for every boolean and the warehouse ends up with an integer column where a
    flag belongs.
    """
    if value is None:
        return _NULL
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        # Second precision: these are business events, and sub-second noise
        # would imply a fidelity the generator does not have.
        return value.replace(microsecond=0).isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return f"{value:f}"
    if isinstance(value, float):
        # Fixed precision rather than repr(): float repr is stable in modern
        # CPython, but pinning it here makes the byte-level output contract
        # independent of that guarantee.
        return f"{value:.6f}"
    return str(value)


def write_table(table: Table, out_dir: Path) -> WrittenFile:
    """Write one table to ``<out_dir>/<name>.csv`` and hash the result."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{table.name}.csv"
    header = table.dataset.header()

    # newline="" is required by the csv module: without it, Python's own
    # newline translation runs on top of the writer's line terminator and
    # produces \r\r\n on Windows.
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        for row in table.rows:
            values = dataclasses.astuple(row) if dataclasses.is_dataclass(row) else tuple(row)
            writer.writerow([format_value(value) for value in values])

    raw = path.read_bytes()
    return WrittenFile(
        dataset=table.name,
        path=path.name,
        rows=len(table.rows),
        sha256=hashlib.sha256(raw).hexdigest(),
        bytes=len(raw),
    )


def write_tables(tables: Sequence[Table], out_dir: Path) -> tuple[WrittenFile, ...]:
    """Write every table, in name order so the manifest is stable."""
    return tuple(write_table(table, out_dir) for table in sorted(tables, key=lambda t: t.name))
