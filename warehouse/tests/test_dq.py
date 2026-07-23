"""Scoring detections against the generator's seeded truth.

This is the test that makes the data-quality layer a measurement rather than a
gesture. It is also the only place where Phase 1 and Phase 2 are checked
against each other: the generator's `target_key` format and the transform's
must agree exactly, and nothing else in either codebase would notice if they
drifted.
"""

from __future__ import annotations

import pytest

from intus_warehouse.dq import format_scorecard, latest_run_id, score
from intus_warehouse.load import load_directory
from intus_warehouse.transform import TRANSFORM_DIR, run

#: Every defect type the generator can seed. Listed explicitly, rather than
#: derived from intus_gen at import time, so that a new defect type added on
#: the generator side without a matching warehouse rule shows up here as a
#: failing test rather than as a quietly incomplete scorecard.
IMPLEMENTED_RULES = {
    "HR_OVERLAPPING_SPAN",
    "HR_ORPHAN_MANAGER",
    "HR_MISSING_TERMINATION_REASON",
    "HR_SALARY_OUTLIER",
    "CRM_DUPLICATE_ACCOUNT",
    "CRM_ORPHAN_OPPORTUNITY",
    "CRM_CLOSED_BEFORE_CREATED",
    "CRM_NEGATIVE_INVOICE",
    "USAGE_DUPLICATE_EVENT",
    "USAGE_UNKNOWN_ACCOUNT",
    "USAGE_NEGATIVE_SESSIONS",
    "AI_COST_MISMATCH",
    "AI_UNKNOWN_MODEL",
    "SEC_MISSING_ACTOR",
    "SEC_LOGIN_AFTER_TERMINATION",
    "SEC_IMPOSSIBLE_TRAVEL",
    "FIN_ORPHAN_COST_CENTER",
    "FIN_CLOSED_PERIOD_POSTING",
    "FIN_UNAUTHORISED_APPROVER",
}


@pytest.fixture
def scored(migrated_connection, extract):
    load_directory(migrated_connection, extract)
    run(migrated_connection)
    return score(migrated_connection, extract), migrated_connection


def test_every_implemented_rule_has_perfect_recall(scored):
    """The point of the whole exercise: catch every seeded defect in scope."""
    scorecard, _ = scored
    for rule in scorecard.rules:
        if rule.rule_code not in IMPLEMENTED_RULES:
            continue
        assert rule.seeded > 0, f"{rule.rule_code} seeded nothing to detect"
        assert rule.missed == 0, f"{rule.rule_code} missed {rule.missed} seeded defect(s)"
        assert rule.recall == 1.0


def test_no_implemented_rule_raises_false_positives(scored):
    """Recall alone is meaningless — a rule that rejects everything scores 100%."""
    scorecard, _ = scored
    for rule in scorecard.rules:
        if rule.rule_code not in IMPLEMENTED_RULES:
            continue
        assert rule.false_positives == 0, (
            f"{rule.rule_code} raised {rule.false_positives} exception(s) "
            "that no seeded defect explains"
        )


def test_implemented_rules_are_exactly_the_expected_set(scored):
    scorecard, _ = scored
    reported = {rule.rule_code for rule in scorecard.implemented}
    assert reported == IMPLEMENTED_RULES


def test_every_defect_type_is_covered(scored):
    """The payoff of Phase 2c: no defect type is left as 'not implemented'.

    format_scorecard still knows how to render an unimplemented rule (see
    test_scorecard_renders_an_unimplemented_rule below, using a synthetic
    scorecard) — that reporting path matters for whenever the *next* new
    defect type is added and briefly uncovered, even though nothing in the
    current, fully-covered warehouse exercises it.
    """
    scorecard, _ = scored
    assert scorecard.implemented == scorecard.rules
    assert {rule.rule_code for rule in scorecard.rules} == IMPLEMENTED_RULES


def test_strict_mode_would_pass(scored):
    scorecard, _ = scored
    assert scorecard.all_implemented_rules_perfect


def test_dispositions_are_used_as_designed(scored):
    """Reject / repair / flag are distinct decisions, not synonyms."""
    _, connection = scored
    with connection.cursor() as cursor:
        cursor.execute("SELECT rule_code, disposition, severity FROM warehouse.dq_exception")
        rows = {(code, disposition, severity) for code, disposition, severity in cursor.fetchall()}

    by_rule = {code: (disposition, severity) for code, disposition, severity in rows}
    assert by_rule["HR_OVERLAPPING_SPAN"] == ("rejected", "error")
    assert by_rule["HR_ORPHAN_MANAGER"] == ("repaired", "warning")
    assert by_rule["HR_MISSING_TERMINATION_REASON"] == ("flagged", "warning")
    assert by_rule["CRM_DUPLICATE_ACCOUNT"] == ("rejected", "error")
    assert by_rule["CRM_ORPHAN_OPPORTUNITY"] == ("rejected", "error")
    assert by_rule["USAGE_DUPLICATE_EVENT"] == ("rejected", "error")
    assert by_rule["USAGE_UNKNOWN_ACCOUNT"] == ("rejected", "error")
    assert by_rule["AI_UNKNOWN_MODEL"] == ("flagged", "warning")
    # The security findings are the clearest case for severity and
    # disposition being independent axes: both stay in the fact table
    # (flagged, never rejected — an ITGC exception that vanishes on
    # discovery is not evidence of anything) while being the most severe
    # thing this warehouse detects.
    assert by_rule["SEC_LOGIN_AFTER_TERMINATION"] == ("flagged", "error")
    assert by_rule["SEC_MISSING_ACTOR"] == ("flagged", "error")


