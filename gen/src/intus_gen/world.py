"""Halcyon: the fictional company every domain generator draws on.

The single most important structural decision in this package is that the
*entities* are built once, here, and handed to every domain generator — rather
than each generator inventing its own keys.

Domains that cannot join are not a dataset, they are six unrelated files. The
warehouse phase needs employees to appear in HR records, in the AI-usage logs,
in the security logs and as owners of CRM accounts, all under the same
``employee_id``; the governance phase needs a salesperson's region to be the
same fact in every table, because that is what a row-level-security predicate
filters on. Building the world first is what makes those joins real.

Halcyon is invented in full: ~800 employees across three regions, selling four
software products to mid-market and enterprise customers. Every name here is
fictional and every number is generated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from random import Random

from intus_gen.seeds import Seeds

# --------------------------------------------------------------------------
# Scale
# --------------------------------------------------------------------------


class Scale(StrEnum):
    """How much data to generate.

    ``SMALL`` exists so the test suite and CI exercise the *real* generators
    rather than a mock: same code paths, same invariants, a few seconds. A
    fixture that only ever ran at full scale would be skipped in practice, and
    a fixture that mocked the generators would test the mock.
    """

    SMALL = "small"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class ScaleProfile:
    headcount: int  # target active headcount at the end date
    accounts: int
    days: int  # length of the generated history
    security_log_days: int  # security logs retain less; see access.py


_PROFILES: dict[Scale, ScaleProfile] = {
    Scale.SMALL: ScaleProfile(headcount=60, accounts=45, days=120, security_log_days=45),
    # 912 days ≈ 2.5 years: enough history for year-over-year comparison, an
    # SCD2 dimension with several changes per employee, and a budget cycle
    # that repeats — the three things the warehouse phase needs to be
    # interesting rather than merely present.
    Scale.FULL: ScaleProfile(headcount=800, accounts=1100, days=912, security_log_days=180),
}


def profile_for(scale: Scale) -> ScaleProfile:
    return _PROFILES[scale]


# --------------------------------------------------------------------------
# Org structure
# --------------------------------------------------------------------------

REGIONS: tuple[str, ...] = ("Americas", "EMEA", "APAC")

# Weighted so the Americas dominates, as it would for a company headquartered
# there. Used for both employees and customers.
_REGION_WEIGHTS: tuple[float, ...] = (0.55, 0.29, 0.16)

LOCATIONS: dict[str, tuple[str, ...]] = {
    "Americas": ("New York", "Austin", "Toronto", "Chicago"),
    "EMEA": ("London", "Dublin", "Amsterdam", "Frankfurt"),
    "APAC": ("Singapore", "Sydney", "Bengaluru", "Tokyo"),
}


@dataclass(frozen=True, slots=True)
class Department:
    code: str
    name: str
    cost_center: str
    #: R&D / S&M / G&A — the expense-classification rollup finance reports on.
    function: str
    #: Share of total headcount, used to size the department.
    headcount_share: float
    #: Job-title noun for the individual-contributor ladder.
    ic_noun: str


DEPARTMENTS: tuple[Department, ...] = (
    Department("ENG", "Engineering", "CC-1000", "R&D", 0.34, "Engineer"),
    Department("PRD", "Product", "CC-1100", "R&D", 0.06, "Product Manager"),
    Department("SLS", "Sales", "CC-2000", "S&M", 0.18, "Account Executive"),
    Department("MKT", "Marketing", "CC-2100", "S&M", 0.07, "Marketing Specialist"),
    Department("CSM", "Customer Success", "CC-2200", "S&M", 0.14, "Customer Success Manager"),
    Department("FIN", "Finance", "CC-3000", "G&A", 0.06, "Financial Analyst"),
    Department("HRS", "People", "CC-3100", "G&A", 0.05, "People Partner"),
    Department("ITS", "Information Technology", "CC-3200", "G&A", 0.07, "Systems Engineer"),
    Department("LGL", "Legal", "CC-3300", "G&A", 0.03, "Counsel"),
)

DEPARTMENTS_BY_CODE: dict[str, Department] = {dept.code: dept for dept in DEPARTMENTS}

# Job levels 1-4 are individual contributors, 5-7 management, 8 the CEO.
_IC_PREFIX: dict[int, str] = {1: "Associate ", 2: "", 3: "Senior ", 4: "Staff "}
_MAX_IC_LEVEL = 4
_CEO_LEVEL = 8
_VP_LEVEL = 7

# Base salary by level in USD, before regional and functional adjustment.
_SALARY_BY_LEVEL: dict[int, int] = {
    1: 78_000,
    2: 98_000,
    3: 126_000,
    4: 158_000,
    5: 180_000,
    6: 224_000,
    7: 295_000,
    8: 440_000,
}

# Cost of labour differs by market; these are the generator's assumptions, not
# a claim about any real market.
_REGION_SALARY_FACTOR: dict[str, float] = {"Americas": 1.0, "EMEA": 0.88, "APAC": 0.72}
_FUNCTION_SALARY_FACTOR: dict[str, float] = {"R&D": 1.15, "S&M": 1.0, "G&A": 0.92}

# Level mix for non-executive staff: a real company is bottom-heavy.
_LEVEL_CHOICES: tuple[int, ...] = (1, 2, 3, 4, 5, 6)
_LEVEL_WEIGHTS: tuple[float, ...] = (0.17, 0.30, 0.26, 0.14, 0.09, 0.04)

# fmt: off
_FIRST_NAMES: tuple[str, ...] = (
    "Amara", "Priya", "Daniel", "Mei", "Tomas", "Aisha", "Lukas", "Sofia",
    "Kenji", "Nadia", "Marcus", "Yuki", "Elena", "Rohan", "Clara", "Diego",
    "Ingrid", "Omar", "Hannah", "Jae", "Farida", "Peter", "Anika", "Mateo",
    "Grace", "Viktor", "Leilani", "Samuel", "Zara", "Andres", "Freya", "Hugo",
    "Noor", "Emil", "Camila", "Arjun", "Marta", "Oliver", "Sana", "Felix",
    "Bianca", "Nikolai", "Thandiwe", "Jonas", "Meera", "Callum", "Yara", "Ravi",
)
# fmt: on

# fmt: off
_LAST_NAMES: tuple[str, ...] = (
    "Okafor", "Sharma", "Whitfield", "Chen", "Novak", "Rahman", "Berger",
    "Moreno", "Tanaka", "Haddad", "Lindqvist", "Osei", "Castellano", "Iyer",
    "Dubois", "Petrov", "Almeida", "Kowalski", "Nakamura", "Bergstrom", "Villanueva",
    "Hoffmann", "Adeyemi", "Rossi", "Mbeki", "Larsen", "Delgado", "Fitzgerald",
    "Kaur", "Sorensen", "Moretti", "Achebe", "Vasquez", "Lindgren", "Bhatt",
    "Kowalczyk", "Duarte", "Erikson", "Nguyen", "Abadi", "Sandoval", "Halvorsen",
    "Trivedi", "Barbieri", "Oyelaran", "Marchetti", "Steinberg", "Karlsson",
)
# fmt: on


@dataclass(frozen=True, slots=True)
class EmploymentSpan:
    """One slowly-changing slice of a person's record: attributes valid over a range.

    This is generated in SCD2 shape on purpose. The warehouse phase builds a
    type-2 employee dimension, and building it from source data that already
    has honest effective dating — including same-day corrections and the
    occasional back-dated change — is the difference between exercising an
    SCD2 merge and merely demonstrating one.

    ``valid_to`` is exclusive and ``None`` means "still current".
    """

    valid_from: date
    valid_to: date | None
    department: str
    job_level: int
    title: str
    manager_id: str | None
    location: str
    employment_type: str
    annual_salary_usd: int
    change_reason: str


@dataclass(frozen=True, slots=True)
class Person:
    """An employee, past or present, with their full attribute history."""

    employee_id: str
    first_name: str
    last_name: str
    work_email: str
    region: str
    hire_date: date
    termination_date: date | None
    termination_reason: str | None
    spans: tuple[EmploymentSpan, ...]

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def current(self) -> EmploymentSpan:
        """The most recent span — the person's state as of their last change."""
        return self.spans[-1]

    def span_on(self, day: date) -> EmploymentSpan | None:
        """The span in force on ``day``, or ``None`` if not employed then."""
        for span in self.spans:
            if span.valid_from <= day and (span.valid_to is None or day < span.valid_to):
                return span
        return None

    def employed_on(self, day: date) -> bool:
        if day < self.hire_date:
            return False
        return self.termination_date is None or day < self.termination_date


