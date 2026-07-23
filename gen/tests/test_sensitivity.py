"""The classification registry must not be able to drift from the schemas.

These tests are the mechanism that lets every later governance claim rest on
the catalog: if a column can exist without a declared tier, the catalog is a
best-effort document rather than a specification.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from intus_gen.domains import all_datasets
from intus_gen.sensitivity import Column, Dataset, SchemaMismatchError, Tier


@dataclass(frozen=True, slots=True)
class _Row:
    identifier: str
    secret: int


def _dataset(
    columns: tuple[Column, ...], primary_key: tuple[str, ...] = ("identifier",)
) -> Dataset:
    return Dataset(
        name="probe",
        description="fixture",
        record=_Row,
        steward="Nobody",
        primary_key=primary_key,
        columns=columns,
    )


def test_accepts_matching_columns():
    dataset = _dataset(
        (
            Column("identifier", Tier.INTERNAL, "key"),
            Column("secret", Tier.RESTRICTED, "value"),
        )
    )
    assert dataset.header() == ("identifier", "secret")


def test_rejects_missing_column():
    with pytest.raises(SchemaMismatchError, match="missing"):
        _dataset((Column("identifier", Tier.INTERNAL, "key"),))


def test_rejects_unknown_column():
    with pytest.raises(SchemaMismatchError, match="unexpected"):
        _dataset(
            (
                Column("identifier", Tier.INTERNAL, "key"),
                Column("secret", Tier.RESTRICTED, "value"),
                Column("invented", Tier.PUBLIC, "not a field"),
            )
        )


def test_rejects_wrong_order():
    """Order is part of the contract: it is the CSV header order."""
    with pytest.raises(SchemaMismatchError, match="wrong order"):
        _dataset(
            (
                Column("secret", Tier.RESTRICTED, "value"),
                Column("identifier", Tier.INTERNAL, "key"),
            )
        )


def test_rejects_unknown_primary_key():
    with pytest.raises(SchemaMismatchError, match="primary key"):
        _dataset(
            (
                Column("identifier", Tier.INTERNAL, "key"),
                Column("secret", Tier.RESTRICTED, "value"),
            ),
            primary_key=("nonexistent",),
        )


def test_tier_rank_is_restriction_order_not_alphabetical():
    """Guards the reason `rank` exists at all: "confidential" < "internal" as text."""
    assert Tier.PUBLIC.rank < Tier.INTERNAL.rank < Tier.CONFIDENTIAL.rank < Tier.RESTRICTED.rank
    assert Tier.CONFIDENTIAL.value < Tier.INTERNAL.value  # the trap being avoided


def test_max_tier_picks_the_most_restrictive():
    dataset = _dataset(
        (
            Column("identifier", Tier.PUBLIC, "key"),
            Column("secret", Tier.CONFIDENTIAL, "value"),
        )
    )
    assert dataset.max_tier is Tier.CONFIDENTIAL


# --------------------------------------------------------------------------
# The real catalog
# --------------------------------------------------------------------------


def test_every_dataset_name_is_unique():
    names = [dataset.name for dataset in all_datasets()]
    assert len(names) == len(set(names))


def test_every_column_has_a_description():
    for dataset in all_datasets():
        for column in dataset.columns:
            assert column.description.strip(), f"{dataset.name}.{column.name} has no description"


def test_restricted_columns_exist_to_be_protected():
    """The governance phase needs something to mask; assert the targets are there."""
    restricted = {
        (dataset.name, column)
        for dataset in all_datasets()
        for column in dataset.columns_at(Tier.RESTRICTED)
    }
    assert ("hr_compensation", "annual_salary_usd") in restricted
    assert ("hr_performance_review", "rating") in restricted
    assert ("sec_access_event", "source_ip") in restricted


def test_every_dataset_has_a_steward():
    for dataset in all_datasets():
        assert dataset.steward.strip(), f"{dataset.name} has no steward"
