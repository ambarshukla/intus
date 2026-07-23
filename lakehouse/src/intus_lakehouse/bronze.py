"""Parsing ``lakehouse/sql/10_bronze.sql`` well enough to check it for drift.

Not a SQL parser: the bronze file has one shape by construction (a sequence of
``CREATE OR REPLACE TABLE intus.bronze.<dataset> AS SELECT * FROM
read_files(..., schema => '<col> <TYPE>, ...')`` statements), and this reads
exactly that shape with two regexes. A real parser would tolerate SQL this
file never contains; a narrower reader is *more* honest about what the drift
test actually checks, and breaks loudly (regex finds nothing) if the file's
shape ever changes enough to need one.
"""

from __future__ import annotations

import re
from pathlib import Path

_TABLE = re.compile(r"CREATE OR REPLACE TABLE intus\.bronze\.(\w+)")
_SCHEMA = re.compile(r"schema\s*=>\s*'([^']*)'")


def parse_bronze_tables(sql_path: Path) -> dict[str, list[str]]:
    """Map each bronze table name to its declared column names, in order."""
    text = sql_path.read_text(encoding="utf-8")

    tables: dict[str, list[str]] = {}
    for statement in text.split(";"):
        table_match = _TABLE.search(statement)
        schema_match = _SCHEMA.search(statement)
        if not table_match or not schema_match:
            continue
        columns = [
            field.strip().split()[0] for field in schema_match.group(1).split(",") if field.strip()
        ]
        tables[table_match.group(1)] = columns

    return tables
