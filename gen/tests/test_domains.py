"""Cross-domain referential integrity on clean data.

These are the joins the warehouse phase is going to build. Asserting them here
means a broken foreign key is caught by a two-second unit test rather than by a
load that fails halfway through, and it is the evidence for the claim that the
domains form one dataset rather than six unrelated files.
"""

from __future__ import annotations

from decimal import Decimal

from intus_gen.domains import all_datasets, all_domains, build_all
from intus_gen.domains.ai_usage import MODELS_BY_NAME, cost_for
from intus_gen.domains.finance import GL_BY_CODE
from intus_gen.world import DEPARTMENTS, PRODUCTS_BY_CODE


def test_every_declared_dataset_is_produced(world, clean_by_name):
    assert set(clean_by_name) == {dataset.name for dataset in all_datasets()}


def test_every_domain_produces_rows(clean_by_name):
    """A silently empty domain would pass every other test in this file."""
    for name, table in clean_by_name.items():
        assert table.rows, f"{name} generated no rows"


def test_rows_match_their_declared_record_type(clean_tables):
    for table in clean_tables:
        for row in table.rows:
            assert isinstance(row, table.dataset.record), table.name


def test_generation_is_deterministic(world):
    first = {table.name: table.rows for table in build_all(world)}
    second = {table.name: table.rows for table in build_all(world)}
    assert first == second


# --------------------------------------------------------------------------
# Referential integrity
# --------------------------------------------------------------------------


def test_hr_references_the_employee_population(world, clean_by_name):
    known = set(world.people_by_id)
    for row in clean_by_name["hr_employee_history"].rows:
        assert row.employee_id in known
        if row.manager_id is not None:
            assert row.manager_id in known
    for row in clean_by_name["hr_compensation"].rows:
        assert row.employee_id in known
    for row in clean_by_name["hr_performance_review"].rows:
        assert row.employee_id in known


def test_crm_foreign_keys_resolve(world, clean_by_name):
    accounts = {row.account_id for row in clean_by_name["crm_account"].rows}
    subscriptions = {row.subscription_id for row in clean_by_name["crm_subscription"].rows}

    for row in clean_by_name["crm_subscription"].rows:
        assert row.account_id in accounts
        assert row.product_code in PRODUCTS_BY_CODE
    for row in clean_by_name["crm_opportunity"].rows:
        assert row.account_id in accounts
        assert row.owner_employee_id in world.people_by_id
    for row in clean_by_name["crm_invoice"].rows:
        assert row.account_id in accounts
        assert row.subscription_id in subscriptions


def test_telemetry_references_real_accounts(clean_by_name):
    accounts = {row.account_id for row in clean_by_name["crm_account"].rows}
    for row in clean_by_name["usage_daily"].rows:
        assert row.account_id in accounts
        assert row.product_code in PRODUCTS_BY_CODE


def test_ai_usage_references_real_employees(world, clean_by_name):
    for row in clean_by_name["ai_usage_event"].rows:
        assert row.employee_id in world.people_by_id
        assert row.model in MODELS_BY_NAME


def test_finance_references_real_cost_centres(world, clean_by_name):
    cost_centres = {department.cost_center for department in DEPARTMENTS}
    employees = set(world.people_by_id)

    for row in clean_by_name["fin_budget"].rows:
        assert row.cost_center in cost_centres
        assert row.gl_account in GL_BY_CODE
        assert row.approved_by in employees
    for row in clean_by_name["fin_actual"].rows:
        assert row.cost_center in cost_centres
        assert row.gl_account in GL_BY_CODE
        assert row.posted_by in employees


def test_access_events_reference_real_employees(world, clean_by_name):
    for row in clean_by_name["sec_access_event"].rows:
        assert row.employee_id in world.people_by_id


# --------------------------------------------------------------------------
# Domain-specific semantics
# --------------------------------------------------------------------------