# --------------------------------------------------------------------------
# Customers
# --------------------------------------------------------------------------

SEGMENTS: tuple[str, ...] = ("Enterprise", "Mid-Market", "SMB")
_SEGMENT_WEIGHTS: tuple[float, ...] = (0.18, 0.42, 0.40)

INDUSTRIES: tuple[str, ...] = (
    "Financial Services",
    "Healthcare",
    "Manufacturing",
    "Retail",
    "Technology",
    "Logistics",
    "Energy",
    "Public Sector",
    "Media",
    "Professional Services",
)

_ACCOUNT_PREFIXES: tuple[str, ...] = (
    "Northwind",
    "Ironbridge",
    "Calder",
    "Vantage",
    "Redstone",
    "Blackpine",
    "Aurelia",
    "Kestrel",
    "Meridian",
    "Silverbeck",
    "Thornbury",
    "Foxglove",
    "Halloway",
    "Brightwater",
    "Ashford",
    "Larkspur",
    "Copperfield",
    "Wrenfield",
    "Marlowe",
    "Eastgate",
    "Fairhaven",
    "Quarrystone",
    "Windermere",
    "Alderman",
)

_ACCOUNT_NOUNS: tuple[str, ...] = (
    "Logistics",
    "Analytics",
    "Systems",
    "Industries",
    "Health",
    "Financial",
    "Energy",
    "Media",
    "Retail",
    "Manufacturing",
    "Networks",
    "Partners",
    "Technologies",
    "Dynamics",
    "Solutions",
    "Capital",
)

