"""Systems and security: who accessed which internal system, and how it went.

The domain the governance phase is really about. Two properties are modelled
deliberately rather than incidentally:

**Short retention.** Security logs are generated only for the most recent
window (180 days at full scale), not the full history. That is how these logs
actually work, and it means the warehouse has to cope with one fact table
whose grain and retention differ from every other — a constraint that is easy
to design for up front and painful to retrofit.

**Access that should not have happened.** The defect set includes a
terminated employee's account still being used. That is not a formatting
error; it is an access-control finding, and the kind of thing an ITGC (IT
general controls) review exists to catch. Seeding it here means the later
detection query can be scored against known truth instead of merely running.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from random import Random

from intus_gen.defects import DefectSpec, Injection, replace_at, sample_indices
from intus_gen.emit import Table
from intus_gen.sensitivity import Column, Dataset, Tier
from intus_gen.world import HalcyonWorld

_SATURDAY = 5

SYSTEMS: tuple[str, ...] = (
    "HRIS",
    "CRM",
    "Data Warehouse",
    "Finance ERP",
    "Source Control",
    "VPN",
    "Admin Console",
)

# Which departments legitimately touch which systems. The governance phase
# compares observed access against this, so it is the entitlement baseline as
# much as it is a generation parameter.
_SYSTEM_ACCESS: dict[str, tuple[str, ...]] = {
    "HRIS": ("HRS", "FIN"),
    "CRM": ("SLS", "MKT", "CSM"),
    "Data Warehouse": ("ENG", "PRD", "FIN", "SLS", "MKT"),
    "Finance ERP": ("FIN",),
    "Source Control": ("ENG", "PRD"),
    "VPN": tuple(),  # everyone
    "Admin Console": ("ITS",),
}

ACTIONS: tuple[str, ...] = ("LOGIN", "LOGOUT", "QUERY", "EXPORT", "PERMISSION_CHANGE")
_ACTION_WEIGHTS: tuple[float, ...] = (0.34, 0.28, 0.26, 0.10, 0.02)

_COUNTRY_BY_REGION: dict[str, tuple[str, ...]] = {
    "Americas": ("US", "CA", "BR"),
    "EMEA": ("GB", "IE", "NL", "DE"),
    "APAC": ("SG", "AU", "IN", "JP"),
}


@dataclass(frozen=True, slots=True)
class AccessEventRow:
    event_id: str
    event_ts: datetime
    employee_id: str | None
    department_code: str
    system: str
    action: str
    resource: str
    source_ip: str
    source_country: str
    result: str
    mfa_used: bool


ACCESS_EVENT = Dataset(
    name="sec_access_event",
    description="Authentication and access events across internal systems (180-day retention).",
    record=AccessEventRow,
    steward="Information Security",
    primary_key=("event_id",),
    retention_days=180,
    columns=(
        Column("event_id", Tier.INTERNAL, "Surrogate key for the event."),
        Column("event_ts", Tier.INTERNAL, "When the event occurred."),
        Column("employee_id", Tier.RESTRICTED, "Acting employee — individual activity."),
        Column("department_code", Tier.INTERNAL, "Actor's department at the time."),
        Column("system", Tier.INTERNAL, "System accessed."),
        Column("action", Tier.INTERNAL, "What was attempted."),
        Column("resource", Tier.CONFIDENTIAL, "Object acted on, where the system records one."),
        Column("source_ip", Tier.RESTRICTED, "Originating address — personal data under GDPR."),
        Column("source_country", Tier.CONFIDENTIAL, "Geolocated country of the source address."),
        Column("result", Tier.INTERNAL, "SUCCESS or DENIED."),
        Column("mfa_used", Tier.CONFIDENTIAL, "Whether multi-factor authentication was satisfied."),
    ),
)

DATASETS: tuple[Dataset, ...] = (ACCESS_EVENT,)

_RESOURCES: dict[str, tuple[str, ...]] = {
    "HRIS": ("employee_profile", "compensation_report", "org_chart"),
    "CRM": ("account_list", "pipeline_report", "contact_record"),
    "Data Warehouse": ("gold.revenue_summary", "gold.usage_daily", "silver.employee"),
    "Finance ERP": ("gl_journal", "budget_workbook", "vendor_master"),
    "Source Control": ("platform-core", "insight-service", "infra-config"),
    "VPN": ("corp-gateway",),
    "Admin Console": ("user_directory", "role_assignment", "audit_settings"),
}


def _ip_for(rng: Random, country: str) -> str:
    """A stable-looking private address; the octets carry no meaning."""
    return f"10.{rng.randrange(0, 256)}.{rng.randrange(0, 256)}.{rng.randrange(1, 255)}"


def build(world: HalcyonWorld) -> tuple[Table, ...]:
    rows: list[AccessEventRow] = []
    window_start = world.end_date - timedelta(days=world.profile.security_log_days)

    for person in world.people:
        rng = world.seeds.stream("access", person.employee_id)

        day = max(window_start, person.hire_date)
        while day <= world.end_date:
            if not person.employed_on(day):
                day += timedelta(days=1)
                continue

            span = person.span_on(day)
            if span is None:
                day += timedelta(days=1)
                continue

            # Weekends see a trickle of activity, not none.
            if day.weekday() >= _SATURDAY and rng.random() > 0.12:
                day += timedelta(days=1)
                continue

            permitted = [
                system
                for system in SYSTEMS
                if not _SYSTEM_ACCESS[system] or span.department in _SYSTEM_ACCESS[system]
            ]

            for _ in range(rng.randrange(1, 7)):
                # Most access is to a system the person is entitled to; the
                # occasional attempt at one they are not is what makes the
                # DENIED result meaningful.
                if rng.random() < 0.03:
                    system = rng.choice(SYSTEMS)
                    entitled = system in permitted
                else:
                    system = rng.choice(permitted)
                    entitled = True

                action = rng.choices(ACTIONS, weights=_ACTION_WEIGHTS, k=1)[0]
                # An unentitled attempt is denied; entitled access occasionally
                # fails anyway (wrong password, expired token).
                result = "SUCCESS" if entitled and rng.random() > 0.04 else "DENIED"

                moment = datetime.combine(
                    day,
                    time(
                        hour=rng.randrange(7, 21),
                        minute=rng.randrange(0, 60),
                        second=rng.randrange(0, 60),
                    ),
                )
                rows.append(
                    AccessEventRow(
                        event_id=f"SEC{len(rows) + 1:08d}",
                        event_ts=moment,
                        employee_id=person.employee_id,
                        department_code=span.department,
                        system=system,
                        action=action,
                        resource=rng.choice(_RESOURCES[system]),
                        source_ip=_ip_for(rng, person.region),
                        source_country=rng.choice(_COUNTRY_BY_REGION[person.region]),
                        result=result,
                        # Privileged systems require MFA; the rest mostly have it.
                        mfa_used=system in {"Admin Console", "Finance ERP", "HRIS"}
                        or rng.random() < 0.86,
                    )
                )
            day += timedelta(days=1)

    return (Table(ACCESS_EVENT, rows),)


# --------------------------------------------------------------------------
# Defects
# --------------------------------------------------------------------------


def _login_after_termination(rows: list, rng: Random, world: HalcyonWorld) -> list[Injection]:
    """Re-date an event so a terminated employee's account is used after they left.

    The centrepiece defect: an access-control finding rather than a data
    error. Deprovisioning that lags termination is one of the most common
    real ITGC exceptions, and it is invisible to any check that looks at the
    access log alone — it only appears when the log is joined to HR.
    """
    injections: list[Injection] = []
    leavers = [
        person
        for person in world.people
        if person.termination_date is not None
        and person.termination_date <= world.end_date
        and person.termination_date
        >= world.end_date - timedelta(days=world.profile.security_log_days)
    ]
    if not leavers or not rows:
        return injections

    for person in sorted(leavers, key=lambda p: p.employee_id)[:4]:
        index = rng.randrange(len(rows))
        after = datetime.combine(
            person.termination_date + timedelta(days=rng.randrange(1, 20)),
            time(hour=rng.randrange(0, 24), minute=rng.randrange(0, 60)),
        )
        if after.date() > world.end_date:
            continue

        original = replace_at(
            rows,
            index,
            employee_id=person.employee_id,
            event_ts=after,
            action="LOGIN",
            result="SUCCESS",
        )
        injections.append(
            Injection(
                defect="SEC_LOGIN_AFTER_TERMINATION",
                dataset=ACCESS_EVENT.name,
                target_key=original.event_id,
                detail=(
                    f"successful LOGIN by {person.employee_id} at {after} — "
                    f"terminated {person.termination_date}"
                ),
            )
        )
    return injections


def _impossible_travel(rows: list, rng: Random, world: HalcyonWorld) -> list[Injection]:
    """Two logins from different continents minutes apart — a credential-sharing signal."""
    injections: list[Injection] = []
    for index in sample_indices(rng, len(rows), 3):
        source = rows[index]
        elsewhere = "SG" if source.source_country not in {"SG", "AU", "IN", "JP"} else "US"
        twin = AccessEventRow(
            event_id=f"SEC9{index:07d}",
            event_ts=source.event_ts + timedelta(minutes=rng.randrange(3, 25)),
            employee_id=source.employee_id,
            department_code=source.department_code,
            system=source.system,
            action="LOGIN",
            resource=source.resource,
            source_ip=f"203.0.{rng.randrange(0, 256)}.{rng.randrange(1, 255)}",
            source_country=elsewhere,
            result="SUCCESS",
            mfa_used=False,
        )
        rows.append(twin)
        injections.append(
            Injection(
                defect="SEC_IMPOSSIBLE_TRAVEL",
                dataset=ACCESS_EVENT.name,
                target_key=twin.event_id,
                detail=(
                    f"{source.employee_id} logged in from {source.source_country} and "
                    f"{elsewhere} within {(twin.event_ts - source.event_ts).seconds // 60} minutes"
                ),
            )
        )
    return injections


def _missing_actor(rows: list, rng: Random, world: HalcyonWorld) -> list[Injection]:
    """Drop the employee id, leaving an event nobody can be held to."""
    injections: list[Injection] = []
    for index in sample_indices(rng, len(rows), 4):
        original = replace_at(rows, index, employee_id=None)
        injections.append(
            Injection(
                defect="SEC_MISSING_ACTOR",
                dataset=ACCESS_EVENT.name,
                target_key=original.event_id,
                detail=f"employee_id {original.employee_id} -> NULL, event is unattributable",
            )
        )
    return injections


DEFECTS: tuple[DefectSpec, ...] = (
    DefectSpec(
        name="SEC_LOGIN_AFTER_TERMINATION",
        dataset=ACCESS_EVENT.name,
        description="Successful login by an employee who had already left.",
        apply=_login_after_termination,
    ),
    DefectSpec(
        name="SEC_IMPOSSIBLE_TRAVEL",
        dataset=ACCESS_EVENT.name,
        description="Two logins from geographically impossible locations minutes apart.",
        apply=_impossible_travel,
    ),
    DefectSpec(
        name="SEC_MISSING_ACTOR",
        dataset=ACCESS_EVENT.name,
        description="Access event with no attributable actor.",
        apply=_missing_actor,
    ),
)
