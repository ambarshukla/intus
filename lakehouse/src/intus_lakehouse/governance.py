"""Parsing 41_governance_apply.sql well enough to answer one question: which
columns actually get a mask attached.

Same "not a real parser" approach as gold.py/silver.py's schema-drift
helpers — a regex over the one shape this file's `ALTER COLUMN ... SET MASK`
statements actually take, not a general SQL parser.
"""

from __future__ import annotations

import re
from pathlib import Path

_SET_MASK = re.compile(
    r"ALTER TABLE\s+\S+\s*\n?\s*ALTER COLUMN\s+(\w+)\s+SET MASK",
    re.IGNORECASE,
)


def parse_masked_columns(sql_text: str) -> set[str]:
    """Names of every column that gets `SET MASK`'d somewhere in the file.

    Column *names*, not (table, column) pairs — the same name-based
    comparison test_gold_schema.py already uses for its drift check, and
    sufficient here since RESTRICTED column names don't collide across
    datasets with different meanings (e.g. `employee_id` means the same
    thing, "requesting/acting individual", everywhere it appears RESTRICTED).
    """
    return {match.group(1) for match in _SET_MASK.finditer(sql_text)}


def parse_sql_file(path: Path) -> set[str]:
    return parse_masked_columns(path.read_text(encoding="utf-8"))