_ACCOUNT_SUFFIXES: tuple[str, ...] = ("Group", "Holdings", "Inc.", "Ltd.", "AG", "PLC", "Co.")


@dataclass(frozen=True, slots=True)
class Product:
    code: str
    name: str
    #: List price per seat per year, in USD.
    list_price_per_seat: int


PRODUCTS: tuple[Product, ...] = (
    Product("HAL-CORE", "Halcyon Core Platform", 1_200),
    Product("HAL-INSIGHT", "Halcyon Insight Analytics", 900),
    Product("HAL-CONNECT", "Halcyon Connect", 480),
    Product("HAL-GUARD", "Halcyon Guard", 660),
)

PRODUCTS_BY_CODE: dict[str, Product] = {product.code: product for product in PRODUCTS}

# Attach rates: nearly every customer buys Core, fewer buy the add-ons.
_PRODUCT_ATTACH_RATE: dict[str, float] = {
    "HAL-CORE": 0.97,
    "HAL-INSIGHT": 0.46,
    "HAL-CONNECT": 0.31,
    "HAL-GUARD": 0.22,
}

_SEGMENT_SEATS: dict[str, tuple[int, int]] = {
    "Enterprise": (250, 4_000),
    "Mid-Market": (40, 250),
    "SMB": (5, 40),
}

# Larger customers negotiate larger discounts off list.
_SEGMENT_DISCOUNT: dict[str, tuple[float, float]] = {
    "Enterprise": (0.22, 0.45),
    "Mid-Market": (0.08, 0.25),
    "SMB": (0.0, 0.12),
}


