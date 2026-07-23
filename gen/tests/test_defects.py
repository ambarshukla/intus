"""Defect injection, and the honesty of its manifest.

The manifest is the ground truth a later data-quality framework is scored
against, so the tests that matter here are not "does injection run" but "does
the manifest describe what actually happened". A defect that silently no-ops
while still reporting itself would make every future detection metric a lie —
and would look exactly like a passing test suite.
"""

from __future__ import annotations

from datetime import timedelta
from itertools import pairwise

from intus_gen.defects import inject
from intus_gen.domains import all_defects, build_all


def test_clean_run_injects_nothing(world, clean_tables):
    """With no specs applied the tables are untouched."""
    before = {table.name: list(table.rows) for table in clean_tables}
    assert inject(clean_tables, (), world) == ()
    assert {table.name: table.rows for table in clean_tables} == before


def test_every_declared_defect_actually_fires(injected):
    """Each spec must appear in the manifest, or it is dead code pretending to work."""
    _tables, injections = injected
    fired = {injection.defect for injection in injections}
    declared = {spec.name for spec in all_defects()}
    assert declared - fired == set(), f"declared but never injected: {sorted(declared - fired)}"


def test_injection_is_deterministic(world):
    first_tables = build_all(world)
    second_tables = build_all(world)
    first = inject(first_tables, all_defects(), world)
    second = inject(second_tables, all_defects(), world)
    assert first == second
    assert {t.name: t.rows for t in first_tables} == {t.name: t.rows for t in second_tables}


def test_injection_changes_the_data(world):
    """The complement of the determinism test: injection is not a no-op."""
    clean = {table.name: list(table.rows) for table in build_all(world)}
    dirty_tables = build_all(world)
    inject(dirty_tables, all_defects(), world)
    dirty = {table.name: table.rows for table in dirty_tables}
    assert any(clean[name] != dirty[name] for name in clean)


def test_manifest_targets_name_real_datasets(injected):
    tables, injections = injected
    names = {table.name for table in tables}
    for injection in injections:
        assert injection.dataset in names
        assert injection.detail.strip()
        assert injection.target_key.strip()


# --------------------------------------------------------------------------
# Each defect is verified against the data it claims to have broken
# --------------------------------------------------------------------------


def _keys(injections, defect):
    return {injection.target_key for injection in injections if injection.defect == defect}


def test_orphan_manager_really_orphans(world, injected_by_name):
    tables, injections = injected_by_name
    targets = _keys(injections, "HR_ORPHAN_MANAGER")
    assert targets

    broken = {
        f"{row.employee_id}|{row.valid_from}"
        for row in tables["hr_employee_history"].rows
        if row.manager_id is not None and row.manager_id not in world.people_by_id
    }
    assert targets <= broken


def test_overlapping_spans_really_overlap(injected_by_name):
    tables, injections = injected_by_name
    assert _keys(injections, "HR_OVERLAPPING_SPAN")

    rows = sorted(
        tables["hr_employee_history"].rows, key=lambda row: (row.employee_id, row.valid_from)
    )
    overlaps = [
        earlier
        for earlier, later in pairwise(rows)
        if earlier.employee_id == later.employee_id
        and earlier.valid_to is not None
        and later.valid_from < earlier.valid_to
    ]
    assert overlaps, "no overlapping spans found despite the defect being reported"


def test_duplicate_account_breaks_the_primary_key(injected_by_name):
    tables, injections = injected_by_name
    targets = _keys(injections, "CRM_DUPLICATE_ACCOUNT")
    assert targets

    seen: dict[str, int] = {}
    for row in tables["crm_account"].rows:
        seen[row.account_id] = seen.get(row.account_id, 0) + 1
    assert {key for key, count in seen.items() if count > 1} == targets


