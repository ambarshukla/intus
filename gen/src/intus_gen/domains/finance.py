"""Finance: budgets and actuals by fiscal period, cost centre, and GL account.

One deliberate cross-domain tie: **salary actuals are derived from the HR
population**, not invented. The monthly posting to GL 6000 for a cost centre
is the sum of the monthly salary of everyone in that department that month.

That costs a little generator complexity and buys two things. It gives the
warehouse phase a genuine reconciliation to build — payroll expense in Finance
should tie to headcount times compensation in HR — and it gives the governance
phase a concrete dilemma, because that reconciliation crosses a classification
boundary: the inputs are ``RESTRICTED`` and the output is ordinary management
reporting. Aggregating across a masking boundary is the interesting case, and
inventing salary expense independently would have designed it away.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from random import Random

from intus_gen.defects import DefectSpec, Injection, replace_at, sample_indices
from intus_gen.emit import Table, money
from intus_gen.fiscal import periods_between
from intus_gen.sensitivity import Column, Dataset, Tier
from intus_gen.world import DEPARTMENTS, HalcyonWorld

_MONTHS_PER_YEAR = 12
_SALARY_GL = "6000"


@dataclass(frozen=True, slots=True)
class GLAccount:
    code: str
    name: str
    #: Monthly budget per head, in USD, for departments that spend on this
    #: line. Salaries are excluded — they come from the HR population.
    per_head_monthly: float


GL_ACCOUNTS: tuple[GLAccount, ...] = (
    GLAccount(_SALARY_GL, "Salaries and Wages", 0.0),
    GLAccount("6100", "Employee Benefits", 780.0),
    GLAccount("6200", "Travel and Entertainment", 310.0),
    GLAccount("6300", "Software Subscriptions", 240.0),
    GLAccount("6400", "Marketing Programs", 0.0),
    GLAccount("6500", "Professional Fees", 190.0),
    GLAccount("6600", "Facilities and Rent", 640.0),
    GLAccount("6700", "Cloud Infrastructure", 0.0),
    GLAccount("6800", "Training and Development", 95.0),
    GLAccount("6900", "Talent Acquisition", 130.0),
)

GL_BY_CODE: dict[str, GLAccount] = {account.code: account for account in GL_ACCOUNTS}

# Lines that belong to particular functions rather than to every department.
_MARKETING_MONTHLY = 420_000.0
_CLOUD_MONTHLY = 610_000.0


@dataclass(frozen=True, slots=True)
class BudgetRow:
    budget_id: str
    fiscal_period: str
    fiscal_year: int
    fiscal_quarter: int
    cost_center: str
    department_code: str
    gl_account: str
    gl_account_name: str
    budget_usd: int
    approved_by: str
    approved_date: date


BUDGET = Dataset(
    name="fin_budget",
    description="Approved operating budget by period, cost centre, and GL account.",
    record=BudgetRow,
    steward="Financial Planning & Analysis",
    primary_key=("budget_id",),
    columns=(
        Column("budget_id", Tier.INTERNAL, "Surrogate key."),
        Column("fiscal_period", Tier.PUBLIC, "Fiscal month key, e.g. FY2026-M03."),
        Column("fiscal_year", Tier.PUBLIC, "Fiscal year."),
        Column("fiscal_quarter", Tier.PUBLIC, "Fiscal quarter, 1-4."),
        Column("cost_center", Tier.INTERNAL, "Cost centre the budget belongs to."),
        Column("department_code", Tier.INTERNAL, "Owning department."),
        Column("gl_account", Tier.INTERNAL, "General ledger account code."),
        Column("gl_account_name", Tier.PUBLIC, "General ledger account name."),
        Column("budget_usd", Tier.CONFIDENTIAL, "Approved amount for the period."),
        Column("approved_by", Tier.CONFIDENTIAL, "Employee who approved the budget line."),
        Column("approved_date", Tier.CONFIDENTIAL, "Date of approval — change-control evidence."),
    ),
)


@dataclass(frozen=True, slots=True)
class ActualRow:
    actual_id: str
    fiscal_period: str
    posting_date: date
    cost_center: str
    department_code: str
    gl_account: str
    gl_account_name: str
    amount_usd: Decimal
    vendor: str
    description: str
    posted_by: str


ACTUAL = Dataset(
    name="fin_actual",
    description="Posted general-ledger transactions, the actuals side of budget variance.",
    record=ActualRow,
    steward="Financial Planning & Analysis",
    primary_key=("actual_id",),
    columns=(
        Column("actual_id", Tier.INTERNAL, "Surrogate key."),
        Column("fiscal_period", Tier.PUBLIC, "Fiscal month the posting lands in."),
        Column("posting_date", Tier.INTERNAL, "Date the entry was posted."),
        Column("cost_center", Tier.INTERNAL, "Cost centre charged."),
        Column("department_code", Tier.INTERNAL, "Owning department."),
        Column("gl_account", Tier.INTERNAL, "General ledger account code."),
        Column("gl_account_name", Tier.PUBLIC, "General ledger account name."),
        Column("amount_usd", Tier.CONFIDENTIAL, "Posted amount."),
        Column("vendor", Tier.CONFIDENTIAL, "Counterparty, where applicable."),
        Column("description", Tier.CONFIDENTIAL, "Free-text posting narrative."),
        Column(
            "posted_by", Tier.CONFIDENTIAL, "Employee who posted the entry — segregation of duties."
        ),
    ),
)


DATASETS: tuple[Dataset, ...] = (BUDGET, ACTUAL)

_VENDORS: tuple[str, ...] = (
    "Brightline Cloud",
    "Fenwick Travel",
    "Cedar Systems",
    "Northgate Consulting",
    "Vellum Software",
    "Harbourpoint Facilities",
    "Lumen Training",
    "Cassia Talent Partners",
    "Ridgeway Media",
    "Ashgrove Legal",
)


def _salary_expense(world: HalcyonWorld, department: str, period_end: date) -> float:
    """Monthly payroll for a department, from the HR population itself."""
    total = 0.0
    for person in world.people:
        if not person.employed_on(period_end):
            continue
        span = person.span_on(period_end)
        if span is None or span.department != department:
            continue
        total += span.annual_salary_usd / _MONTHS_PER_YEAR
    return total


def build(world: HalcyonWorld) -> tuple[Table, ...]:
    budgets: list[BudgetRow] = []
    actuals: list[ActualRow] = []
    periods = periods_between(world.start_date, world.end_date)

    # Budget approvers are the finance leadership; posting is done by finance
    # staff. Keeping the two populations distinct is what lets a later phase
    # test segregation of duties rather than assert it.
    finance_people = sorted(
        person.employee_id
        for person in world.people
        if person.current.department == "FIN" and person.termination_date is None
    )
    approvers = finance_people[:3] or [world.people[0].employee_id]
    posters = finance_people[3:] or approvers

    for period in periods:
        headcount_by_department: dict[str, int] = {}
        for person in world.people:
            if not person.employed_on(period.end_date):
                continue
            span = person.span_on(period.end_date)
            if span is not None:
                headcount_by_department[span.department] = (
                    headcount_by_department.get(span.department, 0) + 1
                )

        for department in DEPARTMENTS:
            heads = headcount_by_department.get(department.code, 0)
            if heads == 0:
                continue

            for gl in GL_ACCOUNTS:
                rng = world.seeds.stream("finance", "budget", period.key, department.code, gl.code)

                if gl.code == _SALARY_GL:
                    planned = _salary_expense(world, department.code, period.end_date)
                    # Budgets are set before the year starts, so they never
                    # match actual payroll exactly.
                    planned *= rng.uniform(0.96, 1.07)
                elif gl.code == "6400":
                    if department.code != "MKT":
                        continue
                    planned = _MARKETING_MONTHLY * rng.uniform(0.85, 1.15)
                elif gl.code == "6700":
                    if department.code != "ENG":
                        continue
                    planned = _CLOUD_MONTHLY * rng.uniform(0.9, 1.12)
                else:
                    planned = heads * gl.per_head_monthly * rng.uniform(0.85, 1.15)

                if planned <= 0:
                    continue

                budgets.append(
                    BudgetRow(
                        budget_id=f"B{len(budgets) + 1:07d}",
                        fiscal_period=period.key,
                        fiscal_year=period.fiscal_year,
                        fiscal_quarter=period.fiscal_quarter,
                        cost_center=department.cost_center,
                        department_code=department.code,
                        gl_account=gl.code,
                        gl_account_name=gl.name,
                        budget_usd=int(round(planned / 100) * 100),
                        approved_by=rng.choice(approvers),
                        approved_date=date(period.fiscal_year - 1, 11, 15),
                    )
                )

                # Actuals arrive as several postings across the month, not one
                # lump — which is what makes period-close and cut-off testing
                # meaningful later.
                if gl.code == _SALARY_GL:
                    postings = 1
                    total = _salary_expense(world, department.code, period.end_date)
                else:
                    postings = rng.randrange(2, 7)
                    total = planned * rng.uniform(0.78, 1.22)

                for index in range(postings):
                    share = total / postings * rng.uniform(0.7, 1.3) if postings > 1 else total
                    posting_day = min(
                        period.start_date.replace(day=min(rng.randrange(1, 29), 28)),
                        period.end_date,
                    )
                    actuals.append(
                        ActualRow(
                            actual_id=f"GL{len(actuals) + 1:08d}",
                            fiscal_period=period.key,
                            posting_date=posting_day,
                            cost_center=department.cost_center,
                            department_code=department.code,
                            gl_account=gl.code,
                            gl_account_name=gl.name,
                            amount_usd=money(share),
                            vendor="Payroll" if gl.code == _SALARY_GL else rng.choice(_VENDORS),
                            description=f"{gl.name} - {period.key} posting {index + 1}",
                            posted_by=rng.choice(posters),
                        )
                    )

    return (Table(BUDGET, budgets), Table(ACTUAL, actuals))


# --------------------------------------------------------------------------
# Defects
# --------------------------------------------------------------------------


def _orphan_cost_center(rows: list, rng: Random, world: HalcyonWorld) -> list[Injection]:
    """Post to a cost centre that does not exist in the org."""
    injections: list[Injection] = []
    for index in sample_indices(rng, len(rows), 3):
        ghost = f"CC-9{rng.randrange(100, 999)}"
        original = replace_at(rows, index, cost_center=ghost)
        injections.append(
            Injection(
                defect="FIN_ORPHAN_COST_CENTER",
                dataset=ACTUAL.name,
                target_key=original.actual_id,
                detail=f"cost_center {original.cost_center} -> {ghost} (not in the org structure)",
            )
        )
    return injections


def _closed_period_posting(rows: list, rng: Random, world: HalcyonWorld) -> list[Injection]:
    """Post an entry into a period the ledger has already closed.

    A SOX-relevant defect rather than merely a dirty one: it changes a number
    that has already been reported, which is precisely what period-close
    controls exist to prevent.
    """
    injections: list[Injection] = []
    for index in sample_indices(rng, len(rows), 3):
        original = rows[index]
        # The posting date stays where it is; the period label moves back.
        year, month = int(original.fiscal_period[2:6]), int(original.fiscal_period[-2:])
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
        stale = f"FY{year}-M{month:02d}"
        replace_at(rows, index, fiscal_period=stale)
        injections.append(
            Injection(
                defect="FIN_CLOSED_PERIOD_POSTING",
                dataset=ACTUAL.name,
                target_key=original.actual_id,
                detail=(
                    f"fiscal_period {original.fiscal_period} -> {stale} "
                    f"while posting_date stays {original.posting_date}"
                ),
            )
        )
    return injections


def _self_approved_budget(rows: list, rng: Random, world: HalcyonWorld) -> list[Injection]:
    """Approve a budget line with an employee who is not an approver.

    Segregation of duties, broken: the evidence a control operated is only
    worth anything if the approver was entitled to approve.
    """
    injections: list[Injection] = []
    for index in sample_indices(rng, len(rows), 2):
        ghost = f"E9{rng.randrange(1000, 9999)}"
        original = replace_at(rows, index, approved_by=ghost)
        injections.append(
            Injection(
                defect="FIN_UNAUTHORISED_APPROVER",
                dataset=BUDGET.name,
                target_key=original.budget_id,
                detail=f"approved_by {original.approved_by} -> {ghost} (not an employee)",
            )
        )
    return injections


DEFECTS: tuple[DefectSpec, ...] = (
    DefectSpec(
        name="FIN_ORPHAN_COST_CENTER",
        dataset=ACTUAL.name,
        description="Posting charged to an unknown cost centre.",
        apply=_orphan_cost_center,
    ),
    DefectSpec(
        name="FIN_CLOSED_PERIOD_POSTING",
        dataset=ACTUAL.name,
        description="Entry booked into an already-closed fiscal period.",
        apply=_closed_period_posting,
    ),
    DefectSpec(
        name="FIN_UNAUTHORISED_APPROVER",
        dataset=BUDGET.name,
        description="Budget approved by someone outside the approver population.",
        apply=_self_approved_budget,
    ),
)
