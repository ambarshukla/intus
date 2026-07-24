"""Command line: ``intus-lakehouse parity``."""

from __future__ import annotations

import argparse
import os
import sys

import psycopg

from intus_lakehouse.databricks_auth import DatabricksAuthError, resolve_token
from intus_lakehouse.databricks_source import GoldSourceError
from intus_lakehouse.databricks_source import fetch_view as fetch_gold_view
from intus_lakehouse.parity import VIEWS, all_match, compare_view, format_report
from intus_lakehouse.warehouse_source import WarehouseSourceError
from intus_lakehouse.warehouse_source import fetch_view as fetch_warehouse_view
from intus_warehouse import db as warehouse_db

#: Matches databricks.yml's variables.warehouse_id default — not a secret,
#: safe to hardcode, same reasoning as the bundle.
DEFAULT_WAREHOUSE_ID = "0fb6ed828ed1e874"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="intus-lakehouse",
        description="Databricks lakehouse: schema-drift checks and legacy-warehouse parity.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    parity = subparsers.add_parser(
        "parity", help="compare intus.gold.* against the legacy reporting.* views"
    )
    parity.add_argument(
        "--dsn",
        default=None,
        help="Postgres DSN (default: $INTUS_PG_DSN, else the local docker-compose database)",
    )
    parity.add_argument(
        "--warehouse-id",
        default=os.environ.get("DATABRICKS_WAREHOUSE_ID", DEFAULT_WAREHOUSE_ID),
        help="Databricks SQL warehouse id",
    )
    return parser


def run_parity(args: argparse.Namespace) -> int:
    host = os.environ.get("DATABRICKS_HOST", "").strip()
    if not host:
        print("DATABRICKS_HOST must be set — see .env.example", file=sys.stderr)
        return 1

    try:
        token = resolve_token(host)
    except DatabricksAuthError as exc:
        print(f"parity failed: {exc}", file=sys.stderr)
        return 1

    dsn = args.dsn or warehouse_db.dsn()
    try:
        with psycopg.connect(dsn) as connection:
            results = tuple(
                compare_view(
                    view,
                    *fetch_warehouse_view(connection, view),
                    *fetch_gold_view(host, token, args.warehouse_id, view),
                )
                for view in VIEWS
            )
    except (psycopg.Error, GoldSourceError, WarehouseSourceError) as exc:
        print(f"parity failed: {exc}", file=sys.stderr)
        return 1

    print(format_report(results))
    return 0 if all_match(results) else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"parity": run_parity}
    return handlers[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