def test_primary_keys_are_unique_on_clean_data(clean_tables):
    for table in clean_tables:
        keys = [
            tuple(getattr(row, field) for field in table.dataset.primary_key) for row in table.rows
        ]
        assert len(keys) == len(set(keys)), f"{table.name} has duplicate primary keys"


def test_access_log_respects_its_retention_window(world, clean_by_name):
    """Security logs cover only the recent window, unlike every other domain."""
    horizon = world.end_date - __import__("datetime").timedelta(
        days=world.profile.security_log_days
    )
    for row in clean_by_name["sec_access_event"].rows:
        assert horizon <= row.event_ts.date() <= world.end_date


def test_access_events_fall_within_employment(world, clean_by_name):
    """Clean data must contain no access after termination — that is a seeded defect."""
    for row in clean_by_name["sec_access_event"].rows:
        person = world.people_by_id[row.employee_id]
        assert person.employed_on(row.event_ts.date()), row.event_id


def test_ai_cost_reconciles_to_tokens(clean_by_name):
    """Clean data must satisfy the arithmetic the cost-mismatch defect breaks."""
    for row in clean_by_name["ai_usage_event"].rows:
        expected = cost_for(MODELS_BY_NAME[row.model], row.prompt_tokens, row.completion_tokens)
        assert row.cost_usd == expected, row.event_id


def test_money_columns_are_decimal_not_float(clean_by_name):
    """Guards against a float creeping back into a monetary column."""
    for row in clean_by_name["fin_actual"].rows[:200]:
        assert isinstance(row.amount_usd, Decimal)
    for row in clean_by_name["ai_usage_event"].rows[:200]:
        assert isinstance(row.cost_usd, Decimal)


def test_salary_actuals_tie_to_the_hr_population(world, clean_by_name):
    """The deliberate cross-domain reconciliation: payroll expense comes from HR.

    Budget is a forecast and deviates by design, so the tie is asserted
    against actuals, which are derived from the population directly.
    """
    from intus_gen.fiscal import periods_between

    period = periods_between(world.start_date, world.end_date)[1]
    expected: dict[str, float] = {}
    for person in world.people:
        if not person.employed_on(period.end_date):
            continue
        span = person.span_on(period.end_date)
        if span is None:
            continue
        expected[span.department] = expected.get(span.department, 0.0) + span.annual_salary_usd / 12

    posted: dict[str, Decimal] = {}
    for row in clean_by_name["fin_actual"].rows:
        if row.fiscal_period == period.key and row.gl_account == "6000":
            posted[row.department_code] = (
                posted.get(row.department_code, Decimal(0)) + row.amount_usd
            )

    assert posted, "no salary postings found for the probe period"
    for department, amount in posted.items():
        assert abs(float(amount) - expected[department]) < 1.0, department


def test_invoices_fall_inside_their_subscription(clean_by_name):
    subscriptions = {row.subscription_id: row for row in clean_by_name["crm_subscription"].rows}
    for row in clean_by_name["crm_invoice"].rows:
        subscription = subscriptions[row.subscription_id]
        assert row.issue_date >= subscription.start_date
        assert row.due_date > row.issue_date


def test_opportunities_close_after_they_open(clean_by_name):
    for row in clean_by_name["crm_opportunity"].rows:
        if row.close_date is not None:
            assert row.close_date >= row.created_date, row.opportunity_id


def test_open_opportunities_have_no_outcome(clean_by_name):
    for row in clean_by_name["crm_opportunity"].rows:
        if row.stage.startswith("Closed"):
            assert row.is_won is not None
        else:
            assert row.is_won is None


def test_usage_counters_are_non_negative(clean_by_name):
    for row in clean_by_name["usage_daily"].rows:
        assert row.active_users >= 0
        assert row.sessions >= 0
        assert row.api_calls >= 0


def test_every_domain_declares_defects():
    for domain in all_domains():
        assert domain.DEFECTS, f"{domain.__name__} declares no defects"