def test_scoring_without_a_run_is_an_error(migrated_connection, extract):
    with pytest.raises(ValueError, match="no successful transform run"):
        score(migrated_connection, extract)


def test_latest_run_id_ignores_failed_runs(migrated_connection, extract, tmp_path):
    load_directory(migrated_connection, extract)
    good = run(migrated_connection)

    (tmp_path / "10_bad.sql").write_text("SELECT no_such_function();", encoding="utf-8")
    with pytest.raises(Exception):  # noqa: B017 - psycopg raises a driver-specific error
        run(migrated_connection, tmp_path)

    assert latest_run_id(migrated_connection) == good.run_id


def test_scorecard_renders(scored):
    scorecard, _ = scored
    rendered = format_scorecard(scorecard)
    assert "HR_OVERLAPPING_SPAN" in rendered
    assert "rules implemented: 19/19" in rendered
    assert "not implemented" not in rendered


def test_scorecard_renders_an_unimplemented_rule():
    """format_scorecard's "not implemented" branch, exercised directly.

    Nothing in the current, fully-covered warehouse takes this path — see
    test_every_defect_type_is_covered — but the rendering code still has to
    handle it correctly for whenever the next defect type is added and is
    briefly uncovered, so it is tested against a scorecard built by hand
    rather than left to accidentally bit-rot.
    """
    from intus_warehouse.dq import RuleScore, Scorecard

    scorecard = Scorecard(
        run_id=1,
        rules=(
            RuleScore(
                rule_code="FUTURE_DEFECT",
                dataset="some_dataset",
                seeded=2,
                detected=0,
                missed=0,
                false_positives=0,
                implemented=False,
            ),
        ),
    )
    rendered = format_scorecard(scorecard)
    assert "FUTURE_DEFECT" in rendered
    assert "not implemented" in rendered
    assert "rules implemented: 0/1" in rendered


# --------------------------------------------------------------------------
# Reference data duplicated into SQL on purpose — kept honest against the
# generator, the same pattern as staging DDL vs. the Dataset registry (D-010).
# --------------------------------------------------------------------------


def test_ai_pricing_matches_the_generator():
    """The rates embedded in 120_fact_ai_usage.sql must match intus_gen exactly.

    AI_COST_MISMATCH recomputes cost from a hard-coded copy of
    intus_gen.domains.ai_usage.MODELS rather than detecting the defect
    statistically (token counts vary too widely for a statistical threshold
    to separate a correct high-token request from a corrupted one — an
    earlier version of this rule tried that and it did not work). Duplicating
    the rate card is deliberate; this test is what keeps the copy from
    drifting silently the day a rate changes upstream.
    """
    import re

    from intus_gen.domains.ai_usage import MODELS

    sql = (TRANSFORM_DIR / "120_fact_ai_usage.sql").read_text(encoding="utf-8")
    # Parsed and compared as numbers, not as matched substrings: Python's
    # float repr (0.003) and the SQL literal (0.0030) are the same value
    # written differently, and a string-containment check would fail on that
    # formatting difference alone rather than on an actual rate mismatch.
    found = {
        name: (float(input_rate), float(output_rate))
        for name, input_rate, output_rate in re.findall(
            r"\('([\w-]+)',\s*([\d.]+),\s*([\d.]+)\)", sql
        )
    }
    for model in MODELS:
        assert model.name in found, f"{model.name} is missing from the SQL rate table"
        assert found[model.name] == (model.input_usd_per_1k, model.output_usd_per_1k), (
            f"{model.name} rate in the SQL does not match intus_gen"
        )


def test_region_lookup_matches_the_generator():
    """SEC_IMPOSSIBLE_TRAVEL's country-to-region table, checked the same way.

    "Different country" was too loose a signal on its own — source_country is
    drawn per event from a several-country pool *within* a region, so two
    ordinary events for the same person can legitimately land on different
    countries within their own region. Only a cross-*region* jump is
    implausible, hence this lookup, and hence keeping it honest against
    intus_gen.domains.access._COUNTRY_BY_REGION.
    """
    from intus_gen.domains.access import _COUNTRY_BY_REGION

    sql = (TRANSFORM_DIR / "130_fact_access_event.sql").read_text(encoding="utf-8")
    for region, countries in _COUNTRY_BY_REGION.items():
        for country in countries:
            assert f"('{country}', '{region}')" in sql, (
                f"({country}, {region}) from intus_gen.world is missing from the SQL lookup"
            )
