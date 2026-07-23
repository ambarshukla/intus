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
from intus_warehouse.transform import run

#: Rules the warehouse implements so far. The remaining defects land on facts,
#: which this phase has not built — listing them explicitly means adding a rule
#: without adding it here is a test failure rather than a silent omission.
IMPLEMENTED_RULES = {
    "HR_OVERLAPPING_SPAN",
    "HR_ORPHAN_MANAGER",
    "HR_MISSING_TERMINATION_REASON",
    "CRM_DUPLICATE_ACCOUNT",
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


def test_unimplemented_rules_are_reported_as_such_not_as_failures(scored):
    """A partially built warehouse should report coverage honestly."""
    scorecard, _ = scored
    unimplemented = [rule for rule in scorecard.rules if not rule.implemented]
    assert unimplemented, "the fixture should still contain unhandled defect types"
    for rule in unimplemented:
        assert rule.recall is None
        assert rule.seeded > 0


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
    assert "not implemented" in rendered
    assert "rules implemented:" in rendered
