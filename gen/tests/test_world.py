"""Invariants of the generated company itself.

Most of these look obvious written down and are exactly the ones a generator
gets wrong: an employee managed by someone who left before they joined, an SCD2
history with a gap in it, an email address reused after a name collision.
"""

from __future__ import annotations

from datetime import date
from itertools import pairwise

from intus_gen.world import DEPARTMENTS_BY_CODE, Scale, build_world


def test_population_is_deterministic(world, test_seed, as_of):
    again = build_world(seed=test_seed, scale=Scale.SMALL, end_date=as_of)
    assert again.people == world.people
    assert again.accounts == world.accounts
    assert again.subscriptions == world.subscriptions


def test_a_different_seed_gives_a_different_company(world, test_seed, as_of):
    other = build_world(seed=test_seed + 1, scale=Scale.SMALL, end_date=as_of)
    assert other.people != world.people


def test_as_of_date_is_an_input_not_the_clock(world, test_seed, as_of):
    """The extract date drives the data, so regenerating tomorrow changes nothing.

    The complement matters too: a *different* as-of date must produce
    different data, or the parameter is being ignored.
    """
    assert world.end_date == as_of
    shifted = build_world(seed=test_seed, scale=Scale.SMALL, end_date=date(2026, 5, 31))
    assert shifted.people != world.people


def test_emails_are_unique(world):
    emails = [person.work_email for person in world.people]
    assert len(emails) == len(set(emails))


def test_employee_ids_are_unique(world):
    ids = [person.employee_id for person in world.people]
    assert len(ids) == len(set(ids))


def test_spans_are_contiguous_and_non_overlapping(world):
    """SCD2 source data: each span starts exactly where the previous one ended."""
    for person in world.people:
        assert person.spans, f"{person.employee_id} has no spans"
        assert person.spans[0].valid_from == person.hire_date
        for earlier, later in pairwise(person.spans):
            assert earlier.valid_to == later.valid_from, person.employee_id
            assert earlier.valid_from < earlier.valid_to


def test_no_span_has_zero_length(as_of):
    """A termination landing exactly on a work anniversary once produced a
    valid_from == valid_to final span — impossible to load under the
    warehouse's SCD2 exclusion constraint, and not physically meaningful
    either (a role lasting zero days). Checked for every span, not just the
    non-final ones test_spans_are_contiguous_and_non_overlapping covers,
    since the bug was specifically in the final span.

    Swept over a range of seeds rather than relying on the shared `world`
    fixture's one seed: the bug depends on a termination date landing exactly
    on a hire-date anniversary, which most seeds simply do not produce. The
    fixture's default seed (4242) is one of them — this test passed against
    the unfixed generator until seed 2 was tried, which is precisely the
    coverage gap a single fixed seed leaves. All at SMALL scale, so the sweep
    stays cheap.
    """
    for seed in range(20):
        world = build_world(seed=seed, scale=Scale.SMALL, end_date=as_of)
        for person in world.people:
            for span in person.spans:
                if span.valid_to is not None:
                    assert span.valid_from < span.valid_to, (
                        seed,
                        person.employee_id,
                        span.valid_from,
                    )


def test_final_span_matches_termination(world):
    for person in world.people:
        assert person.spans[-1].valid_to == person.termination_date


def test_span_lookup_agrees_with_employment(world):
    """`span_on` and `employed_on` must never disagree — later phases join on both."""
    probe_days = (world.start_date, world.end_date, world.end_date.replace(day=1))
    for person in world.people:
        for day in probe_days:
            if person.employed_on(day):
                assert person.span_on(day) is not None, (person.employee_id, day)
            else:
                assert person.span_on(day) is None, (person.employee_id, day)


def test_managers_exist_and_are_not_self(world):
    for person in world.people:
        for span in person.spans:
            if span.manager_id is None:
                continue
            assert span.manager_id in world.people_by_id
            assert span.manager_id != person.employee_id


def test_exactly_one_person_has_no_manager(world):
    """The CEO, and only the CEO."""
    unmanaged = [
        person for person in world.people if all(span.manager_id is None for span in person.spans)
    ]
    assert len(unmanaged) == 1
    assert unmanaged[0].spans[-1].title == "Chief Executive Officer"


def test_departments_are_known(world):
    for person in world.people:
        for span in person.spans:
            assert span.department in DEPARTMENTS_BY_CODE


def test_salaries_are_plausible(world):
    for person in world.people:
        for span in person.spans:
            assert 20_000 < span.annual_salary_usd < 1_500_000, person.employee_id


def test_terminations_fall_after_hire(world):
    for person in world.people:
        if person.termination_date is not None:
            assert person.termination_date > person.hire_date
            assert person.termination_reason is not None


def test_population_contains_both_leavers_and_stayers(world):
    """Otherwise the HR domain cannot demonstrate history at all."""
    leavers = [person for person in world.people if person.termination_date is not None]
    stayers = [person for person in world.people if person.termination_date is None]
    assert leavers and stayers


def test_accounts_are_owned_by_real_employees(world):
    for account in world.accounts:
        assert account.owner_employee_id in world.people_by_id


def test_account_names_are_unique(world):
    names = [account.name for account in world.accounts]
    assert len(names) == len(set(names))


def test_subscriptions_reference_real_accounts_and_products(world):
    from intus_gen.world import PRODUCTS_BY_CODE

    for subscription in world.subscriptions:
        assert subscription.account_id in world.accounts_by_id
        assert subscription.product_code in PRODUCTS_BY_CODE
        assert subscription.seats > 0
        assert subscription.arr_usd > 0


def test_subscription_starts_after_the_account_opens(world):
    for subscription in world.subscriptions:
        account = world.accounts_by_id[subscription.account_id]
        assert subscription.start_date >= account.created_date
        if subscription.end_date is not None:
            assert subscription.end_date >= subscription.start_date


def test_churned_accounts_end_their_subscriptions(world):
    for account in world.accounts:
        if account.churn_date is None:
            continue
        for subscription in world.subscriptions_for(account.account_id):
            assert subscription.end_date is not None
            assert subscription.end_date <= account.churn_date
