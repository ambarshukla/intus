"""Product telemetry: daily usage per customer and product.

The highest-volume domain by far, and the one that makes the warehouse phase
worth doing — aggregate-heavy reporting over a fact table with tens of
millions of cells is where star-schema design and indexing stop being
theoretical.

Usage is generated with weekday seasonality and a slow per-account trend.
Both matter: flat random noise would make every window function, moving
average, and cohort-retention query in the reporting layer return something
indistinguishable from a constant, which is a poor way to demonstrate that the
query works.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from random import Random

from intus_gen.defects import DefectSpec, Injection, replace_at, sample_indices
from intus_gen.emit import Table
from intus_gen.sensitivity import Column, Dataset, Tier
from intus_gen.world import HalcyonWorld

_SATURDAY = 5


@dataclass(frozen=True, slots=True)
class UsageDailyRow:
    usage_date: date
    account_id: str
    product_code: str
    active_users: int
    sessions: int
    api_calls: int
    storage_gb: float
    avg_latency_ms: int
    error_count: int


USAGE_DAILY = Dataset(
    name="usage_daily",
    description="Daily product usage per account: the platform's core telemetry fact.",
    record=UsageDailyRow,
    steward="Product Analytics",
    primary_key=("usage_date", "account_id", "product_code"),
    columns=(
        Column("usage_date", Tier.PUBLIC, "Calendar date the usage was recorded for."),
        Column("account_id", Tier.INTERNAL, "Customer account."),
        Column("product_code", Tier.PUBLIC, "Product SKU."),
        Column("active_users", Tier.CONFIDENTIAL, "Distinct users active that day."),
        Column("sessions", Tier.CONFIDENTIAL, "Sessions started."),
        Column("api_calls", Tier.CONFIDENTIAL, "API requests served."),
        Column("storage_gb", Tier.CONFIDENTIAL, "Storage consumed at end of day."),
        Column("avg_latency_ms", Tier.INTERNAL, "Mean request latency."),
        Column("error_count", Tier.INTERNAL, "Requests that returned an error."),
    ),
)

DATASETS: tuple[Dataset, ...] = (USAGE_DAILY,)


def _weekday_factor(day: date) -> float:
    """Business software is used on business days."""
    weekday = day.weekday()
    if weekday >= _SATURDAY:
        return 0.22
    if weekday == 0:  # Monday catch-up
        return 1.12
    if weekday == 4:  # Friday wind-down
        return 0.88
    return 1.0


def build(world: HalcyonWorld) -> tuple[Table, ...]:
    rows: list[UsageDailyRow] = []

    for subscription in world.subscriptions:
        account = world.accounts_by_id[subscription.account_id]
        rng = world.seeds.stream("telemetry", subscription.subscription_id)

        # Adoption: not every seat is used, and the ratio differs by account.
        adoption = rng.uniform(0.35, 0.92)
        # A gentle multiplicative drift so accounts visibly grow or decline
        # over the window rather than oscillating around a fixed mean.
        drift_per_day = rng.uniform(-0.0004, 0.0011)
        base_users = max(1, int(subscription.seats * adoption))
        sessions_per_user = rng.uniform(1.4, 4.2)
        calls_per_session = rng.uniform(18, 140)
        storage_base = rng.uniform(0.4, 9.0)
        latency_base = rng.uniform(45, 240)

        first = max(subscription.start_date, world.start_date)
        last = min(subscription.end_date or world.end_date, world.end_date)

        day = first
        elapsed = 0
        while day <= last:
            factor = _weekday_factor(day) * (1 + drift_per_day * elapsed)
            # Day-to-day noise on top of the structural signal.
            factor *= rng.uniform(0.82, 1.18)
            users = max(0, int(base_users * factor))

            if users == 0:
                day += timedelta(days=1)
                elapsed += 1
                continue

            sessions = max(users, int(users * sessions_per_user * rng.uniform(0.85, 1.15)))
            api_calls = int(sessions * calls_per_session * rng.uniform(0.8, 1.2))
            latency = int(latency_base * rng.uniform(0.75, 1.45))

            rows.append(
                UsageDailyRow(
                    usage_date=day,
                    account_id=account.account_id,
                    product_code=subscription.product_code,
                    active_users=users,
                    sessions=sessions,
                    api_calls=api_calls,
                    # Storage accumulates rather than fluctuating daily.
                    storage_gb=round(storage_base + elapsed * rng.uniform(0.002, 0.03), 3),
                    avg_latency_ms=latency,
                    # Errors correlate with latency: incidents show up in both.
                    error_count=int(
                        api_calls * rng.uniform(0.0002, 0.006) * (latency / latency_base)
                    ),
                )
            )
            day += timedelta(days=1)
            elapsed += 1

    return (Table(USAGE_DAILY, rows),)


# --------------------------------------------------------------------------
# Defects
# --------------------------------------------------------------------------


def _duplicate_event(rows: list, rng: Random, world: HalcyonWorld) -> list[Injection]:
    """Re-deliver a day's usage row — the classic at-least-once pipeline bug."""
    injections: list[Injection] = []
    for index in sample_indices(rng, len(rows), 6):
        duplicate = rows[index]
        rows.append(duplicate)
        injections.append(
            Injection(
                defect="USAGE_DUPLICATE_EVENT",
                dataset=USAGE_DAILY.name,
                target_key=f"{duplicate.usage_date}|{duplicate.account_id}|{duplicate.product_code}",
                detail="row duplicated, double-counting the day's usage",
            )
        )
    return injections


def _negative_sessions(rows: list, rng: Random, world: HalcyonWorld) -> list[Injection]:
    """A counter that went backwards, as counters reset by a bad deploy do."""
    injections: list[Injection] = []
    for index in sample_indices(rng, len(rows), 4):
        flipped = -abs(rows[index].sessions)
        original = replace_at(rows, index, sessions=flipped)
        injections.append(
            Injection(
                defect="USAGE_NEGATIVE_SESSIONS",
                dataset=USAGE_DAILY.name,
                target_key=f"{original.usage_date}|{original.account_id}|{original.product_code}",
                detail=f"sessions {original.sessions} -> {flipped}",
            )
        )
    return injections


def _unknown_account(rows: list, rng: Random, world: HalcyonWorld) -> list[Injection]:
    """Telemetry for an account the CRM has never heard of."""
    injections: list[Injection] = []
    for index in sample_indices(rng, len(rows), 4):
        ghost = f"A9{rng.randrange(1000, 9999)}"
        original = replace_at(rows, index, account_id=ghost)
        injections.append(
            Injection(
                defect="USAGE_UNKNOWN_ACCOUNT",
                dataset=USAGE_DAILY.name,
                target_key=f"{original.usage_date}|{ghost}|{original.product_code}",
                detail=f"account_id {original.account_id} -> {ghost} (not in crm_account)",
            )
        )
    return injections


DEFECTS: tuple[DefectSpec, ...] = (
    DefectSpec(
        name="USAGE_DUPLICATE_EVENT",
        dataset=USAGE_DAILY.name,
        description="Duplicated daily usage row, inflating totals.",
        apply=_duplicate_event,
    ),
    DefectSpec(
        name="USAGE_NEGATIVE_SESSIONS",
        dataset=USAGE_DAILY.name,
        description="Negative session count from a counter reset.",
        apply=_negative_sessions,
    ),
    DefectSpec(
        name="USAGE_UNKNOWN_ACCOUNT",
        dataset=USAGE_DAILY.name,
        description="Usage recorded against an account absent from the CRM.",
        apply=_unknown_account,
    ),
)
