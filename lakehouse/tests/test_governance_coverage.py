"""Every RESTRICTED-tier column is masked — checked against the generator's
own classification, not restated by hand.

`Dataset.columns_at()` in `intus_gen/sensitivity.py` says outright, in its
own docstring, that this is the governance phase's job: "every RESTRICTED
column must be covered by a mask." This is the seventh use of the D-010
"duplicate small reference data, test for drift" pattern in this project —
a RESTRICTED column added to a generator later and left unmasked here fails
CI instead of shipping quietly.

Static, not live — same reasoning as every other schema-drift test in this
suite: no Databricks credentials needed, so it runs in CI.
"""

from __future__ import annotations

from pathlib import Path

from intus_gen.domains import all_datasets
from intus_gen.sensitivity import Tier
from intus_lakehouse.governance import parse_sql_file

_GOVERNANCE_APPLY_SQL = Path(__file__).parents[2] / "lakehouse" / "sql" / "41_governance_apply.sql"

# Columns that are RESTRICTED at generation time but describe something no
# longer meaningful once the data has moved into the star schema, or that
# duplicate a column already covered under a different name. Named here
# rather than silently passing, so a reviewer can see exactly what's
# excluded and why instead of having to trust a green test.
_NOT_APPLICABLE_IN_SILVER: dict[str, str] = {}


def _restricted_column_names() -> set[str]:
    return {
        column
        for dataset in all_datasets()
        for column in dataset.columns_at(Tier.RESTRICTED)
        if column not in _NOT_APPLICABLE_IN_SILVER
    }


def test_every_restricted_column_is_masked():
    restricted = _restricted_column_names()
    masked = parse_sql_file(_GOVERNANCE_APPLY_SQL)
    missing = restricted - masked
    assert missing == set(), (
        f"RESTRICTED column(s) with no mask in 41_governance_apply.sql: {sorted(missing)}"
    )


def test_parser_finds_the_masks_actually_in_the_file():
    """Guards the parser itself: a regex that matched nothing would make the
    coverage test above vacuously pass.
    """
    masked = parse_sql_file(_GOVERNANCE_APPLY_SQL)
    assert len(masked) >= 10, f"expected at least 10 masked columns, parsed {sorted(masked)}"


def test_restricted_column_list_is_not_accidentally_empty():
    """Guards the other side: a classification import that silently returned
    nothing would also make the coverage test vacuously pass.
    """
    assert len(_restricted_column_names()) >= 10
