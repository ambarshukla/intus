"""One module per internal data domain.

The domains mirror the kinds of data a real internal data-lake team is asked
for — people, customers, product usage, AI spend, budgets, and access logs —
because those are the domains whose governance requirements genuinely differ
from one another. Compensation needs column masking; deal values need
row-level security by sales territory; security logs need short retention and
an audit trail of their own.

Every module exposes the same two things, so the CLI never needs to know what
a domain contains:

``DATASETS``
    The classified schemas the domain owns.
``build(world)``
    Clean tables generated from the shared :class:`~intus_gen.world.HalcyonWorld`.
``DEFECTS``
    Deliberate corruptions this domain understands how to inject.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from intus_gen.defects import DefectSpec
from intus_gen.emit import Table
from intus_gen.sensitivity import Dataset
from intus_gen.world import HalcyonWorld


class Domain(Protocol):
    """The shape every domain module satisfies."""

    DATASETS: tuple[Dataset, ...]
    DEFECTS: tuple[DefectSpec, ...]

    def build(self, world: HalcyonWorld) -> tuple[Table, ...]: ...


def all_domains() -> tuple[Domain, ...]:
    """Every domain, in a fixed order so generation output is stable.

    Imported here rather than at module scope to keep the import graph
    one-directional: domains import this package's types, not its registry.
    """
    from intus_gen.domains import access, ai_usage, crm, finance, hris, telemetry

    return (hris, crm, telemetry, ai_usage, finance, access)  # type: ignore[return-value]


def all_datasets() -> tuple[Dataset, ...]:
    datasets: list[Dataset] = []
    for domain in all_domains():
        datasets.extend(domain.DATASETS)
    return tuple(datasets)


def all_defects() -> tuple[DefectSpec, ...]:
    specs: list[DefectSpec] = []
    for domain in all_domains():
        specs.extend(domain.DEFECTS)
    return tuple(specs)


def build_all(world: HalcyonWorld) -> tuple[Table, ...]:
    tables: list[Table] = []
    for domain in all_domains():
        tables.extend(domain.build(world))
    return tuple(tables)


def dataset_names(datasets: Sequence[Dataset]) -> tuple[str, ...]:
    return tuple(dataset.name for dataset in datasets)
