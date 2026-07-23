"""The two hand-duplicated reference tables in 22_silver_facts.sql, kept honest.

Same D-010 pattern, same two tables, as
warehouse/tests/test_dq.py::test_ai_pricing_matches_the_generator and
::test_region_lookup_matches_the_generator — checked a second time here
because the SQL was hand-ported, not generated, and a copy-paste slip in the
port would not be caught by the Postgres-side test at all.
"""

from __future__ import annotations

import re
from pathlib import Path

from intus_gen.domains.access import _COUNTRY_BY_REGION
from intus_gen.domains.ai_usage import MODELS

_SILVER_FACTS_SQL = Path(__file__).parents[2] / "lakehouse" / "sql" / "22_silver_facts.sql"


def test_ai_pricing_matches_the_generator():
    sql = _SILVER_FACTS_SQL.read_text(encoding="utf-8")
    found = {
        name: (float(input_rate), float(output_rate))
        for name, input_rate, output_rate in re.findall(
            r"\('([\w-]+)',\s*([\d.]+),\s*([\d.]+)\)", sql
        )
    }
    for model in MODELS:
        assert model.name in found, (
            f"{model.name} from intus_gen is missing from the SQL rate table"
        )
        assert found[model.name] == (model.input_usd_per_1k, model.output_usd_per_1k), (
            f"{model.name}: SQL rate {found[model.name]} does not match generator rate "
            f"({model.input_usd_per_1k}, {model.output_usd_per_1k})"
        )


def test_region_lookup_matches_the_generator():
    sql = _SILVER_FACTS_SQL.read_text(encoding="utf-8")
    for region, countries in _COUNTRY_BY_REGION.items():
        for country in countries:
            assert f"('{country}', '{region}')" in sql, (
                f"({country}, {region}) from intus_gen.world is missing from the SQL lookup"
            )
