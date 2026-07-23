"""The output contract: formatting, line endings, and hash stability."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from intus_gen.emit import Table, format_value, money, write_table, write_tables
from intus_gen.sensitivity import Column, Dataset, Tier


@dataclass(frozen=True, slots=True)
class _Row:
    identifier: str
    when: date
    at: datetime
    amount: Decimal
    ratio: float
    flag: bool
    missing: str | None


_DATASET = Dataset(
    name="probe",
    description="fixture",
    record=_Row,
    steward="Nobody",
    primary_key=("identifier",),
    columns=(
        Column("identifier", Tier.PUBLIC, "key"),
        Column("when", Tier.PUBLIC, "a date"),
        Column("at", Tier.PUBLIC, "a timestamp"),
        Column("amount", Tier.PUBLIC, "money"),
        Column("ratio", Tier.PUBLIC, "a float"),
        Column("flag", Tier.PUBLIC, "a boolean"),
        Column("missing", Tier.PUBLIC, "a nullable field"),
    ),
)


def _row() -> _Row:
    return _Row(
        identifier="X1",
        when=date(2026, 6, 30),
        at=datetime(2026, 6, 30, 14, 5, 9, 123456),
        amount=Decimal("1234.50"),
        ratio=0.125,
        flag=True,
        missing=None,
    )


def test_bool_is_not_written_as_an_integer():
    """bool subclasses int in Python, so the naive check order writes 1/0."""
    assert format_value(True) == "true"
    assert format_value(False) == "false"
    assert format_value(1) == "1"


def test_none_becomes_empty_for_postgres_copy():
    assert format_value(None) == ""


def test_dates_are_iso_and_timestamps_lose_microseconds():
    assert format_value(date(2026, 6, 30)) == "2026-06-30"
    assert format_value(datetime(2026, 6, 30, 14, 5, 9, 123456)) == "2026-06-30 14:05:09"


def test_decimal_keeps_its_scale():
    assert format_value(Decimal("1234.50")) == "1234.50"
    assert format_value(Decimal("0.000001")) == "0.000001"


def test_money_quantises_without_binary_float_error():
    """Decimal(str(x)) rather than Decimal(x): the latter captures the binary form."""
    assert money(0.1 + 0.2) == Decimal("0.30")
    assert money(2.675) == Decimal("2.68")
    assert money(0.0000004, places=6) == Decimal("0.000000")


def test_written_file_uses_lf_endings(tmp_path):
    """CRLF would make the SHA-256 differ between Windows and CI."""
    written = write_table(Table(_DATASET, [_row()]), tmp_path)
    raw = (tmp_path / written.path).read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")


def test_header_matches_the_declared_columns(tmp_path):
    write_table(Table(_DATASET, [_row()]), tmp_path)
    lines = (tmp_path / "probe.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "identifier,when,at,amount,ratio,flag,missing"
    assert lines[1] == "X1,2026-06-30,2026-06-30 14:05:09,1234.50,0.125000,true,"


def test_hash_and_row_count_are_recorded(tmp_path):
    written = write_table(Table(_DATASET, [_row(), _row()]), tmp_path)
    assert written.rows == 2
    assert len(written.sha256) == 64
    assert written.bytes == len((tmp_path / "probe.csv").read_bytes())


def test_identical_input_gives_identical_hash(tmp_path):
    first = write_table(Table(_DATASET, [_row()]), tmp_path / "a")
    second = write_table(Table(_DATASET, [_row()]), tmp_path / "b")
    assert first.sha256 == second.sha256


def test_tables_are_written_in_name_order(tmp_path):
    other = Dataset(
        name="aardvark",
        description="fixture",
        record=_Row,
        steward="Nobody",
        primary_key=("identifier",),
        columns=_DATASET.columns,
    )
    written = write_tables([Table(_DATASET, [_row()]), Table(other, [_row()])], tmp_path)
    assert [file.dataset for file in written] == ["aardvark", "probe"]