@dataclass(frozen=True, slots=True)
class Account:
    account_id: str
    name: str
    region: str
    segment: str
    industry: str
    created_date: date
    #: Sales rep who owns the relationship — a cross-domain foreign key into
    #: the employee population, and the basis of the sales RLS predicate.
    owner_employee_id: str
    churn_date: date | None


@dataclass(frozen=True, slots=True)
class Subscription:
    subscription_id: str
    account_id: str
    product_code: str
    start_date: date
    end_date: date | None
    seats: int
    arr_usd: int


# --------------------------------------------------------------------------
# The world
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HalcyonWorld:
    """Every cross-domain entity, built once and shared by all generators."""

    scale: Scale
    seeds: Seeds
    start_date: date
    end_date: date
    people: tuple[Person, ...]
    accounts: tuple[Account, ...]
    subscriptions: tuple[Subscription, ...]
    people_by_id: dict[str, Person]
    accounts_by_id: dict[str, Account]

    @property
    def profile(self) -> ScaleProfile:
        return profile_for(self.scale)

    def active_people_on(self, day: date) -> tuple[Person, ...]:
        return tuple(person for person in self.people if person.employed_on(day))

    def people_in(self, department: str) -> tuple[Person, ...]:
        return tuple(person for person in self.people if person.current.department == department)

    def subscriptions_for(self, account_id: str) -> tuple[Subscription, ...]:
        return tuple(sub for sub in self.subscriptions if sub.account_id == account_id)


def _weighted_choice(rng, options: tuple[str, ...] | tuple[int, ...], weights: tuple[float, ...]):
    return rng.choices(options, weights=weights, k=1)[0]


def _title_for(department: Department, level: int) -> str:
    if level == _CEO_LEVEL:
        return "Chief Executive Officer"
    if level == _VP_LEVEL:
        return f"VP, {department.name}"
    if level == 6:
        return f"Director, {department.name}"
    if level == 5:
        return f"Manager, {department.name}"
    return f"{_IC_PREFIX[level]}{department.ic_noun}"


def _salary_for(rng, department: Department, level: int, region: str) -> int:
    base = _SALARY_BY_LEVEL[level]
    adjusted = base * _REGION_SALARY_FACTOR[region] * _FUNCTION_SALARY_FACTOR[department.function]
    # ±9% individual variation, so compensation analysis has something to find
    # rather than every employee at a level earning an identical figure.
    adjusted *= rng.uniform(0.91, 1.09)
    return int(round(adjusted / 500) * 500)


