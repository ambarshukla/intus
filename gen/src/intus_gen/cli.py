"""Command line entry point: ``intus-gen generate`` and ``intus-gen catalog``.

``--as-of`` defaults to a fixed date rather than today's. That looks odd until
you consider what the alternative does: a dataset whose contents depend on the
day it was generated cannot be compared against one generated yesterday, so
every parity check between the legacy warehouse and the lakehouse would fail
for reasons that have nothing to do with either. The as-of date is an input,
and inputs belong on the command line.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from intus_gen import __version__
from intus_gen.catalog import render_markdown
from intus_gen.defects import inject
from intus_gen.domains import all_datasets, all_defects, build_all
from intus_gen.emit import write_tables
from intus_gen.manifest import build_manifest
from intus_gen.world import Scale, build_world

#: The dataset's notional extract date. Fixed so output is a function of the
#: command line alone.
DEFAULT_AS_OF = date(2026, 6, 30)
DEFAULT_SEED = 20260723


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as error:  # argparse renders this as a usage error
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {text!r}") from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="intus-gen",
        description="Deterministic synthetic data generators for Halcyon.",
    )
    parser.add_argument("--version", action="version", version=f"intus-gen {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="generate the Halcyon dataset")
    generate.add_argument(
        "--scale",
        type=Scale,
        choices=tuple(Scale),
        default=Scale.FULL,
        help="dataset size (default: full)",
    )
    generate.add_argument("--seed", type=int, default=DEFAULT_SEED, help="run seed")
    generate.add_argument("--out", type=Path, required=True, help="output directory")
    generate.add_argument(
        "--as-of",
        type=_parse_date,
        default=DEFAULT_AS_OF,
        help=f"extract date (default: {DEFAULT_AS_OF.isoformat()})",
    )
    generate.add_argument(
        "--no-defects",
        action="store_true",
        help="generate clean data with no injected data-quality defects",
    )

    catalog = subparsers.add_parser("catalog", help="write the sensitivity catalog")
    catalog.add_argument("--out", type=Path, required=True, help="markdown file to write")

    return parser


def run_generate(args: argparse.Namespace) -> int:
    world = build_world(seed=args.seed, scale=args.scale, end_date=args.as_of)
    tables = build_all(world)

    injections = () if args.no_defects else inject(tables, all_defects(), world)

    files = write_tables(tables, args.out)
    manifest = build_manifest(
        seed=args.seed,
        scale=args.scale.value,
        as_of_date=args.as_of,
        start_date=world.start_date,
        defects_enabled=not args.no_defects,
        files=files,
        injections=injections,
        datasets=all_datasets(),
    )
    manifest_path = manifest.write(args.out)

    print(f"scale={args.scale.value} seed={args.seed} as-of={args.as_of.isoformat()}")
    for file in files:
        print(f"  {file.dataset:<24} {file.rows:>9,} rows  {file.sha256[:12]}")
    print(f"  {'defects injected':<24} {len(injections):>9,}")
    print(f"  {'total':<24} {manifest.total_rows:>9,} rows -> {manifest_path}")
    return 0


def run_catalog(args: argparse.Namespace) -> int:
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_markdown(all_datasets()), encoding="utf-8", newline="\n")
    print(f"wrote {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "generate":
        return run_generate(args)
    return run_catalog(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
