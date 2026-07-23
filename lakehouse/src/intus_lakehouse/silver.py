"""Parsing the silver SQL well enough to check it for drift.

Same approach as ``bronze.py``: not a real SQL parser, just enough structure
reading to check the two things worth automating — that every warehouse
dimension/fact has a silver counterpart, and that the counterpart declares
the same columns. Types are not compared: the two platforms' type systems
differ enough (``text`` vs ``STRING``, ``numeric`` vs ``DECIMAL``) that a
type-level diff would be noise, not signal. Column *names*, in order, are the
part of the contract that actually matters — it is what every hand-written
`INSERT ... SELECT` in 21_silver_dimensions.sql and 22_silver_facts.sql
relies on lining up.
"""

from __future__ import annotations

import re
from pathlib import Path

_CREATE_TABLE = re.compile(
    r"CREATE TABLE IF NOT EXISTS (?:intus\.silver|warehouse)\.(\w+)\s*"
    r"\((.*?)\)\s*(?:USING DELTA)?\s*;",
    re.DOTALL,
)
_COLUMN_LINE = re.compile(r"^\s*(\w+)\s+\S")
_NOT_A_COLUMN = ("CONSTRAINT", "PRIMARY", "UNIQUE", "CHECK", "FOREIGN", "EXCLUDE")


def _strip_line_comments(sql_text: str) -> str:
    """Drop ``-- ...`` comments before structural parsing.

    A handful of the DDL's inline comments contain commas of their own (plain
    English, not SQL), which would otherwise be misread as column separators
    by the depth-tracking split below.
    """
    return "\n".join(line.split("--", 1)[0] for line in sql_text.splitlines())


def parse_table_columns(sql_text: str) -> dict[str, list[str]]:
    """Map each ``CREATE TABLE`` in the text to its declared column names, in order."""
    sql_text = _strip_line_comments(sql_text)
    tables: dict[str, list[str]] = {}
    for table_match in _CREATE_TABLE.finditer(sql_text):
        name, body = table_match.group(1), table_match.group(2)
        columns = []
        depth = 0
        current = []
        for ch in body:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                current_line = "".join(current).strip()
                current = []
                col_match = _COLUMN_LINE.match(current_line)
                if col_match and not current_line.startswith(_NOT_A_COLUMN):
                    columns.append(col_match.group(1))
            else:
                current.append(ch)
        current_line = "".join(current).strip()
        col_match = _COLUMN_LINE.match(current_line)
        if col_match and not current_line.startswith(_NOT_A_COLUMN):
            columns.append(col_match.group(1))
        tables[name] = columns
    return tables


def parse_sql_file(path: Path) -> dict[str, list[str]]:
    return parse_table_columns(path.read_text(encoding="utf-8"))