def test_orphan_opportunity_references_a_missing_account(injected_by_name):
    tables, injections = injected_by_name
    targets = _keys(injections, "CRM_ORPHAN_OPPORTUNITY")
    assert targets

    accounts = {row.account_id for row in tables["crm_account"].rows}
    orphans = {
        row.opportunity_id
        for row in tables["crm_opportunity"].rows
        if row.account_id not in accounts
    }
    assert targets <= orphans


def test_closed_before_created_is_chronologically_impossible(injected_by_name):
    tables, injections = injected_by_name
    targets = _keys(injections, "CRM_CLOSED_BEFORE_CREATED")
    assert targets

    impossible = {
        row.opportunity_id
        for row in tables["crm_opportunity"].rows
        if row.close_date is not None and row.close_date < row.created_date
    }
    assert targets <= impossible


def test_ai_cost_mismatch_breaks_reconciliation(injected_by_name):
    from intus_gen.domains.ai_usage import MODELS_BY_NAME, cost_for

    tables, injections = injected_by_name
    targets = _keys(injections, "AI_COST_MISMATCH")
    assert targets

    mismatched = {
        row.event_id
        for row in tables["ai_usage_event"].rows
        if row.model in MODELS_BY_NAME
        and row.cost_usd
        != cost_for(MODELS_BY_NAME[row.model], row.prompt_tokens, row.completion_tokens)
    }
    assert targets <= mismatched


def test_unknown_model_is_outside_the_catalog(injected_by_name):
    from intus_gen.domains.ai_usage import MODELS_BY_NAME

    tables, injections = injected_by_name
    targets = _keys(injections, "AI_UNKNOWN_MODEL")
    assert targets

    unknown = {
        row.event_id for row in tables["ai_usage_event"].rows if row.model not in MODELS_BY_NAME
    }
    assert targets <= unknown


def test_login_after_termination_is_genuinely_after(world, injected_by_name):
    """The centrepiece defect, verified against HR rather than taken on trust."""
    tables, injections = injected_by_name
    targets = _keys(injections, "SEC_LOGIN_AFTER_TERMINATION")
    assert targets, "the access-control defect must fire; the governance phase scores against it"

    violations = set()
    for row in tables["sec_access_event"].rows:
        if row.employee_id is None:
            continue
        person = world.people_by_id.get(row.employee_id)
        if person is None or person.termination_date is None:
            continue
        if row.event_ts.date() >= person.termination_date:
            violations.add(row.event_id)
    assert targets <= violations


def test_impossible_travel_pairs_are_close_in_time_and_far_apart(injected_by_name):
    tables, injections = injected_by_name
    targets = _keys(injections, "SEC_IMPOSSIBLE_TRAVEL")
    assert targets

    by_id = {row.event_id: row for row in tables["sec_access_event"].rows}
    for target in targets:
        twin = by_id[target]
        siblings = [
            row
            for row in tables["sec_access_event"].rows
            if row.employee_id == twin.employee_id
            and row.event_id != twin.event_id
            and abs(row.event_ts - twin.event_ts) < timedelta(hours=1)
            and row.source_country != twin.source_country
        ]
        assert siblings, f"{target} has no impossible-travel counterpart"


def test_missing_actor_is_null(injected_by_name):
    tables, injections = injected_by_name
    targets = _keys(injections, "SEC_MISSING_ACTOR")
    assert targets

    unattributable = {
        row.event_id for row in tables["sec_access_event"].rows if row.employee_id is None
    }
    assert targets <= unattributable


def test_closed_period_posting_disagrees_with_its_date(injected_by_name):
    from intus_gen.fiscal import period_for

    tables, injections = injected_by_name
    targets = _keys(injections, "FIN_CLOSED_PERIOD_POSTING")
    assert targets

    inconsistent = {
        row.actual_id
        for row in tables["fin_actual"].rows
        if period_for(row.posting_date) != row.fiscal_period
    }
    assert targets <= inconsistent


