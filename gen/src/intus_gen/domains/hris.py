"""HR: employee history, compensation, and performance reviews.

Split across three tables rather than one wide employee record, because that
is both how HR systems are actually built and what makes the governance phase
meaningful. Employee history is ``INTERNAL`` — any employee may look up who
works where. Compensation and performance ratings are ``RESTRICTED``, live in
their own tables, and are the primary targets for column masking and
row-level security later.

Employee history is emitted in **SCD2 shape**: one row per attribute-validity
span, with ``valid_from`` inclusive and ``valid_to`` exclusive, ``NULL`` for
the current row. The warehouse phase builds a type-2 dimension from this, and
feeding that merge honestly-effective-dated source data — rather than a
snapshot it has to infer history from — is what makes the exercise real.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from random import Random

from intus_gen.defects import DefectSpec, Injection, replace_at, sample_indices
from intus_gen.emit import Table
from intus_gen.sensitivity import Column, Dataset, Tier
from intus_gen.world import DEPARTMENTS_BY_CODE, HalcyonWorld

# --------------------------------------------------------------------------
# hr_employee_history
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EmployeeHistoryRow:
    employee_id: str
    valid_from: date
    valid_to: date | None
    first_name: str
    last_name: str
    work_email: str
    region: str
    location: str
    department_code: str
    department_name: str
    job_level: int
    job_title: str
    manager_id: str | None
    employment_type: str
    change_reason: str
    hire_date: date
    termination_date: date | None
    termination_reason: str | None


EMPLOYEE_HISTORY = Dataset(
    name="hr_employee_history",
    description="Effective-dated employee attributes, one row per validity span (SCD2 source).",
    record=EmployeeHistoryRow,
    steward="People Operations",
    primary_key=("employee_id", "valid_from"),
    columns=(
        Column("employee_id", Tier.INTERNAL, "Stable employee identifier."),
        Column("valid_from", Tier.INTERNAL, "Span start, inclusive."),
        Column("valid_to", Tier.INTERNAL, "Span end, exclusive; NULL for the current row."),
        Column("first_name", Tier.INTERNAL, "Given name as held in the HR system."),
        Column("last_name", Tier.INTERNAL, "Family name as held in the HR system."),
        Column(
            "work_email", Tier.INTERNAL, "Corporate address; the join key most other systems use."
        ),
        Column(
            "region", Tier.INTERNAL, "Employing region — the row-level-security predicate for HR."
        ),
        Column("location", Tier.INTERNAL, "Primary office."),
        Column("department_code", Tier.INTERNAL, "Owning department."),
        Column("department_name", Tier.INTERNAL, "Department display name."),
        Column(
            "job_level", Tier.CONFIDENTIAL, "Internal levelling; visible to HR and managers only."
        ),
        Column("job_title", Tier.INTERNAL, "Public-facing job title."),
        Column("manager_id", Tier.INTERNAL, "Employee ID of the line manager."),
        Column("employment_type", Tier.INTERNAL, "Full-Time or Contractor."),
        Column(
            "change_reason", Tier.CONFIDENTIAL, "Why this span opened (hire, promotion, transfer)."
        ),
        Column("hire_date", Tier.INTERNAL, "Original hire date, repeated on every span."),
        Column("termination_date", Tier.CONFIDENTIAL, "Leaving date; NULL while employed."),
        Column(
            "termination_reason", Tier.RESTRICTED, "Voluntary/involuntary — HR only, never in BI."
        ),
    ),
)


# --------------------------------------------------------------------------
# hr_compensation
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompensationRow:
    compensation_id: str
    employee_id: str
    effective_from: date
    effective_to: date | None
    pay_grade: str
    annual_salary_usd: int
    bonus_target_pct: float
    equity_units: int
    currency: str
    change_reason: str


COMPENSATION = Dataset(
    name="hr_compensation",
    description="Effective-dated compensation. Restricted: the canonical column-masking target.",
    record=CompensationRow,
    steward="Total Rewards",
    primary_key=("compensation_id",),
    columns=(
        Column("compensation_id", Tier.INTERNAL, "Surrogate key for the compensation record."),
        Column("employee_id", Tier.INTERNAL, "Employee the record belongs to."),
        Column("effective_from", Tier.CONFIDENTIAL, "Span start, inclusive."),
        Column("effective_to", Tier.CONFIDENTIAL, "Span end, exclusive; NULL if current."),
        Column("pay_grade", Tier.CONFIDENTIAL, "Banding used for pay-equity analysis."),
        Column("annual_salary_usd", Tier.RESTRICTED, "Base salary — masked outside Total Rewards."),
        Column("bonus_target_pct", Tier.RESTRICTED, "Target bonus as a percentage of base."),
        Column("equity_units", Tier.RESTRICTED, "Outstanding equity grant in units."),
        Column("currency", Tier.INTERNAL, "Currency of record; USD throughout this dataset."),
        Column("change_reason", Tier.CONFIDENTIAL, "Why compensation changed."),
    ),
)


# --------------------------------------------------------------------------
# hr_performance_review
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PerformanceReviewRow:
    review_id: str
    employee_id: str
    review_period: str
    reviewer_id: str | None
    rating: int
    rating_label: str
    promotion_recommended: bool
    submitted_date: date


PERFORMANCE_REVIEW = Dataset(
    name="hr_performance_review",
    description="Annual performance ratings. Restricted; visible to HR and the reviewing manager.",
    record=PerformanceReviewRow,
    steward="People Operations",
    primary_key=("review_id",),
    columns=(
        Column("review_id", Tier.INTERNAL, "Surrogate key for the review."),
        Column("employee_id", Tier.INTERNAL, "Employee under review."),
        Column("review_period", Tier.INTERNAL, "Fiscal year the review covers."),
        Column("reviewer_id", Tier.CONFIDENTIAL, "Manager who submitted the review."),
        Column("rating", Tier.RESTRICTED, "1-5 performance rating."),
        Column("rating_label", Tier.RESTRICTED, "Human-readable form of the rating."),
        Column("promotion_recommended", Tier.RESTRICTED, "Whether promotion was recommended."),
        Column("submitted_date", Tier.CONFIDENTIAL, "Date the review was submitted."),
    ),
)


DATASETS: tuple[Dataset, ...] = (EMPLOYEE_HISTORY, COMPENSATION, PERFORMANCE_REVIEW)

_PAY_GRADE_BY_LEVEL: dict[int, str] = {
    1: "G1",
    2: "G2",
    3: "G3",
    4: "G4",
    5: "M1",
    6: "M2",
    7: "E1",
    8: "E2",
}
_BONUS_TARGET_BY_LEVEL: dict[int, float] = {
    1: 0.05,
    2: 0.07,
    3: 0.10,
    4: 0.12,
    5: 0.15,
    6: 0.20,
    7: 0.30,
    8: 0.50,
}
_RATING_LABELS: dict[int, str] = {
    1: "Below Expectations",
    2: "Partially Meets",
    3: "Meets Expectations",
    4: "Exceeds Expectations",
    5: "Outstanding",
}
# Most people are rated "meets" — a distribution centred anywhere else would
# make the pay-equity and promotion analyses in later phases meaningless.
_RATING_WEIGHTS: tuple[float, ...] = (0.03, 0.10, 0.55, 0.26, 0.06)


def build(world: HalcyonWorld) -> tuple[Table, ...]:
    history: list[EmployeeHistoryRow] = []
    compensation: list[CompensationRow] = []
    reviews: list[PerformanceReviewRow] = []

    for person in world.people:
        for index, span in enumerate(person.spans):
            department = DEPARTMENTS_BY_CODE[span.department]
            history.append(
                EmployeeHistoryRow(
                    employee_id=person.employee_id,
                    valid_from=span.valid_from,
                    valid_to=span.valid_to,
                    first_name=person.first_name,
                    last_name=person.last_name,
                    work_email=person.work_email,
                    region=person.region,
                    location=span.location,
                    department_code=span.department,
                    department_name=department.name,
                    job_level=span.job_level,
                    job_title=span.title,
                    manager_id=span.manager_id,
                    employment_type=span.employment_type,
                    change_reason=span.change_reason,
                    hire_date=person.hire_date,
                    termination_date=person.termination_date,
                    termination_reason=person.termination_reason,
                )
            )

            rng = world.seeds.stream("hris", "comp", person.employee_id, index)
            compensation.append(
                CompensationRow(
                    compensation_id=f"C{len(compensation) + 1:07d}",
                    employee_id=person.employee_id,
                    effective_from=span.valid_from,
                    effective_to=span.valid_to,
                    pay_grade=_PAY_GRADE_BY_LEVEL[span.job_level],
                    annual_salary_usd=span.annual_salary_usd,
                    bonus_target_pct=_BONUS_TARGET_BY_LEVEL[span.job_level],
                    # Equity is granted at hire and on promotion, not on a
                    # merit increase — so it is a function of the reason.
                    equity_units=(
                        rng.randrange(200, 4_000)
                        if span.change_reason in {"Hire", "Promotion"}
                        else 0
                    ),
                    currency="USD",
                    change_reason=span.change_reason,
                )
            )

        # Reviews run once a fiscal year, for anyone employed at year end and
        # with at least six months' tenure — new joiners are not rated.
        for year in range(world.start_date.year, world.end_date.year + 1):
            cycle_end = date(year, 12, 31)
            if cycle_end > world.end_date:
                cycle_end = world.end_date
            if not person.employed_on(cycle_end):
                continue
            if (cycle_end - person.hire_date).days < 182:
                continue

            rng = world.seeds.stream("hris", "review", person.employee_id, year)
            rating = rng.choices((1, 2, 3, 4, 5), weights=_RATING_WEIGHTS, k=1)[0]
            span = person.span_on(cycle_end)
            reviews.append(
                PerformanceReviewRow(
                    review_id=f"R{len(reviews) + 1:07d}",
                    employee_id=person.employee_id,
                    review_period=f"FY{year}",
                    reviewer_id=span.manager_id if span else None,
                    rating=rating,
                    rating_label=_RATING_LABELS[rating],
                    promotion_recommended=rating >= 4 and rng.random() < 0.35,
                    submitted_date=cycle_end + timedelta(days=rng.randrange(10, 45)),
                )
            )

    return (
        Table(EMPLOYEE_HISTORY, history),
        Table(COMPENSATION, compensation),
        Table(PERFORMANCE_REVIEW, reviews),
    )


# --------------------------------------------------------------------------
# Defects
# --------------------------------------------------------------------------


def _orphan_manager(rows: list, rng: Random, world: HalcyonWorld) -> list[Injection]:
    """Point a manager_id at an employee who does not exist.

    The classic referential break in HR feeds: a manager leaves, their record
    is purged from the extract, and everyone reporting to them is orphaned.
    """
    injections: list[Injection] = []
    for index in sample_indices(rng, len(rows), 4):
        if rows[index].manager_id is None:
            continue
        ghost = f"E9{rng.randrange(1000, 9999)}"
        original = replace_at(rows, index, manager_id=ghost)
        injections.append(
            Injection(
                defect="HR_ORPHAN_MANAGER",
                dataset=EMPLOYEE_HISTORY.name,
                target_key=f"{original.employee_id}|{original.valid_from}",
                detail=f"manager_id {original.manager_id} -> {ghost} (not in employee population)",
            )
        )
    return injections


def _overlapping_span(rows: list, rng: Random, world: HalcyonWorld) -> list[Injection]:
    """Back-date a span so it overlaps its predecessor.

    An SCD2 merge that does not check for overlap will happily load this and
    then return two rows for a point-in-time lookup that must return one.
    """
    injections: list[Injection] = []
    candidates = [
        index
        for index in range(1, len(rows))
        if rows[index].employee_id == rows[index - 1].employee_id
        and rows[index - 1].valid_to is not None
    ]
    if not candidates:
        return injections

    for index in sorted(rng.sample(candidates, k=min(3, len(candidates)))):
        shifted = rows[index].valid_from - timedelta(days=45)
        original = replace_at(rows, index, valid_from=shifted)
        injections.append(
            Injection(
                defect="HR_OVERLAPPING_SPAN",
                dataset=EMPLOYEE_HISTORY.name,
                # The *shifted* value, because this is the only defect whose
                # corruption lands on a primary-key column. Naming the original
                # key would point at a row that no longer exists in the
                # delivered data, leaving the one defect that most needs
                # detection impossible to join back to — which is exactly what
                # happened until the warehouse tried to score against it.
                target_key=f"{original.employee_id}|{shifted}",
                detail=f"valid_from {original.valid_from} -> {shifted}, overlapping the prior span",
            )
        )
    return injections


def _missing_termination_reason(rows: list, rng: Random, world: HalcyonWorld) -> list[Injection]:
    """Terminate someone without recording why — a mandatory field left blank."""
    injections: list[Injection] = []
    candidates = [
        index
        for index in range(len(rows))
        if rows[index].termination_date is not None and rows[index].termination_reason is not None
    ]
    if not candidates:
        return injections

    for index in sorted(rng.sample(candidates, k=min(3, len(candidates)))):
        original = replace_at(rows, index, termination_reason=None)
        injections.append(
            Injection(
                defect="HR_MISSING_TERMINATION_REASON",
                dataset=EMPLOYEE_HISTORY.name,
                target_key=f"{original.employee_id}|{original.valid_from}",
                detail=f"termination_reason {original.termination_reason!r} -> NULL",
            )
        )
    return injections


def _salary_outlier(rows: list, rng: Random, world: HalcyonWorld) -> list[Injection]:
    """A fat-fingered salary: a trailing zero too many.

    Chosen over a negative salary because it is far harder to catch — it
    passes any not-null and positive-value check, and only a distribution or
    banding rule finds it.
    """
    injections: list[Injection] = []
    for index in sample_indices(rng, len(rows), 3):
        inflated = rows[index].annual_salary_usd * 10
        original = replace_at(rows, index, annual_salary_usd=inflated)
        injections.append(
            Injection(
                defect="HR_SALARY_OUTLIER",
                dataset=COMPENSATION.name,
                target_key=original.compensation_id,
                detail=f"annual_salary_usd {original.annual_salary_usd} -> {inflated}",
            )
        )
    return injections


DEFECTS: tuple[DefectSpec, ...] = (
    DefectSpec(
        name="HR_ORPHAN_MANAGER",
        dataset=EMPLOYEE_HISTORY.name,
        description="manager_id references an employee absent from the extract.",
        apply=_orphan_manager,
    ),
    DefectSpec(
        name="HR_OVERLAPPING_SPAN",
        dataset=EMPLOYEE_HISTORY.name,
        description="Two SCD2 spans for one employee cover the same date.",
        apply=_overlapping_span,
    ),
    DefectSpec(
        name="HR_MISSING_TERMINATION_REASON",
        dataset=EMPLOYEE_HISTORY.name,
        description="Employee terminated with no reason recorded.",
        apply=_missing_termination_reason,
    ),
    DefectSpec(
        name="HR_SALARY_OUTLIER",
        dataset=COMPENSATION.name,
        description="Salary inflated tenfold by a data-entry error.",
        apply=_salary_outlier,
    ),
)