def _build_people(
    seeds: Seeds, profile: ScaleProfile, start: date, end: date
) -> tuple[Person, ...]:
    """Build the employee population, with attribute history and an org chart.

    Generated in three passes because managers must exist before they can be
    referenced: executives first, then staff, then the reporting lines and the
    attribute-change history that depends on them.
    """
    # More people than the headcount target: the difference is attrition, so
    # the population contains leavers as well as current staff. A generator
    # that only produced current employees would make the HR domain unable to
    # demonstrate the one thing HR data is actually hard at — history.
    total = int(profile.headcount * 1.4)
    earliest_hire = start - timedelta(days=365 * 7)

    rng = seeds.stream("world", "people")
    used_emails: set[str] = set()
    raw: list[tuple[str, str, str, str, str, Department, int, date]] = []

    # Pass 1: the executive team. The CEO plus one VP per department, hired
    # earliest, so that every later employee has someone to report to.
    ceo_dept = DEPARTMENTS_BY_CODE["ENG"]
    exec_slots: list[tuple[Department, int]] = [(ceo_dept, _CEO_LEVEL)]
    exec_slots += [(dept, _VP_LEVEL) for dept in DEPARTMENTS]

    for index in range(total):
        employee_id = f"E{index + 1:05d}"
        person_rng = seeds.stream("world", "person", employee_id)
        first = person_rng.choice(_FIRST_NAMES)
        last = person_rng.choice(_LAST_NAMES)

        if index < len(exec_slots):
            department, level = exec_slots[index]
            region = (
                "Americas"
                if level == _CEO_LEVEL
                else _weighted_choice(person_rng, REGIONS, _REGION_WEIGHTS)
            )
            # Executives predate the reporting window, giving the org a
            # plausible tenure distribution rather than a company that
            # apparently hired its leadership last year.
            hire = earliest_hire + timedelta(days=person_rng.randrange(0, 400))
        else:
            department = _weighted_choice(
                person_rng,
                tuple(dept.code for dept in DEPARTMENTS),
                tuple(dept.headcount_share for dept in DEPARTMENTS),
            )
            department = DEPARTMENTS_BY_CODE[department]
            level = _weighted_choice(person_rng, _LEVEL_CHOICES, _LEVEL_WEIGHTS)
            region = _weighted_choice(person_rng, REGIONS, _REGION_WEIGHTS)
            span_days = (end - earliest_hire).days
            # Headcount growth accelerates over time: squaring a uniform
            # draw biases start dates towards the recent end, which is what
            # a growing company's tenure histogram looks like.
            offset = int(span_days * (1 - person_rng.random() ** 2))
            hire = earliest_hire + timedelta(days=offset)

        local = f"{first}.{last}".lower()
        email = f"{local}@halcyon.example"
        suffix = 2
        while email in used_emails:
            # Real directories disambiguate collisions rather than rejecting
            # the hire; the warehouse phase needs email to be a usable
            # natural key, so it has to be genuinely unique here.
            email = f"{local}{suffix}@halcyon.example"
            suffix += 1
        used_emails.add(email)

        raw.append((employee_id, first, last, email, region, department, level, hire))

    # Pass 2: reporting lines. Candidates are anyone in the same department at
    # a higher level who was already employed; the department VP is the
    # fallback, and the CEO reports to nobody.
    by_department: dict[str, list[tuple[str, int, date]]] = {}
    for employee_id, _first, _last, _email, _region, department, level, hire in raw:
        by_department.setdefault(department.code, []).append((employee_id, level, hire))

    vp_of: dict[str, str] = {}
    for employee_id, _f, _l, _e, _r, department, level, _h in raw:
        if level == _VP_LEVEL and department.code not in vp_of:
            vp_of[department.code] = employee_id

    people: list[Person] = []
    for employee_id, first, last, email, region, department, level, hire in raw:
        person_rng = seeds.stream("world", "career", employee_id)

        if level == _CEO_LEVEL:
            manager_id = None
        else:
            candidates = [
                other_id
                for other_id, other_level, other_hire in by_department[department.code]
                if other_level > level and other_hire <= hire and other_id != employee_id
            ]
            if candidates:
                manager_id = person_rng.choice(sorted(candidates))
            else:
                manager_id = vp_of.get(department.code, raw[0][0])
                if manager_id == employee_id:
                    manager_id = raw[0][0]

        location = person_rng.choice(LOCATIONS[region])
        employment_type = "Full-Time" if person_rng.random() > 0.06 else "Contractor"

        # Attrition: ~14% a year, applied per year of tenure. Executives are
        # exempt to keep the org chart stable.
        termination: date | None = None
        termination_reason: str | None = None
        if level < _VP_LEVEL:
            probe = hire + timedelta(days=person_rng.randrange(120, 400))
            while probe <= end:
                if person_rng.random() < 0.14:
                    termination = probe
                    termination_reason = person_rng.choices(
                        ("Voluntary", "Involuntary", "End of Contract"),
                        weights=(0.72, 0.18, 0.10),
                        k=1,
                    )[0]
                    break
                probe += timedelta(days=365)

        # Pass 3: the attribute history. Each anniversary can bring a merit
        # increase, a promotion, or a transfer; every change closes the
        # previous span and opens a new one.
        spans: list[EmploymentSpan] = []
        current_level = level
        current_department = department
        current_salary = _salary_for(person_rng, department, level, region)
        span_start = hire
        reason = "Hire"
        last_day = termination or end

        anniversary = hire
        while True:
            anniversary = anniversary + timedelta(days=365)
            if anniversary > last_day:
                break

            roll = person_rng.random()
            if roll < 0.14 and current_level < _MAX_IC_LEVEL + 2:
                new_level = current_level + 1
                new_department = current_department
                new_reason = "Promotion"
            elif roll < 0.19:
                new_level = current_level
                new_department = DEPARTMENTS_BY_CODE[
                    person_rng.choice(
                        [d.code for d in DEPARTMENTS if d.code != current_department.code]
                    )
                ]
                new_reason = "Transfer"
            elif roll < 0.72:
                new_level = current_level
                new_department = current_department
                new_reason = "Merit Increase"
            else:
                continue  # a year with no change at all

            spans.append(
                EmploymentSpan(
                    valid_from=span_start,
                    valid_to=anniversary,
                    department=current_department.code,
                    job_level=current_level,
                    title=_title_for(current_department, current_level),
                    manager_id=manager_id,
                    location=location,
                    employment_type=employment_type,
                    annual_salary_usd=current_salary,
                    change_reason=reason,
                )
            )

            if new_reason == "Promotion":
                current_salary = _salary_for(person_rng, new_department, new_level, region)
            elif new_reason == "Transfer":
                current_salary = int(
                    round(current_salary * person_rng.uniform(0.98, 1.06) / 500) * 500
                )
            else:
                current_salary = int(
                    round(current_salary * person_rng.uniform(1.02, 1.06) / 500) * 500
                )

            current_level = new_level
            current_department = new_department
            span_start = anniversary
            reason = new_reason

        spans.append(
            EmploymentSpan(
                valid_from=span_start,
                valid_to=termination,
                department=current_department.code,
                job_level=current_level,
                title=_title_for(current_department, current_level),
                manager_id=manager_id,
                location=location,
                employment_type=employment_type,
                annual_salary_usd=current_salary,
                change_reason=reason,
            )
        )

        people.append(
            Person(
                employee_id=employee_id,
                first_name=first,
                last_name=last,
                work_email=email,
                region=region,
                hire_date=hire,
                termination_date=termination,
                termination_reason=termination_reason,
                spans=tuple(spans),
            )
        )

    del rng  # population-level stream reserved; per-person streams do the work
    return tuple(people)


