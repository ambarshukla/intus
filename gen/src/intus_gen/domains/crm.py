"""Sales and revenue: accounts, subscriptions, pipeline, and invoices.

This is the domain where row-level security has real business meaning. A sales
rep sees their own accounts; a regional director sees their region; finance
sees everything. The predicate for all three is ``owner_employee_id`` joined
back to the employee's region — which only works because both sides come from
the same generated world.

Amounts are ``CONFIDENTIAL`` rather than ``RESTRICTED``: deal values are
legitimately visible across Sales and Finance, unlike compensation, and the
distinction is the point of having four tiers instead of a single "sensitive"
flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from random import Random

from intus_gen.defects import DefectSpec, Injection, replace_at, sample_indices
from intus_gen.emit import Table
from intus_gen.sensitivity import Column, Dataset, Tier
from intus_gen.world import PRODUCTS_BY_CODE, HalcyonWorld

# --------------------------------------------------------------------------
# crm_account
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccountRow:
    account_id: str
    account_name: str
    region: str
    segment: str
    industry: str
    created_date: date
    owner_employee_id: str
    status: str
    churn_date: date | None


ACCOUNT = Dataset(
    name="crm_account",
    description="Customer accounts and who owns them.",
    record=AccountRow,
    steward="Sales Operations",
    primary_key=("account_id",),
    columns=(
        Column("account_id", Tier.INTERNAL, "Stable account identifier."),
        Column("account_name", Tier.INTERNAL, "Customer's legal name."),
        Column("region", Tier.INTERNAL, "Sales region — the row-level-security predicate."),
        Column("segment", Tier.INTERNAL, "Enterprise / Mid-Market / SMB."),
        Column("industry", Tier.INTERNAL, "Customer's industry vertical."),
        Column("created_date", Tier.INTERNAL, "Date the account was opened."),
        Column("owner_employee_id", Tier.INTERNAL, "Sales rep who owns the relationship."),
        Column("status", Tier.INTERNAL, "Active or Churned as at the extract date."),
        Column("churn_date", Tier.CONFIDENTIAL, "Date the customer left; NULL if still active."),
    ),
)


# --------------------------------------------------------------------------
# crm_subscription
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SubscriptionRow:
    subscription_id: str
    account_id: str
    product_code: str
    product_name: str
    start_date: date
    end_date: date | None
    seats: int
    arr_usd: int
    billing_frequency: str


SUBSCRIPTION = Dataset(
    name="crm_subscription",
    description="Active and historical product subscriptions, carrying annual recurring revenue.",
    record=SubscriptionRow,
    steward="Sales Operations",
    primary_key=("subscription_id",),
    columns=(
        Column("subscription_id", Tier.INTERNAL, "Surrogate key."),
        Column("account_id", Tier.INTERNAL, "Account the subscription belongs to."),
        Column("product_code", Tier.PUBLIC, "Product SKU."),
        Column("product_name", Tier.PUBLIC, "Product display name."),
        Column("start_date", Tier.INTERNAL, "Subscription start."),
        Column("end_date", Tier.INTERNAL, "Subscription end; NULL if live."),
        Column("seats", Tier.CONFIDENTIAL, "Licensed seats."),
        Column("arr_usd", Tier.CONFIDENTIAL, "Annual recurring revenue, net of discount."),
        Column("billing_frequency", Tier.INTERNAL, "Annual or Quarterly."),
    ),
)


# --------------------------------------------------------------------------
# crm_opportunity
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpportunityRow:
    opportunity_id: str
    account_id: str
    owner_employee_id: str
    product_code: str
    opportunity_type: str
    created_date: date
    close_date: date | None
    stage: str
    amount_usd: int
    probability_pct: int
    is_won: bool | None


OPPORTUNITY = Dataset(
    name="crm_opportunity",
    description="Sales pipeline: open and closed opportunities with forecast amounts.",
    record=OpportunityRow,
    steward="Sales Operations",
    primary_key=("opportunity_id",),
    columns=(
        Column("opportunity_id", Tier.INTERNAL, "Surrogate key."),
        Column("account_id", Tier.INTERNAL, "Account the deal is against."),
        Column("owner_employee_id", Tier.INTERNAL, "Rep who owns the deal."),
        Column("product_code", Tier.PUBLIC, "Product being sold."),
        Column("opportunity_type", Tier.INTERNAL, "New Business, Expansion, or Renewal."),
        Column("created_date", Tier.INTERNAL, "Date the opportunity was raised."),
        Column("close_date", Tier.INTERNAL, "Actual or forecast close; NULL while open."),
        Column("stage", Tier.CONFIDENTIAL, "Pipeline stage."),
        Column("amount_usd", Tier.CONFIDENTIAL, "Deal value."),
        Column("probability_pct", Tier.CONFIDENTIAL, "Weighting applied in the forecast."),
        Column("is_won", Tier.CONFIDENTIAL, "Outcome; NULL while the deal is open."),
    ),
)


# --------------------------------------------------------------------------
# crm_invoice
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InvoiceRow:
    invoice_id: str
    account_id: str
    subscription_id: str
    issue_date: date
    due_date: date
    paid_date: date | None
    amount_usd: int
    currency: str
    status: str


INVOICE = Dataset(
    name="crm_invoice",
    description="Billing history per subscription, including payment status.",
    record=InvoiceRow,
    steward="Finance — Revenue",
    primary_key=("invoice_id",),
    columns=(
        Column("invoice_id", Tier.INTERNAL, "Invoice number."),
        Column("account_id", Tier.INTERNAL, "Customer billed."),
        Column("subscription_id", Tier.INTERNAL, "Subscription being billed."),
        Column("issue_date", Tier.INTERNAL, "Invoice date."),
        Column("due_date", Tier.INTERNAL, "Payment due date (net 30)."),
        Column("paid_date", Tier.CONFIDENTIAL, "Date paid; NULL if outstanding."),
        Column("amount_usd", Tier.CONFIDENTIAL, "Invoiced amount."),
        Column("currency", Tier.PUBLIC, "Billing currency."),
        Column("status", Tier.CONFIDENTIAL, "Paid, Outstanding, or Overdue at extract date."),
    ),
)


DATASETS: tuple[Dataset, ...] = (ACCOUNT, SUBSCRIPTION, OPPORTUNITY, INVOICE)

_OPEN_STAGES: tuple[str, ...] = ("Prospecting", "Qualification", "Proposal", "Negotiation")
_STAGE_PROBABILITY: dict[str, int] = {
    "Prospecting": 10,
    "Qualification": 25,
    "Proposal": 50,
    "Negotiation": 75,
}
_OPPORTUNITY_TYPES: tuple[str, ...] = ("New Business", "Expansion", "Renewal")
_NET_TERMS_DAYS = 30


def build(world: HalcyonWorld) -> tuple[Table, ...]:
    accounts: list[AccountRow] = []
    subscriptions: list[SubscriptionRow] = []
    opportunities: list[OpportunityRow] = []
    invoices: list[InvoiceRow] = []

    for account in world.accounts:
        accounts.append(
            AccountRow(
                account_id=account.account_id,
                account_name=account.name,
                region=account.region,
                segment=account.segment,
                industry=account.industry,
                created_date=account.created_date,
                owner_employee_id=account.owner_employee_id,
                status="Churned" if account.churn_date else "Active",
                churn_date=account.churn_date,
            )
        )

    for subscription in world.subscriptions:
        rng = world.seeds.stream("crm", "subscription", subscription.subscription_id)
        product = PRODUCTS_BY_CODE[subscription.product_code]
        # Enterprise customers negotiate quarterly billing; smaller ones pay up front.
        frequency = "Quarterly" if rng.random() < 0.35 else "Annual"

        subscriptions.append(
            SubscriptionRow(
                subscription_id=subscription.subscription_id,
                account_id=subscription.account_id,
                product_code=subscription.product_code,
                product_name=product.name,
                start_date=subscription.start_date,
                end_date=subscription.end_date,
                seats=subscription.seats,
                arr_usd=subscription.arr_usd,
                billing_frequency=frequency,
            )
        )

        # Billing runs from the subscription start until it ends or the
        # extract date, whichever comes first.
        step = 91 if frequency == "Quarterly" else 365
        amount = subscription.arr_usd // 4 if frequency == "Quarterly" else subscription.arr_usd
        last_day = min(subscription.end_date or world.end_date, world.end_date)

        issue = subscription.start_date
        while issue <= last_day:
            if issue >= world.start_date:
                due = issue + timedelta(days=_NET_TERMS_DAYS)
                paid: date | None = None
                status = "Outstanding"
                if rng.random() < 0.93:
                    # Most invoices are paid; a minority run late, which is
                    # what makes a days-sales-outstanding report non-trivial.
                    delay = rng.randrange(-3, 55)
                    candidate = due + timedelta(days=delay)
                    if candidate <= world.end_date:
                        paid = candidate
                        status = "Paid"
                if paid is None and due < world.end_date:
                    status = "Overdue"

                invoices.append(
                    InvoiceRow(
                        invoice_id=f"INV{len(invoices) + 1:07d}",
                        account_id=subscription.account_id,
                        subscription_id=subscription.subscription_id,
                        issue_date=issue,
                        due_date=due,
                        paid_date=paid,
                        amount_usd=amount,
                        currency="USD",
                        status=status,
                    )
                )
            issue += timedelta(days=step)

    for account in world.accounts:
        rng = world.seeds.stream("crm", "pipeline", account.account_id)
        count = rng.randrange(1, 7)
        for _ in range(count):
            created_span = (world.end_date - max(account.created_date, world.start_date)).days
            if created_span <= 1:
                continue
            created = max(account.created_date, world.start_date) + timedelta(
                days=rng.randrange(0, created_span)
            )
            product = rng.choice(tuple(PRODUCTS_BY_CODE.values()))
            amount = int(round(rng.uniform(8_000, 420_000) / 1_000) * 1_000)

            # A deal older than its sales cycle has closed; anything more
            # recent is still open, which is what makes the pipeline snapshot
            # at the extract date look like a real one.
            cycle_days = rng.randrange(30, 210)
            closed_on = created + timedelta(days=cycle_days)
            if closed_on <= world.end_date:
                won = rng.random() < 0.34
                stage = "Closed Won" if won else "Closed Lost"
                opportunities.append(
                    OpportunityRow(
                        opportunity_id=f"OPP{len(opportunities) + 1:06d}",
                        account_id=account.account_id,
                        owner_employee_id=account.owner_employee_id,
                        product_code=product.code,
                        opportunity_type=rng.choice(_OPPORTUNITY_TYPES),
                        created_date=created,
                        close_date=closed_on,
                        stage=stage,
                        amount_usd=amount,
                        probability_pct=100 if won else 0,
                        is_won=won,
                    )
                )
            else:
                stage = rng.choice(_OPEN_STAGES)
                opportunities.append(
                    OpportunityRow(
                        opportunity_id=f"OPP{len(opportunities) + 1:06d}",
                        account_id=account.account_id,
                        owner_employee_id=account.owner_employee_id,
                        product_code=product.code,
                        opportunity_type=rng.choice(_OPPORTUNITY_TYPES),
                        created_date=created,
                        close_date=closed_on,
                        stage=stage,
                        amount_usd=amount,
                        probability_pct=_STAGE_PROBABILITY[stage],
                        is_won=None,
                    )
                )

    return (
        Table(ACCOUNT, accounts),
        Table(SUBSCRIPTION, subscriptions),
        Table(OPPORTUNITY, opportunities),
        Table(INVOICE, invoices),
    )


# --------------------------------------------------------------------------
# Defects
# --------------------------------------------------------------------------


def _duplicate_account(rows: list, rng: Random, world: HalcyonWorld) -> list[Injection]:
    """Append an exact duplicate of an existing account row.

    A primary-key violation that a CSV extract cannot prevent and that any
    naive load will happily accept, double-counting the customer.
    """
    injections: list[Injection] = []
    for index in sample_indices(rng, len(rows), 2):
        duplicate = rows[index]
        rows.append(duplicate)
        injections.append(
            Injection(
                defect="CRM_DUPLICATE_ACCOUNT",
                dataset=ACCOUNT.name,
                target_key=duplicate.account_id,
                detail="row duplicated verbatim, violating the account_id primary key",
            )
        )
    return injections


def _closed_before_created(rows: list, rng: Random, world: HalcyonWorld) -> list[Injection]:
    """Close a deal before it was created — an impossible chronology."""
    injections: list[Injection] = []
    candidates = [index for index in range(len(rows)) if rows[index].close_date is not None]
    if not candidates:
        return injections

    for index in sorted(rng.sample(candidates, k=min(3, len(candidates)))):
        shifted = rows[index].created_date - timedelta(days=rng.randrange(5, 60))
        original = replace_at(rows, index, close_date=shifted)
        injections.append(
            Injection(
                defect="CRM_CLOSED_BEFORE_CREATED",
                dataset=OPPORTUNITY.name,
                target_key=original.opportunity_id,
                detail=(
                    f"close_date {original.close_date} -> {shifted}, "
                    f"before created_date {original.created_date}"
                ),
            )
        )
    return injections


def _orphan_opportunity(rows: list, rng: Random, world: HalcyonWorld) -> list[Injection]:
    """Reference an account that is not in the account extract."""
    injections: list[Injection] = []
    for index in sample_indices(rng, len(rows), 3):
        ghost = f"A9{rng.randrange(1000, 9999)}"
        original = replace_at(rows, index, account_id=ghost)
        injections.append(
            Injection(
                defect="CRM_ORPHAN_OPPORTUNITY",
                dataset=OPPORTUNITY.name,
                target_key=original.opportunity_id,
                detail=f"account_id {original.account_id} -> {ghost} (not in crm_account)",
            )
        )
    return injections


def _negative_invoice(rows: list, rng: Random, world: HalcyonWorld) -> list[Injection]:
    """Flip an invoice negative while leaving its status as a normal charge.

    A credit note booked as an invoice: the sign is right for a refund but the
    status says otherwise, so revenue reconciles to the wrong figure.
    """
    injections: list[Injection] = []
    for index in sample_indices(rng, len(rows), 3):
        flipped = -rows[index].amount_usd
        original = replace_at(rows, index, amount_usd=flipped)
        injections.append(
            Injection(
                defect="CRM_NEGATIVE_INVOICE",
                dataset=INVOICE.name,
                target_key=original.invoice_id,
                detail=(
                    f"amount_usd {original.amount_usd} -> {flipped} "
                    f"with status still {original.status!r}"
                ),
            )
        )
    return injections


DEFECTS: tuple[DefectSpec, ...] = (
    DefectSpec(
        name="CRM_DUPLICATE_ACCOUNT",
        dataset=ACCOUNT.name,
        description="Account row duplicated, violating the primary key.",
        apply=_duplicate_account,
    ),
    DefectSpec(
        name="CRM_CLOSED_BEFORE_CREATED",
        dataset=OPPORTUNITY.name,
        description="Opportunity close_date precedes created_date.",
        apply=_closed_before_created,
    ),
    DefectSpec(
        name="CRM_ORPHAN_OPPORTUNITY",
        dataset=OPPORTUNITY.name,
        description="Opportunity references a non-existent account.",
        apply=_orphan_opportunity,
    ),
    DefectSpec(
        name="CRM_NEGATIVE_INVOICE",
        dataset=INVOICE.name,
        description="Negative invoice amount recorded with a non-credit status.",
        apply=_negative_invoice,
    ),
)
