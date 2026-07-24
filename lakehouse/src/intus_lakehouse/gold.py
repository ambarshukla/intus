"""Parsing gold-view SQL (and, for drift-checking, the legacy reporting-view
SQL) well enough to extract each view's output column names.

Same "not a real parser" approach as bronze.py/silver.py: `CREATE TABLE`
declares its columns inline, so those two files can read a parenthesised
list; a view's columns come from its final `SELECT`'s output instead, so
this module reads that. All fourteen views in this project (seven
Postgres, seven ported) share one predictable shape by construction — a CTE
chain ending in ``)\\nSELECT\\n<column list>\\nFROM ...\\nORDER BY ...;`` —
checked by hand against every one of them, not assumed to generalise beyond
what's actually here.
"""

from __future__ import annotations

import re
from pathlib import Path

_CREATE_VIEW = re.compile(
    r"CREATE OR REPLACE VIEW (?:reporting|intus\.gold)\.(\w+) AS\b(.*?);",
    re.DOTALL,
)
#: A CTE's closing paren directly followed by the outer SELECT, with no
#: comma between them (a comma there means it's the boundary between two
#: CTEs, not the transition into the final query). `finditer` + take-the-last
#: rather than `search`, in case an earlier accidental match exists in a
#: shape this project's views don't currently have.
_FINAL_SELECT = re.compile(r"\)\s*\nSELECT\n(.*?)\nFROM\b", re.DOTALL)
_AS_ALIAS = re.compile(r"\bAS\s+(\w+)\s*$", re.IGNORECASE)


def _strip_line_comments(sql_text: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql_text.splitlines())


def _split_top_level(column_list: str) -> list[str]:
    fragments = []
    depth = 0
    current: list[str] = []
    for ch in column_list:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            fragments.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        fragments.append(tail)
    return fragments


def _column_name(fragment: str) -> str:
    """The output column name for one item in a SELECT list.

    An explicit ``AS alias`` wins; otherwise the name is whatever a database
    would infer — the fragment itself for a bare column, or the part after
    the last ``.`` for a qualified one (``department.department_code`` ->
    ``department_code``).
    """
    alias_match = _AS_ALIAS.search(fragment)
    if alias_match:
        return alias_match.group(1)
    return fragment.strip().split(".")[-1]


def parse_view_columns(sql_text: str) -> dict[str, list[str]]:
    """Map each view name in the text to its final SELECT's output column names, in order."""
    sql_text = _strip_line_comments(sql_text)
    views: dict[str, list[str]] = {}
    for view_match in _CREATE_VIEW.finditer(sql_text):
        name, body = view_match.group(1), view_match.group(2)
        select_matches = list(_FINAL_SELECT.finditer(body))
        if not select_matches:
            continue
        fragments = _split_top_level(select_matches[-1].group(1))
        views[name] = [_column_name(fragment) for fragment in fragments]
    return views


def parse_sql_file(path: Path) -> dict[str, list[str]]:
    return parse_view_columns(path.read_text(encoding="utf-8"))