def _account_name(rng: Random) -> str:
    """A customer name from the fictional vocabulary.

    Three draws in a fixed order, so a retry after a collision consumes the
    same number of values from the stream as the first attempt did.
    """
    return " ".join(
        (
            rng.choice(_ACCOUNT_PREFIXES),
            rng.choice(_ACCOUNT_NOUNS),
            rng.choice(_ACCOUNT_SUFFIXES),
        )
    )


def _build_accounts(
    seeds: Seeds, profile: ScaleProfile, people: tuple[Person, ...], start: date, end: date
) -> tuple[Account, ...]:
    earliest = start - timedelta(days=365 * 5)
    # Only Sales owns accounts, and only employees who were around long enough
    # to have been given one.
    reps = sorted(
        {
            person.employee_id
            for person in people
            if any(span.department == "SLS" for span in person.spans)
        }
    )
    if not reps:  # pragma: no cover - the department shares make this impossible
        reps = [people[0].employee_id]

    accounts: list[Account] = []
    used_names: set[str] = set()
    for index in range(profile.accounts):
        account_id = f"A{index + 1:05d}"
        rng = seeds.stream("world", "account", account_id)

        name = _account_name(rng)
        attempts = 0
        while name in used_names:
            attempts += 1
            name = _account_name(rng)
            if attempts > 50:  # pragma: no cover - vocabulary is far larger than needed
                name = f"{name} ({account_id})"
        used_names.add(name)

        created_offset = int((end - earliest).days * (1 - rng.random() ** 1.6))
        created = earliest + timedelta(days=created_offset)

        churn: date | None = None
        probe = created + timedelta(days=rng.randrange(200, 500))
        while probe <= end:
            if rng.random() < 0.09:  # ~9% annual logo churn
                churn = probe
                break
            probe += timedelta(days=365)

        accounts.append(
            Account(
                account_id=account_id,
                name=name,
                region=_weighted_choice(rng, REGIONS, _REGION_WEIGHTS),
                segment=_weighted_choice(rng, SEGMENTS, _SEGMENT_WEIGHTS),
                industry=rng.choice(INDUSTRIES),
                created_date=created,
                owner_employee_id=rng.choice(reps),
                churn_date=churn,
            )
        )
    return tuple(accounts)


