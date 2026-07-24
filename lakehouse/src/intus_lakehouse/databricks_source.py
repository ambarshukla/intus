"""Reads ``intus.gold.*`` views over the Databricks SQL Statement API.

Same approach as parvum's ``parvum_export.gold_source``, generalised from one
tenant's gold tables to this project's seven reporting views: values arrive
as strings with a typed manifest, and conversion to proper Python values
happens once, here, rather than in every caller.

Each of the seven views is at most a few hundred rows (checked live, see
``docs/BUILD_LOG.md``), so a result fitting one inline chunk is a real
assumption about this dataset's scale, not an arbitrary limit — a view that
outgrew it should fail loudly rather than silently compare a truncated slice.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

#: Same seven views as lakehouse/sql/30_gold_views.sql and
#: warehouse/sql/005_reporting_views.sql — kept as a tuple here (not derived
#: from the SQL) because parity.py needs an ordered, importable list and the
#: SQL files are not meant to be parsed for that at runtime.
GOLD_VIEWS = (
    "rpt_headcount_trend",
    "rpt_attrition_by_department",
    "rpt_sales_pipeline_by_rep",
    "rpt_revenue_trend",
    "rpt_product_usage_trend",
    "rpt_ai_cost_by_department",
    "rpt_budget_variance",
)


class GoldSourceError(RuntimeError):
    """A gold view could not be read safely."""


def _parse_timestamp(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


_CONVERTERS = {
    "STRING": str,
    "INT": int,
    "LONG": int,
    "SHORT": int,
    "DECIMAL": Decimal,
    "DOUBLE": float,
    "FLOAT": float,
    "DATE": date.fromisoformat,
    "TIMESTAMP": _parse_timestamp,
    "BOOLEAN": lambda raw: raw == "true",
}


def convert_rows(
    schema_columns: list[dict], data: list[list[str | None]]
) -> tuple[tuple[str, ...], tuple[tuple[Any, ...], ...]]:
    """Apply the manifest's types to the raw string rows. Pure — the tested core."""
    names = tuple(column["name"] for column in schema_columns)
    converters = []
    for column in schema_columns:
        type_name = column["type_name"]
        if type_name not in _CONVERTERS:
            raise GoldSourceError(f"no converter for {column['name']}: {type_name}")
        converters.append(_CONVERTERS[type_name])
    rows = tuple(
        tuple(None if raw is None else fn(raw) for fn, raw in zip(converters, row, strict=True))
        for row in data
    )
    return names, rows


def fetch_view(
    host: str, token: str, warehouse_id: str, view: str
) -> tuple[tuple[str, ...], tuple[tuple[Any, ...], ...]]:
    if view not in GOLD_VIEWS:
        raise GoldSourceError(f"not a known gold view: {view}")
    body = {
        "warehouse_id": warehouse_id,
        "wait_timeout": "50s",
        "statement": f"SELECT * FROM intus.gold.{view}",
    }
    request = urllib.request.Request(
        host.rstrip("/") + "/api/2.0/sql/statements",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        result = json.loads(response.read())

    state = result.get("status", {}).get("state")
    if state != "SUCCEEDED":
        raise GoldSourceError(
            f"query on {view} did not succeed: {json.dumps(result.get('status'))[:300]}"
        )
    manifest = result["manifest"]
    if manifest.get("total_chunk_count", 1) > 1:
        raise GoldSourceError(
            f"{view} no longer fits one inline result chunk "
            f"({manifest.get('total_row_count')} rows) — parity needs chunked reads now"
        )
    columns, rows = convert_rows(
        manifest["schema"]["columns"], result.get("result", {}).get("data_array") or []
    )
    return columns, rows
