"""Compares ``intus.gold.*`` views against the legacy ``reporting.*`` views they port.

The load-bearing deliverable of Phase 3c, per the project brief: not "the
lakehouse looks right" but "the lakehouse reproduces the legacy warehouse's
numbers," checked row-for-row against one extract loaded into both systems
(``docs/BUILD_LOG.md`` records how that extract was reconciled).

Comparison, not equality-of-SQL-text: rows are sorted independently on each
side before comparing, so this does not depend on either view's own
``ORDER BY`` staying a total order (several views rank ties, which are not
guaranteed to break the same way on both platforms) — the *set* of rows is
what must agree, not the order they arrive in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

#: Same seven views as databricks_source.GOLD_VIEWS and
#: warehouse_source.REPORTING_VIEWS — the parity tool's own ordered list of
#: what to check, kept as a third copy rather than imported from either
#: source module so this module (the pure, tested core) has no dependency on
#: psycopg or network I/O.
VIEWS = (
    "rpt_headcount_trend",
    "rpt_attrition_by_department",
    "rpt_sales_pipeline_by_rep",
    "rpt_revenue_trend",
    "rpt_product_usage_trend",
    "rpt_ai_cost_by_department",
    "rpt_budget_variance",
)

#: Absolute tolerance for numeric comparison. Every ratio/percentage column
#: in the seven views is already rounded to 1-3 decimal places by the SQL
#: itself, on both platforms; this only needs to absorb IEEE-754 rounding
#: noise in that shared final digit (Databricks' round(DOUBLE, 1) can land on
#: 33.30000000000001), not paper over a real disagreement — 0.01 is an order
#: of magnitude below the coarsest rounding either view performs.
TOLERANCE = 0.01


def _normalize(value: Any) -> Any:
    """A value from either platform, reduced to a form the two can be compared in.

    ``Decimal`` (Postgres numeric, and databricks_source's DECIMAL converter)
    becomes ``float`` so it compares against Databricks' native DOUBLE
    columns without a type-mismatch special case. Dates and timestamps become
    ISO strings, since Postgres returns ``date``/``datetime`` objects and
    databricks_source already parses to the same types — normalizing both to
    strings means one order-agnostic dict lookup covers every temporal
    column instead of an isinstance check per type pairing.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _values_match(a: Any, b: Any) -> bool:
    a, b = _normalize(a), _normalize(b)
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, int | float) and isinstance(b, int | float):
        if isinstance(a, bool) or isinstance(b, bool):
            return a == b
        return abs(a - b) <= TOLERANCE
    return a == b


def _sort_element(value: Any) -> tuple[int, Any]:
    """None sorts before every real value, and is never compared against one.

    Wrapping every element in a ``(0, value)`` / ``(-1, 0)`` pair means the
    placeholder second element for a ``None`` is always compared only against
    another placeholder ``0`` — the real ``value`` in position 1 is only ever
    reached when both rows already have a non-None entry there, so it is
    always comparable (same normalized type as every other value in that
    column position).
    """
    return (0, value) if value is not None else (-1, 0)


def _sort_key(row: tuple[Any, ...]) -> tuple[tuple[int, Any], ...]:
    return tuple(_sort_element(v) for v in row)


@dataclass(frozen=True)
class RowDiff:
    warehouse_row: tuple[Any, ...]
    gold_row: tuple[Any, ...]


@dataclass(frozen=True)
class ViewParity:
    view: str
    warehouse_columns: tuple[str, ...]
    gold_columns: tuple[str, ...]
    warehouse_row_count: int
    gold_row_count: int
    diffs: tuple[RowDiff, ...] = field(default_factory=tuple)

    @property
    def column_mismatch(self) -> bool:
        return self.warehouse_columns != self.gold_columns

    @property
    def ok(self) -> bool:
        return (
            not self.column_mismatch
            and self.warehouse_row_count == self.gold_row_count
            and not self.diffs
        )


def compare_view(
    view: str,
    warehouse_columns: tuple[str, ...],
    warehouse_rows: tuple[tuple[Any, ...], ...],
    gold_columns: tuple[str, ...],
    gold_rows: tuple[tuple[Any, ...], ...],
    *,
    max_diffs: int = 8,
) -> ViewParity:
    """Pure comparison — the tested core. No network or database calls."""
    if tuple(warehouse_columns) != tuple(gold_columns):
        return ViewParity(
            view,
            tuple(warehouse_columns),
            tuple(gold_columns),
            len(warehouse_rows),
            len(gold_rows),
        )

    w_normalized = sorted(
        (tuple(_normalize(v) for v in row) for row in warehouse_rows), key=_sort_key
    )
    g_normalized = sorted((tuple(_normalize(v) for v in row) for row in gold_rows), key=_sort_key)

    diffs: list[RowDiff] = []
    for w_row, g_row in zip(w_normalized, g_normalized, strict=False):
        if not all(_values_match(a, b) for a, b in zip(w_row, g_row, strict=True)):
            diffs.append(RowDiff(w_row, g_row))
            if len(diffs) >= max_diffs:
                break

    return ViewParity(
        view,
        tuple(warehouse_columns),
        tuple(gold_columns),
        len(warehouse_rows),
        len(gold_rows),
        tuple(diffs),
    )


def all_match(results: tuple[ViewParity, ...]) -> bool:
    return all(result.ok for result in results)


def format_report(results: tuple[ViewParity, ...]) -> str:
    lines = ["parity: intus.gold.* vs. reporting.* (legacy warehouse)", ""]
    for result in results:
        status = "match" if result.ok else "MISMATCH"
        lines.append(
            f"  {result.view:<32} {status:<9} "
            f"warehouse={result.warehouse_row_count:>5}  gold={result.gold_row_count:>5}"
        )
        if result.column_mismatch:
            lines.append(f"    column mismatch: warehouse={result.warehouse_columns}")
            lines.append(f"                      gold=     {result.gold_columns}")
            continue
        for diff in result.diffs:
            lines.append(f"    warehouse: {diff.warehouse_row}")
            lines.append(f"    gold:      {diff.gold_row}")

    matched = sum(1 for result in results if result.ok)
    lines += ["", f"  views matched: {matched}/{len(results)}"]
    return "\n".join(lines)