def _build_subscriptions(
    seeds: Seeds, accounts: tuple[Account, ...], end: date
) -> tuple[Subscription, ...]:
    subscriptions: list[Subscription] = []
    for account in accounts:
        rng = seeds.stream("world", "subscription", account.account_id)
        low, high = _SEGMENT_SEATS[account.segment]
        discount_low, discount_high = _SEGMENT_DISCOUNT[account.segment]

        for product in PRODUCTS:
            if rng.random() > _PRODUCT_ATTACH_RATE[product.code]:
                continue
            # Add-ons are bought after the initial platform purchase, not with it.
            lag = 0 if product.code == "HAL-CORE" else rng.randrange(0, 400)
            sub_start = account.created_date + timedelta(days=lag)
            if sub_start > end:
                continue
            # A customer who churned before the add-on's start date never
            # bought it. Without this the subscription would be emitted with
            # an end_date before its start_date — an impossible row, and one
            # that only appears for accounts that churn early in their life.
            if account.churn_date is not None and account.churn_date <= sub_start:
                continue

            seats = rng.randrange(low, high + 1)
            discount = rng.uniform(discount_low, discount_high)
            arr = int(round(seats * product.list_price_per_seat * (1 - discount) / 100) * 100)

            # A subscription ends when the customer churns, or on its own if
            # they drop just that product.
            sub_end = account.churn_date
            if sub_end is None and product.code != "HAL-CORE" and rng.random() < 0.11:
                dropped = sub_start + timedelta(days=rng.randrange(365, 900))
                if dropped <= end:
                    sub_end = dropped

            subscriptions.append(
                Subscription(
                    subscription_id=f"S{len(subscriptions) + 1:06d}",
                    account_id=account.account_id,
                    product_code=product.code,
                    start_date=sub_start,
                    end_date=sub_end,
                    seats=seats,
                    arr_usd=arr,
                )
            )
    return tuple(subscriptions)


def build_world(seed: int, scale: Scale, end_date: date) -> HalcyonWorld:
    """Build Halcyon deterministically from a seed.

    ``end_date`` is the "as of" date of the whole dataset — the day the
    extract was notionally taken. It is a parameter rather than
    ``date.today()`` precisely so the output does not change when the clock
    does; a generator that quietly depends on today's date is reproducible
    only until tomorrow.
    """
    seeds = Seeds(seed)
    profile = profile_for(scale)
    start_date = end_date - timedelta(days=profile.days)

    people = _build_people(seeds, profile, start_date, end_date)
    accounts = _build_accounts(seeds, profile, people, start_date, end_date)
    subscriptions = _build_subscriptions(seeds, accounts, end_date)

    return HalcyonWorld(
        scale=scale,
        seeds=seeds,
        start_date=start_date,
        end_date=end_date,
        people=people,
        accounts=accounts,
        subscriptions=subscriptions,
        people_by_id={person.employee_id: person for person in people},
        accounts_by_id={account.account_id: account for account in accounts},
    )
