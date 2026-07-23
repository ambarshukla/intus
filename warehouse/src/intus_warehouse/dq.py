"""Scoring the warehouse's data-quality rules against known truth.

This is what the ground-truth manifest was for. The generator recorded every
defect it injected, with the primary key of the row it landed on; the transform
records every exception it detected, with the same key in the same format.
Joining the two turns "our checks ran" into a measurement:

* **recall** — of the defects actually seeded, how many did we catch?
* **false positives** — exceptions we raised that no seeded defect explains.

Recall alone is not enough, and the reason is worth stating. A rule that
rejects every row scores perfect recall. Reporting the two together is what
makes the number mean anything, and it is why the manifest records keys rather
than merely counts.

Rules that do not exist yet are reported as *not implemented* rather than as
zero recall, so a partially built warehouse reports its coverage honestly
instead of looking broken.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import psycopg

from intus_warehouse.load import MANIFEST_FILENAME


@dataclass(frozen=True, slots=True)
class RuleScore:
    rule_code: str
    dataset: str
    seeded: int
    detected: int
    missed: int
    false_positives: int
    implemented: bool

    @property
    def recall(self) -> float | None:
        if not self.implemented or self.seeded == 0:
            return None
        return self.detected / self.seeded

    @property
    def perfect(self) -> bool:
        return self.implemented and self.missed == 0 and self.false_positives == 0


@dataclass(frozen=True, slots=True)
class Scorecard:
    run_id: int
    rules: tuple[RuleScore, ...]

    @property
    def implemented(self) -> tuple[RuleScore, ...]:
        return tuple(rule for rule in self.rules if rule.implemented)

    @property
    def seeded_total(self) -> int:
        return sum(rule.seeded for rule in self.rules)

    @property
    def detected_total(self) -> int:
        return sum(rule.detected for rule in self.rules)

    @property
    def all_implemented_rules_perfect(self) -> bool:
        return all(rule.perfect for rule in self.implemented)


def _seeded(manifest_path: Path) -> dict[tuple[str, str], set[str]]:
    """(rule_code, dataset) → set of target keys, from the generator manifest."""
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    seeded: dict[tuple[str, str], set[str]] = {}
    for injection in payload["injections"]:
        key = (injection["defect"], injection["dataset"])
        seeded.setdefault(key, set()).add(injection["target_key"])
    return seeded


def _detected(connection: psycopg.Connection, run_id: int) -> dict[tuple[str, str], set[str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT rule_code, dataset, target_key
            FROM warehouse.dq_exception
            WHERE run_id = %s
            """,
            (run_id,),
        )
        rows = cursor.fetchall()

    detected: dict[tuple[str, str], set[str]] = {}
    for rule_code, dataset, target_key in rows:
        detected.setdefault((rule_code, dataset), set()).add(target_key)
    return detected


def latest_run_id(connection: psycopg.Connection) -> int | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT run_id FROM warehouse.transform_run "
            "WHERE status = 'succeeded' ORDER BY run_id DESC LIMIT 1"
        )
        row = cursor.fetchone()
    return row[0] if row else None


def score(
    connection: psycopg.Connection, extract_dir: Path, run_id: int | None = None
) -> Scorecard:
    """Compare detections from a transform run against the generator's manifest."""
    resolved = run_id if run_id is not None else latest_run_id(connection)
    if resolved is None:
        raise ValueError("no successful transform run to score; run `intus-wh build` first")

    seeded = _seeded(extract_dir / MANIFEST_FILENAME)
    detected = _detected(connection, resolved)

    scores: list[RuleScore] = []
    for key in sorted(set(seeded) | set(detected)):
        rule_code, dataset = key
        seeded_keys = seeded.get(key, set())
        detected_keys = detected.get(key, set())
        implemented = key in detected or not seeded_keys

        scores.append(
            RuleScore(
                rule_code=rule_code,
                dataset=dataset,
                seeded=len(seeded_keys),
                detected=len(seeded_keys & detected_keys),
                missed=len(seeded_keys - detected_keys),
                false_positives=len(detected_keys - seeded_keys),
                implemented=implemented,
            )
        )

    return Scorecard(run_id=resolved, rules=tuple(scores))


def format_scorecard(scorecard: Scorecard) -> str:
    lines = [
        f"transform run {scorecard.run_id}",
        "",
        f"  {'rule':<32} {'seeded':>6} {'found':>6} {'missed':>6} {'false+':>6}  recall",
    ]
    for rule in scorecard.rules:
        if not rule.implemented:
            lines.append(
                f"  {rule.rule_code:<32} {rule.seeded:>6} {'-':>6} {'-':>6} {'-':>6}"
                "  not implemented"
            )
            continue
        recall = rule.recall
        rendered = "n/a" if recall is None else f"{recall:.0%}"
        lines.append(
            f"  {rule.rule_code:<32} {rule.seeded:>6} {rule.detected:>6} "
            f"{rule.missed:>6} {rule.false_positives:>6}  {rendered}"
        )

    implemented = scorecard.implemented
    covered_seeded = sum(rule.seeded for rule in implemented)
    covered_found = sum(rule.detected for rule in implemented)
    lines += [
        "",
        f"  rules implemented: {len(implemented)}/{len(scorecard.rules)}",
        f"  defects seeded in covered rules: {covered_seeded}, detected: {covered_found}",
        f"  defects seeded overall: {scorecard.seeded_total}",
    ]
    return "\n".join(lines)
