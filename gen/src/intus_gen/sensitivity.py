"""Data classification: sensitivity tiers declared alongside the schema.

The governance layer this project builds towards has to be *testable*. A claim
like "compensation is masked for everyone outside HR" can only be verified
against a machine-readable statement of which columns hold compensation — so
classification is declared here, in code, next to the records it describes, and
travels with the data from the moment it is generated.

Two shapes were considered and rejected:

- **Per-row tags.** Attaching a tier to each row models something real
  catalogs do not do: classification is a property of a *column*, not of the
  values in it. It would also have invented a mechanism no downstream engine
  (Unity Catalog, Postgres RLS, a BI semantic layer) can consume.
- **Documenting tiers in prose only.** Free text cannot be asserted against.
  Every access-control test in the governance phase would have to restate the
  classification, and the restatement is what would drift.

Instead a :class:`Dataset` names its columns and their tiers, and validates on
construction that they match the record type's fields exactly. Drift between
schema and classification is therefore not a bug to be caught in review — it is
an import-time error.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import StrEnum


class Tier(StrEnum):
    """How sensitive a column is, and therefore what protects it.

    The four tiers are ordered by increasing restriction, and each names a
    concrete downstream control so the label is never merely decorative:

    ``PUBLIC``
        Safe outside the company. Product names, regions, fiscal calendars.
    ``INTERNAL``
        Any employee may see it. Headcount by department, account names,
        aggregate product usage.
    ``CONFIDENTIAL``
        Restricted to a business function by row-level security. Deal values
        to Sales, budget variance to Finance, per-account revenue to both.
    ``RESTRICTED``
        Column-masked even for holders of the surrounding rows. Salary, bonus,
        performance rating, home address, individual named activity.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"

    @property
    def rank(self) -> int:
        """Position in the restriction order, lowest first.

        ``StrEnum`` compares as text, where "confidential" < "internal" —
        alphabetical order that reads like an ordering but is not the one
        anyone means. Comparisons go through this instead.
        """
        return _TIER_ORDER.index(self)


_TIER_ORDER: tuple[Tier, ...] = (
    Tier.PUBLIC,
    Tier.INTERNAL,
    Tier.CONFIDENTIAL,
    Tier.RESTRICTED,
)


@dataclass(frozen=True, slots=True)
class Column:
    """One column: its name, its classification, and why it is classified so."""

    name: str
    tier: Tier
    description: str


class SchemaMismatchError(ValueError):
    """The declared columns do not match the record type's fields."""


@dataclass(frozen=True, slots=True)
class Dataset:
    """A generated table: its record type, key, and per-column classification.

    Validates on construction, so a field added to a record without a matching
    classification fails at import rather than producing an unclassified column
    that quietly reaches the warehouse.
    """

    name: str
    description: str
    #: The dataclass whose fields this dataset's columns describe.
    record: type
    #: Business function accountable for the data — the approver an access
    #: review routes to, and the owner recorded in the catalog.
    steward: str
    primary_key: tuple[str, ...]
    columns: tuple[Column, ...]
    #: Days of history retained. ``None`` means full history; security logs
    #: deliberately carry a shorter window, as they do in practice.
    retention_days: int | None = None

    def __post_init__(self) -> None:
        if not dataclasses.is_dataclass(self.record):
            raise SchemaMismatchError(f"{self.name}: record type must be a dataclass")

        declared = tuple(column.name for column in self.columns)
        actual = tuple(field.name for field in dataclasses.fields(self.record))

        if len(set(declared)) != len(declared):
            duplicates = sorted({name for name in declared if declared.count(name) > 1})
            raise SchemaMismatchError(f"{self.name}: duplicate column(s) {duplicates}")

        # Order matters as well as membership: the column order here is the
        # column order written to CSV, so keeping it locked to field order
        # means the header can never disagree with the rows beneath it.
        if declared != actual:
            missing = sorted(set(actual) - set(declared))
            extra = sorted(set(declared) - set(actual))
            detail = (
                f"missing={missing} unexpected={extra}" if (missing or extra) else "wrong order"
            )
            raise SchemaMismatchError(
                f"{self.name}: declared columns do not match "
                f"{self.record.__name__} fields ({detail})"
            )

        unknown_key = sorted(set(self.primary_key) - set(actual))
        if unknown_key:
            raise SchemaMismatchError(
                f"{self.name}: primary key names unknown column(s) {unknown_key}"
            )

    @property
    def max_tier(self) -> Tier:
        """The most restrictive tier present — how the table as a whole routes."""
        return max((column.tier for column in self.columns), key=lambda tier: tier.rank)

    def columns_at(self, tier: Tier) -> tuple[str, ...]:
        """Names of columns classified exactly at ``tier``.

        The governance phase asserts against this: every ``RESTRICTED`` column
        must be covered by a mask, and the list of them comes from here rather
        than from a hand-maintained copy.
        """
        return tuple(column.name for column in self.columns if column.tier is tier)

    def header(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)
