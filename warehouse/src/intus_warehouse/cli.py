"""Command line: ``intus-wh migrate``, ``intus-wh load``, ``intus-wh status``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from intus_warehouse import __version__, db
from intus_warehouse.load import load_directory
from intus_warehouse.migrate import discover, pending, run

DEFAULT_DATA_DIR = Path("data/raw")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="intus-wh",
        description="Legacy Postgres warehouse: migrations and extract loading.",
    )
    parser.add_argument("--version", action="version", version=f"intus-wh {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    migrate = subparsers.add_parser("migrate", help="apply pending SQL migrations")
    migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="list what would be applied without applying it",
    )

    load = subparsers.add_parser("load", help="truncate and reload staging from an extract")
    load.add_argument(
        "--from",
        dest="source",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"extract directory (default: {DEFAULT_DATA_DIR})",
    )

    subparsers.add_parser("status", help="show connection and migration state")
    return parser


def run_migrate(args: argparse.Namespace) -> int:
    with db.connect() as connection:
        if args.dry_run:
            outstanding = pending(connection)
            if not outstanding:
                print("up to date")
                return 0
            for migration in outstanding:
                print(f"  would apply {migration.version}_{migration.name}")
            return 0

        applied_now = run(connection)

    if not applied_now:
        print("up to date")
    else:
        for migration in applied_now:
            print(f"  applied {migration.version}_{migration.name}")
    return 0


def run_load(args: argparse.Namespace) -> int:
    with db.connect() as connection:
        results = load_directory(connection, args.source)

    for result in results:
        print(f"  {result.dataset:<24} {result.rows_loaded:>9,} rows")
    print(f"  {'total':<24} {sum(r.rows_loaded for r in results):>9,} rows")
    return 0


def run_status(_args: argparse.Namespace) -> int:
    print(f"dsn: {db.dsn()}")
    if not db.is_available():
        print("database: unreachable (is `make up` running?)")
        return 1

    with db.connect() as connection:
        print(f"server: {db.server_version(connection).split(',')[0]}")
        outstanding = pending(connection)
        print(f"migrations: {len(discover())} on disk, {len(outstanding)} pending")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"migrate": run_migrate, "load": run_load, "status": run_status}
    return handlers[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
