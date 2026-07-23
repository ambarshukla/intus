"""Internal AI usage: which employees use which models, and what it costs.

A genuinely modern internal-data domain, and an awkward one to govern: the
rows are per-employee activity, so aggregate cost reporting is ordinary
management information while the individual rows are close to surveillance.
That tension is modelled explicitly — ``employee_id`` and ``prompt_preview``
are ``RESTRICTED``, everything needed for departmental cost allocation is not.
A later phase can therefore serve FP&A a full-fidelity cost report from a
table no one is allowed to browse row by row.

The model names and prices here are invented. Quoting a real vendor's price
list would date immediately and would make the dataset a claim about a real
product rather than a self-contained example.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from random import Random

from intus_gen.defects import DefectSpec, Injection, replace_at, sample_indices
from intus_gen.emit import Table, money
from intus_gen.sensitivity import Column, Dataset, Tier
from intus_gen.world import DEPARTMENTS_BY_CODE, HalcyonWorld

_SATURDAY = 5


@dataclass(frozen=True, slots=True)
class ModelPricing:
    name: str
    input_usd_per_1k: float
    output_usd_per_1k: float
    #: Typical completion length, in tokens — larger models are used for
    #: longer-form work, which is what makes cost mix rather than volume.
    output_scale: int


MODELS: tuple[ModelPricing, ...] = (
    ModelPricing("atlas-large", 0.0030, 0.0150, 900),
    ModelPricing("atlas-mini", 0.0008, 0.0040, 400),
    ModelPricing("orion-pro", 0.0050, 0.0200, 1_100),
    ModelPricing("orion-lite", 0.0004, 0.0016, 300),
)

MODELS_BY_NAME: dict[str, ModelPricing] = {model.name: model for model in MODELS}

_MODEL_WEIGHTS: tuple[float, ...] = (0.38, 0.27, 0.17, 0.18)

FEATURES: tuple[str, ...] = (
    "Code Assistant",
    "Document Summarisation",
    "Support Draft",
    "Data Query",
    "Meeting Notes",
    "Contract Review",
)

# Departments differ enormously in AI adoption; a flat rate across the company
# would make the cost-allocation reporting uninteresting.
_ADOPTION_BY_DEPARTMENT: dict[str, float] = {
    "ENG": 0.78,
    "PRD": 0.62,
    "SLS": 0.41,
    "MKT": 0.55,
    "CSM": 0.48,
    "FIN": 0.26,
    "HRS": 0.24,
    "ITS": 0.52,
    "LGL": 0.33,
}

_FEATURE_BY_FUNCTION: dict[str, tuple[str, ...]] = {
    "R&D": ("Code Assistant", "Document Summarisation", "Data Query"),
    "S&M": ("Support Draft", "Document Summarisation", "Meeting Notes"),
    "G&A": ("Document Summarisation", "Contract Review", "Meeting Notes", "Data Query"),
}


@dataclass(frozen=True, slots=True)
class AiUsageRow:
    event_id: str
    event_ts: datetime
    employee_id: str
    department_code: str
    region: str
    model: str
    feature: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    latency_ms: int
    flagged_by_policy: bool


AI_USAGE_EVENT = Dataset(
    name="ai_usage_event",
    description="Per-request LLM usage and cost by employee, model, and feature.",
    record=AiUsageRow,
    steward="Information Technology",
    primary_key=("event_id",),
    columns=(
        Column("event_id", Tier.INTERNAL, "Surrogate key for the request."),
        Column("event_ts", Tier.INTERNAL, "When the request was made."),
        Column("employee_id", Tier.RESTRICTED, "Requesting employee — individual activity."),
        Column("department_code", Tier.INTERNAL, "Department the cost allocates to."),
        Column("region", Tier.INTERNAL, "Employee's region, for regional cost reporting."),
        Column("model", Tier.INTERNAL, "Model invoked."),
        Column("feature", Tier.INTERNAL, "Internal product surface the request came from."),
        Column("prompt_tokens", Tier.CONFIDENTIAL, "Input tokens billed."),
        Column("completion_tokens", Tier.CONFIDENTIAL, "Output tokens billed."),
        Column("cost_usd", Tier.CONFIDENTIAL, "Computed cost of the request."),
        Column("latency_ms", Tier.INTERNAL, "End-to-end request latency."),
        Column("flagged_by_policy", Tier.RESTRICTED, "Whether a usage policy rule fired."),
    ),
)

DATASETS: tuple[Dataset, ...] = (AI_USAGE_EVENT,)


def cost_for(model: ModelPricing, prompt_tokens: int, completion_tokens: int) -> Decimal:
    """Cost of one request, to the cent-fraction the billing system would use."""
    cost = (
        prompt_tokens / 1_000 * model.input_usd_per_1k
        + completion_tokens / 1_000 * model.output_usd_per_1k
    )
    # Six places: token billing genuinely runs to fractions of a cent, and
    # rounding to the cent here would make a monthly cost report that
    # aggregates millions of requests visibly wrong.
    return money(cost, places=6)


def build(world: HalcyonWorld) -> tuple[Table, ...]:
    rows: list[AiUsageRow] = []
    # AI tooling was rolled out partway through the window, so the domain has
    # an adoption ramp rather than existing uniformly from day one.
    rollout = world.start_date + timedelta(
        days=int((world.end_date - world.start_date).days * 0.25)
    )

    for person in world.people:
        rng = world.seeds.stream("ai_usage", person.employee_id)
        # Individual enthusiasm varies within a department.
        keenness = rng.uniform(0.3, 1.7)

        day = max(rollout, person.hire_date)
        while day <= world.end_date:
            if not person.employed_on(day) or day.weekday() >= _SATURDAY:
                day += timedelta(days=1)
                continue

            span = person.span_on(day)
            if span is None:
                day += timedelta(days=1)
                continue

            department = DEPARTMENTS_BY_CODE[span.department]
            adoption = _ADOPTION_BY_DEPARTMENT[span.department] * keenness
            # Adoption climbs over the first year after rollout.
            ramp = min(1.0, 0.25 + (day - rollout).days / 365)

            if rng.random() > adoption * ramp:
                day += timedelta(days=1)
                continue

            for _ in range(rng.randrange(1, 9)):
                model = rng.choices(MODELS, weights=_MODEL_WEIGHTS, k=1)[0]
                prompt_tokens = int(rng.gauss(1_400, 700))
                prompt_tokens = max(60, prompt_tokens)
                completion_tokens = max(
                    20, int(rng.gauss(model.output_scale, model.output_scale * 0.4))
                )

                moment = datetime.combine(
                    day,
                    time(
                        hour=rng.randrange(8, 19),
                        minute=rng.randrange(0, 60),
                        second=rng.randrange(0, 60),
                    ),
                )
                rows.append(
                    AiUsageRow(
                        event_id=f"AI{len(rows) + 1:08d}",
                        event_ts=moment,
                        employee_id=person.employee_id,
                        department_code=span.department,
                        region=person.region,
                        model=model.name,
                        feature=rng.choice(_FEATURE_BY_FUNCTION[department.function]),
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        cost_usd=cost_for(model, prompt_tokens, completion_tokens),
                        latency_ms=int(rng.gauss(2_200, 900)) + completion_tokens // 2,
                        # Rare, and deliberately so: a policy that fires often
                        # is one nobody reads the alerts from.
                        flagged_by_policy=rng.random() < 0.004,
                    )
                )
            day += timedelta(days=1)

    return (Table(AI_USAGE_EVENT, rows),)


# --------------------------------------------------------------------------
# Defects
# --------------------------------------------------------------------------


def _cost_mismatch(rows: list, rng: Random, world: HalcyonWorld) -> list[Injection]:
    """Break the arithmetic tying cost to tokens.

    The only defect here that a schema check cannot find: every column is
    individually valid and only the relationship between them is wrong. It is
    exactly the class of error a reconciliation rule exists for.
    """
    injections: list[Injection] = []
    for index in sample_indices(rng, len(rows), 5):
        wrong = money(float(rows[index].cost_usd) * rng.uniform(3.0, 12.0), places=6)
        original = replace_at(rows, index, cost_usd=wrong)
        injections.append(
            Injection(
                defect="AI_COST_MISMATCH",
                dataset=AI_USAGE_EVENT.name,
                target_key=original.event_id,
                detail=f"cost_usd {original.cost_usd} -> {wrong}, inconsistent with token counts",
            )
        )
    return injections


def _unknown_model(rows: list, rng: Random, world: HalcyonWorld) -> list[Injection]:
    """Log a model name that is not in the approved catalog — shadow AI usage."""
    injections: list[Injection] = []
    for index in sample_indices(rng, len(rows), 4):
        unknown = rng.choice(("atlas-large-preview", "orion-experimental", "unknown"))
        original = replace_at(rows, index, model=unknown)
        injections.append(
            Injection(
                defect="AI_UNKNOWN_MODEL",
                dataset=AI_USAGE_EVENT.name,
                target_key=original.event_id,
                detail=f"model {original.model!r} -> {unknown!r} (not in the approved catalog)",
            )
        )
    return injections


DEFECTS: tuple[DefectSpec, ...] = (
    DefectSpec(
        name="AI_COST_MISMATCH",
        dataset=AI_USAGE_EVENT.name,
        description="cost_usd does not reconcile to tokens times the model's rate.",
        apply=_cost_mismatch,
    ),
    DefectSpec(
        name="AI_UNKNOWN_MODEL",
        dataset=AI_USAGE_EVENT.name,
        description="Usage logged against a model outside the approved catalog.",
        apply=_unknown_model,
    ),
)
