"""Shared fixtures.

Everything runs at ``Scale.SMALL`` against the *real* generators — no mocks
and no hand-written fixture data. A test suite that exercised a stub would be
verifying the stub, and the invariants worth asserting here (referential
integrity, SCD2 contiguity, reproducibility) are exactly the ones only real
generated data can violate.

The world and the clean tables are session-scoped because building them is the
expensive part; the defective tables are function-scoped because injection
mutates rows in place, and a test that received someone else's corrupted
tables would fail for reasons it never asked about.
"""

from __future__ import annotations

import copy
from datetime import date

import pytest

from intus_gen.defects import inject
from intus_gen.domains import all_defects, build_all
from intus_gen.world import Scale, build_world

TEST_SEED = 4242
AS_OF = date(2026, 6, 30)


@pytest.fixture(scope="session")
def test_seed() -> int:
    return TEST_SEED


@pytest.fixture(scope="session")
def as_of() -> date:
    return AS_OF


@pytest.fixture(scope="session")
def world():
    return build_world(seed=TEST_SEED, scale=Scale.SMALL, end_date=AS_OF)


@pytest.fixture(scope="session")
def _clean_tables(world):
    return build_all(world)


@pytest.fixture
def clean_tables(_clean_tables):
    """Defect-free tables. Deep-copied so a mutating test cannot leak."""
    return copy.deepcopy(_clean_tables)


@pytest.fixture
def clean_by_name(clean_tables):
    return {table.name: table for table in clean_tables}


@pytest.fixture
def injected(world, clean_tables):
    """Tables with defects applied, plus the ground-truth manifest."""
    injections = inject(clean_tables, all_defects(), world)
    return clean_tables, injections


@pytest.fixture
def injected_by_name(injected):
    tables, injections = injected
    return {table.name: table for table in tables}, injections