def test_orphan_cost_centre_is_not_in_the_org(injected_by_name):
    from intus_gen.world import DEPARTMENTS

    tables, injections = injected_by_name
    targets = _keys(injections, "FIN_ORPHAN_COST_CENTER")
    assert targets

    known = {department.cost_center for department in DEPARTMENTS}
    orphans = {row.actual_id for row in tables["fin_actual"].rows if row.cost_center not in known}
    assert targets <= orphans


def test_unauthorised_approver_is_not_an_employee(world, injected_by_name):
    tables, injections = injected_by_name
    targets = _keys(injections, "FIN_UNAUTHORISED_APPROVER")
    assert targets

    unauthorised = {
        row.budget_id
        for row in tables["fin_budget"].rows
        if row.approved_by not in world.people_by_id
    }
    assert targets <= unauthorised


def test_salary_outlier_is_an_outlier(world, injected_by_name):
    """Compared against the generated pay scale, not a hard-coded threshold.

    A fixed cutoff quietly stops testing anything: the tenfold error applied
    to a junior salary can still land below it, so the assertion passes while
    the defect goes unverified.
    """
    tables, injections = injected_by_name
    targets = _keys(injections, "HR_SALARY_OUTLIER")
    assert targets

    highest_legitimate = max(
        span.annual_salary_usd for person in world.people for span in person.spans
    )
    inflated = {
        row.compensation_id
        for row in tables["hr_compensation"].rows
        if row.annual_salary_usd > highest_legitimate
    }
    assert targets <= inflated


def test_negative_invoice_is_negative(injected_by_name):
    tables, injections = injected_by_name
    targets = _keys(injections, "CRM_NEGATIVE_INVOICE")
    assert targets

    negative = {row.invoice_id for row in tables["crm_invoice"].rows if row.amount_usd < 0}
    assert targets <= negative


def test_usage_defects_land(injected_by_name):
    tables, injections = injected_by_name

    assert _keys(injections, "USAGE_NEGATIVE_SESSIONS")
    assert any(row.sessions < 0 for row in tables["usage_daily"].rows)

    accounts = {row.account_id for row in tables["crm_account"].rows}
    assert _keys(injections, "USAGE_UNKNOWN_ACCOUNT")
    assert any(row.account_id not in accounts for row in tables["usage_daily"].rows)

    assert _keys(injections, "USAGE_DUPLICATE_EVENT")
    keys = [
        (row.usage_date, row.account_id, row.product_code) for row in tables["usage_daily"].rows
    ]
    assert len(keys) != len(set(keys))


def test_missing_termination_reason_is_null(injected_by_name):
    tables, injections = injected_by_name
    targets = _keys(injections, "HR_MISSING_TERMINATION_REASON")
    assert targets

    blank = {
        f"{row.employee_id}|{row.valid_from}"
        for row in tables["hr_employee_history"].rows
        if row.termination_date is not None and row.termination_reason is None
    }
    assert targets <= blank


def test_every_target_key_resolves_to_a_row(injected):
    """The manifest must join to the data it describes.

    ``target_key`` is a row's primary key with the components joined by ``|``.
    That contract is what lets a downstream data-quality layer score its
    detections against the seeded truth, and it is silently broken by any
    defect that corrupts a key column: the manifest then names a row that no
    longer exists.

    HR_OVERLAPPING_SPAN did exactly that — it recorded the pre-corruption
    ``valid_from`` — and nothing noticed until the warehouse tried to use the
    manifest, because every other test only checked that keys were non-empty.
    """
    tables, injections = injected
    by_name = {table.name: table for table in tables}

    keys_by_dataset: dict[str, set[str]] = {}
    for name, table in by_name.items():
        primary_key = table.dataset.primary_key
        keys_by_dataset[name] = {
            "|".join(str(getattr(row, field)) for field in primary_key) for row in table.rows
        }

    unresolved = [
        (injection.defect, injection.target_key)
        for injection in injections
        if injection.target_key not in keys_by_dataset[injection.dataset]
    ]
    assert unresolved == [], f"manifest keys that match no row: {unresolved}"
