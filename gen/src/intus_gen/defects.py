"""Deliberate data-quality defects, with ground truth.

Clean synthetic data cannot demonstrate a data-quality capability. If every
row is already correct, a validation suite that passes proves nothing — it
would pass just as happily if it were empty. So the generators build a clean
dataset first, and then this module corrupts a known, recorded subset of it.

Every corruption appends an :class:`Injection` to a **manifest**: what was
broken, in which table, on which key, and how. That manifest is the answer to
the only question that matters about a data-quality framework — *did it catch
everything that was actually wrong?* A framework that cannot be scored against
known truth is decoration.

The defects here are chosen to be the ones that matter downstream rather than
merely the ones that are easy to inject: referential breaks the warehouse's
foreign keys must reject, SCD2 overlaps a type-2 merge must detect, and — most
pointedly — a terminated employee whose account is still being used, which is
an access-control finding, not a formatting error.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from random import Random

from intus_gen.emit import Table
from intus_gen.world import HalcyonWorld


@dataclass(frozen=True, slots=True)
class Injection:
    """One line of ground truth: what was corrupted, where, and how."""

    defect: str
    dataset: str
    target_key: str
    detail: str  # human-readable before -> after


@dataclass(frozen=True, slots=True)
class DefectSpec:
    """A named corruption, owned by the domain whose data it understands.

    Defect injection lives with the domain rather than in one central switch
    because the interesting defects are semantic: "a login after termination"
    needs to know what a termination is. A generic corrupter could only manage
    the uninteresting kind — nulls and type errors — which no realistic
    pipeline struggles with.

    ``apply`` mutates ``rows`` in place and returns what it did.
    """

    name: str
    dataset: str
    description: str
    apply: Callable[[list, Random, HalcyonWorld], list[Injection]]


def replace_at(rows: list, index: int, **changes: object) -> object:
    """Rewrite a frozen record in place, returning the original.

    Records are frozen dataclasses — worth keeping, because accidental
    mutation during generation is a far more likely bug than this deliberate
    one. Defect injection therefore substitutes a modified copy rather than
    assigning to a field.
    """
    original = rows[index]
    rows[index] = dataclasses.replace(original, **changes)
    return original


def inject(
    tables: Sequence[Table],
    specs: Sequence[DefectSpec],
    world: HalcyonWorld,
) -> tuple[Injection, ...]:
    """Apply every spec to its table, returning the combined manifest.

    Specs run in name order, each with its own seeded stream, so the manifest
    is reproducible and adding a new defect does not perturb the rows chosen
    by the existing ones.
    """
    by_name = {table.name: table for table in tables}
    injections: list[Injection] = []

    for spec in sorted(specs, key=lambda s: s.name):
        table = by_name.get(spec.dataset)
        if table is None or not table.rows:
            # A dataset can legitimately be empty at small scale; injecting
            # into nothing is a no-op, not an error.
            continue
        rng = world.seeds.stream("defects", spec.name)
        injections.extend(spec.apply(table.rows, rng, world))

    return tuple(injections)


def sample_indices(rng: Random, population: int, count: int) -> list[int]:
    """Choose up to ``count`` distinct row indices, sorted for stable manifests."""
    if population == 0:
        return []
    return sorted(rng.sample(range(population), k=min(count, population)))
