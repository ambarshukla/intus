# Build log

Newest first. One entry per merged change; what was built and what was learned.

## 2026-07-23 — Phase 2d: reporting views — closes out Phase 2

Seven views in `reporting`, completing the star schema's consumer side and the SQL
drill track: `rpt_headcount_trend`, `rpt_attrition_by_department`,
`rpt_sales_pipeline_by_rep`, `rpt_revenue_trend`, `rpt_product_usage_trend`,
`rpt_ai_cost_by_department`, `rpt_budget_variance` — one per persona named in the
target posting (HR analyst, sales ops, FP&A, exec), each built around a genuinely
different window-function technique (`LAG`, a moving-average frame, a running total,
`RANK`, an unpartitioned ratio-to-total, `PERCENT_RANK`) rather than seven variations
on the same pattern.

Views are DDL, not data, and live in a migration (`005_reporting_views.sql`) rather
than the transform layer: their SQL text is checksummed and versioned like any other
structure, and they recompute live at query time with no load step of their own.

**None of the seven expose RESTRICTED-tier data at individual grain** — no raw salary,
no per-employee rating. That's a boundary, not an omission: Phase 4 (row-level security
and column masking) is what would make an individual-level compensation report
defensible, and building one now, ahead of the machinery that protects it, would be
building the exact thing the governance phase exists to prevent. A test enumerates
every RESTRICTED column name from the generator's own classification and asserts none
of them appear in `information_schema.columns` for `reporting.*`.

**One real bug, caught only by checking the arithmetic rather than trusting a view
that ran without error.** The first version of `rpt_attrition_by_department` reported
Engineering at **4883% annual attrition**. The denominator CTE averaged a 0/1/2
indicator *per row of `dim_employee`* — which holds one row per SCD2 span, not one row
per employee — across every span regardless of whether it covered either boundary
date. Most spans cover neither, so they silently drag a per-row average toward zero
while the numerator (real termination count) stayed correct, producing a ratio that
looked like a percentage and wasn't one. Fixed by counting `DISTINCT employee_id`
separately at each boundary date and averaging the two counts — the number a human
would actually mean by "average headcount." A regression test asserts every attrition
rate stays under 100%, and a second test walks the actual `RANK()` output verifying tie
semantics rather than assuming a dense 1..N sequence, which only happens to hold when
nothing ties — true at full scale with hundreds of terminations, false at the small CI
extract, where a handful of departments make ties routine.

Also fixed mid-development, not shipped: Postgres rejects a window function fed
directly into another window function's argument ("window function calls cannot be
nested"). `rpt_revenue_trend`'s cumulative net-new ARR needed `LAG`'s result
materialised in its own CTE layer before a running `SUM()` could read it as a plain
column.

## 2026-07-23 — Phase 2c: the fact tables, and full data-quality coverage

Ten fact tables — compensation, performance reviews, subscriptions, invoices,
opportunities, daily usage, AI usage, access events, GL actuals, budgets — completing
the star schema and taking the data-quality scorecard from 4 of 19 defect types to
**19 of 19**, all at 100% recall with zero false positives, verified at both small and
full scale (66/66 seeded defects caught in the full 1.8M-row extract).

**Facts truncate-and-reload; dimensions MERGE.** The contrast with Phase 2b is
deliberate and follows directly from D-012's reasoning: nothing downstream references a
fact row's own key, so there is no surrogate-key stability to protect, and reload is
simpler and faster than the DELETE-then-MERGE dance dimensions require.

**A point-in-time lookup function, used by every fact that joins to `dim_employee`.**
`warehouse.employee_key_as_of(employee_id, date)` returns the SCD2 version in force on a
date, or NULL if none covers it — the standard fact-load join, written once rather than
six times. Its NULL case turned out to be exactly the detection mechanism the centrepiece
security rule needed: `SEC_LOGIN_AFTER_TERMINATION` fires precisely when the function
returns NULL for an event dated after someone's last version closed.

Strict point-in-time resolution is *wrong* for some facts, though. A budget's
`approved_date` is set to the prior November for every fiscal period, which is routinely
before a newer approver's first HR record — a case with nothing wrong with the data, just
a legitimate reason point-in-time lookup fails. A second function,
`employee_key_best`, falls back to the nearest known version instead of NULL. It is used
for every fact's stored `employee_key` column; the strict function stays reserved for
`dq_exception` detection queries, which need the gap-revealing NULL. The same reasoning
governs `fact_access_event`'s post-termination-login row: it resolves to the *real*
employee via `employee_key_best`, not to the sentinel "Unknown Employee" — an audit trail
whose most important finding anonymises the person it is about has defeated its own
purpose.

**Unknown members.** Every dimension gets one sentinel row at key `-1`, for a fact whose
foreign key cannot resolve to anything real. The alternative — a nullable FK — pushes the
problem onto every report ever written against the fact, where every join becomes a LEFT
JOIN and every GROUP BY needs its own COALESCE. Two dimension transforms (`dim_employee`,
`dim_account`) run a DELETE for rows the source no longer carries; both now explicitly
exclude `key = -1`, learned the hard way — the sentinel isn't in any extract by
construction, so an unqualified DELETE removed it on the very next rebuild. The
`is_current`-clearing UPDATE in `dim_employee` had the identical bug in a different
guise: cleared unconditionally, the sentinel's flag never got set back to `true` (it's
never a MERGE match), silently breaking its own "at most one current row" guarantee.

**A genuinely nasty concurrency bug, from switching a database's underlying
population.** Regenerate with a different scale or seed against an already-built
warehouse, and `dim_employee`'s reconciliation DELETE can try to remove an employee
version that a *previous* run's not-yet-truncated facts still reference — a live foreign
key violation, mid-transaction, over a state the transform was always going to correct
two steps later. The fix is `DEFERRABLE INITIALLY DEFERRED` on every fact-to-dimension
foreign key: checked at commit, not at each statement, which is what lets "delete the
parent, rebuild the child" work at all within one transaction. Reproduced deliberately —
build once, switch the loaded extract to full scale, rebuild — to confirm the fix, since
the failure mode does not show up when the same population is simply reloaded.

**`dim_date`'s range was too narrow**, discovered by the same reproduction: executives
are backdated up to seven years before the dataset's own start date
(`intus_gen.world._build_people`), so a compensation record's `effective_from` can
legitimately predate anything else in the extract. The original 2018–2030 range missed
it; widened to 2010–2035 with real margin rather than the exact computed minimum, so a
future change to the backdating window doesn't silently reopen the gap.

**A real bug in the Phase 1 generator, found only by adding a constraint the generator
never had to satisfy before.** `warehouse.dim_employee`'s SCD2 exclusion constraint
rejects any span with `valid_from == valid_to`, and one existed: when a termination date
landed exactly on a work anniversary, `world.py`'s span-building loop treated the
anniversary as an ordinary mid-career change *and* as the terminal boundary, producing a
zero-length trailing span — a role lasting zero days, which is not a data-quality
scenario worth detecting, just wrong. Fixed by changing the loop's boundary check from
`>` to `>=`. The regression test swept 20 seeds rather than relying on the shared test
fixture's one seed, because the bug depends on a coincidence (termination date = hire
date + 365×n) that most seeds simply do not produce — the fixture's default seed was one
of them, and a test using only it would have passed against the unfixed code.

**Two statistical detection rules turned out to be wrong on first measurement, both
caught by scoring against the manifest rather than by reading the SQL.**
`AI_COST_MISMATCH` was designed to flag cost values statistically distant from a
per-model average — defeated by the generator's own wide token-count variance, which
made a correct high-token request indistinguishable from a corrupted one. Replaced with
exact recomputation from a hard-coded copy of the model rate card, reconciling to within
a wide tolerance (10%, comfortably below the seeded 3×–12× corruption and comfortably
above legitimate rounding noise). `SEC_IMPOSSIBLE_TRAVEL` first scored 33% recall and 138
false positives: requiring *both* paired events to be a successful login missed two of
three seeded pairs, since the generator only guarantees that of the *fabricated* twin;
and "different country" was far too loose a signal, since `source_country` is drawn per
event from a multi-country pool *within* one region, so ordinary same-region variation
was swamping the real defect. Fixed by requiring only the later event to be a login, and
by checking region rather than raw country — a second small reference table, duplicated
into SQL the same way as the AI rate card, both kept honest against the generator by a
dedicated drift test (the D-010 pattern, now used three times).

## 2026-07-23 — Phase 2b: conformed dimensions, SCD2, and a data-quality scorecard

The star schema's dimension half, and the piece that turns Phase 1's ground truth into
a measurement.

**Transforms are not migrations.** A second runner, and a separate directory. Migrations
change *structure*, run once, and are checksummed so they can never change afterwards;
transforms change *data*, run on every load, and must be idempotent. Conflating them
gives you either migrations nobody dares re-run or transforms that silently apply twice.
The whole transform set runs in one transaction — a half-built star schema, dimensions
updated and facts not, is a state no report should be able to observe.

**MERGE rather than truncate-and-rebuild**, and the reason is not elegance. For a full
extract, rebuilding is simpler and correct — except that facts will reference
`employee_key`, so reissuing surrogate keys on every load would orphan every fact
pointing at them. There is a test asserting keys survive a rerun, because that is the
entire justification. (Postgres 16's `MERGE` has no `WHEN NOT MATCHED BY SOURCE` — that
arrived in 17 — so removals are a separate `DELETE` that must run first.)

**`dim_employee` is type 2, `dim_account` is type 1**, and the contrast is deliberate.
The choice is made by what the source can evidence, not by which is more sophisticated:
the HR extract is effective-dated, the CRM extract carries only current state. Modelling
accounts as type 2 would manufacture versions the source cannot substantiate — the
dimension would claim to know when an account changed segment, and it does not.

The SCD2 invariant is enforced by the database, not trusted from the transform: a GiST
exclusion constraint on `(employee_id, daterange(valid_from, valid_to))` makes two
versions covering the same day unrepresentable. It should never fire, which is exactly
why it is worth having — a point-in-time join returning two rows silently double-counts
rather than failing. `is_current` means *latest version*, which is not the same as
"still employed"; a leaver's final version is current and closed, so it gets a partial
unique index rather than a check against `valid_to`.

**Three dispositions, not one.** `warehouse.dq_exception` records whether a row was
`rejected`, `repaired`, or `flagged`, and choosing between them is the actual data-
quality design work. An overlapping span is rejected — the table cannot hold it. An
employee whose manager is missing from the extract is *repaired*: the pointer is nulled
and the row kept, because dropping a person to fix a pointer would lose them from
headcount. A missing termination reason is merely flagged: the warehouse can see the
gap but has no business inventing what belongs there. A layer that only knows how to
reject silently loses data.

Rejecting a span leaves a **gap** in that employee's history, and that is intended. The
alternative — stretching a neighbouring span to close it — would invent effective dates
the source does not support, so a point-in-time query would return a confidently wrong
answer instead of no answer. A gap is visible; a fabricated span is not.

**The scorecard.** `intus-wh dq-score` joins detections against the generator's manifest
and reports recall *and* false positives per rule. Both, because recall alone is
meaningless: a rule that rejects every row scores 100%. All four implemented rules score
100% recall with zero false positives; the other fourteen defect types report "not
implemented" rather than zero, so a partially built warehouse states its coverage
honestly. CI runs it with `--strict`, so a regression in a rule fails the build instead
of quietly lowering a number nobody reads.

**A bug in Phase 1, found only by trying to use it.** `HR_OVERLAPPING_SPAN` recorded the
*pre-corruption* `valid_from` as its target key — but that defect works by changing
`valid_from`, so the manifest named a row that no longer existed in the delivered data.
The one defect most worth detecting was the one impossible to score. Every Phase 1 test
had checked that keys were non-empty; none had checked that they *resolve*. Fixed, with
a general test asserting every manifest key matches a real row — verified to fail
against the old code.

## 2026-07-23 — Phase 2a: the legacy warehouse — Postgres, migrations, staging load

The "before" system in the modernization story. Postgres 16 in Docker (port 5433, not
5432 — a sibling project on the same machine already binds the default, and connecting
successfully to the *wrong* database is a worse failure than not connecting at all),
plus a second workspace member `warehouse/` holding `intus_warehouse`.

Three schemas with three different contracts: `staging` (landed extracts, every column
text, no constraints, truncate-and-reload), `warehouse` (the conformed star schema —
next PR), `reporting` (views only). Mixing them means none of them has a contract.

**Staging is untyped on purpose.** The extracts contain deliberate defects. If staging
were typed, COPY would fail on the first bad row and one malformed field would sink a
million-row load with a message pointing at a byte offset. Landing everything as text
moves rejection from the *load* to the *transform*, where a row can be rejected with a
business reason and — the part that matters — recorded rather than failing the batch.
Staging also has no primary keys: two seeded defects are duplicate rows, and the goal
is to detect and report them, not to make them unrepresentable.

**COPY, not INSERT.** 1.8M rows land in 3.2 seconds. Row-by-row inserts would take
minutes and a million round trips, and COPY is what a real warehouse load uses anyway.

**Provenance is checked, not assumed.** Each file's SHA-256 comes from the generator's
manifest and is verified before loading; a file that disagrees with its manifest is
refused. `staging.load_audit` records the hash, seed, scale and as-of date per load, so
"which extract is in staging right now?" has an answer that survives the next reload.

**The migration runner** is ~100 lines standing in for Flyway: ordered `NNN_name.sql`
files, recorded in `public.schema_migration`, checksummed so editing an applied
migration is an error rather than silent divergence, and forward-only. Checksums
normalise line endings first, or a CRLF checkout would report tampering that never
happened.

**Hand-written DDL, drift caught by test.** Generating staging DDL from the Phase 1
`Dataset` registry would make drift impossible and would also hide the SQL, which is
the artefact this phase exists to show. Instead the DDL is written by hand and a test
compares the live `information_schema` against the registry — same safety, real SQL in
the repo.

The tests caught one bug twice, in two different modules, and it is worth writing down.
On a non-autocommit psycopg connection, `connection.transaction()` opens a **savepoint**
inside the surrounding transaction rather than a transaction of its own. So "each
migration commits independently" was false — everything was one uncommitted unit, and a
failure in the last migration would roll back all the earlier ones. The property was
exactly backwards: the whole *run* was atomic, which is precisely what transactional DDL
is supposed to save you from. The loader had the identical bug: a successful load was
never made durable. Both now commit explicitly, and both tests assert the property
rather than the mechanism.

## 2026-07-23 — Phase 1: the Halcyon synthetic data generators

First code in the repo, and with it the whole Python toolchain: a uv workspace
(one `uv.lock` for every member), a shared `ruff.toml` set strict from commit one
(`E,F,W,I,UP,B,SIM,RUF`, line length 100), a `Makefile`, and a GitHub Actions job
running format check, lint, tests, an end-to-end generation smoke test, and a
check that the generated data catalog is not stale.

`gen/` holds `intus_gen`: deterministic generators for six internal data domains —
HR (employee history, compensation, performance), CRM (accounts, subscriptions,
pipeline, invoices), product telemetry, internal AI usage and cost, finance
(budgets and actuals), and systems access logs. Twelve datasets, ~1.8M rows at
full scale in about ninety seconds, written as UTF-8 CSV with a run manifest.

Four things are worth calling out, because they were the design, not the coding:

**The world is built before any domain.** Employees, org, accounts, products and
subscriptions are generated once and handed to every domain generator. Domains
that cannot join are not a dataset, they are six unrelated files — and the
warehouse phase needs the same `employee_id` to appear in HR records, AI-usage
logs, security logs and as the owner of a CRM account.

**Seeding is per-stream, not global.** Every draw comes from
`sha256(run_seed : stream : entity)`. The obvious alternative — one `Random(seed)`
threaded through every generator — makes the dataset order-dependent, so
inserting a generator anywhere upstream silently rewrites everything downstream.
That reduces "regenerable byte-for-byte" to a claim that holds only while nobody
edits the code.

**Sensitivity tiers are declared beside the schema and enforced at import.** A
`Dataset` validates that its declared columns match its record type's fields
exactly, in order. A column added without a classification is an ImportError, not
a review comment — and `docs/data-catalog.md` is generated from those
declarations rather than maintained by hand.

**Defects carry ground truth.** Nineteen deliberate corruptions, each recorded in
the manifest with the key it landed on. The tests do not assert that injection
*ran*; they re-derive each defect from the data and check the manifest describes
what actually happened. A defect that silently no-opped while still reporting
itself would make every future detection metric a lie and would look exactly like
a passing suite.

Two bugs the tests caught that review had not. A subscription could be emitted
with `end_date` before `start_date`, for customers who churned before buying an
add-on — visible only for accounts that churn early in their life. And the
property test for stream independence was itself wrong: it built a fresh stream
per iteration, so it compared the first draw against itself five times and would
have passed against a completely broken implementation.

Money is `Decimal`, never `float`. Binary floating point cannot represent most
decimal fractions exactly, and the error surfaces precisely where it is least
welcome — a budget variance off by a cent, or a legacy-versus-lakehouse parity
check that fails on rounding rather than on logic.

## 2026-07-23 — Repo hygiene: line endings and editor settings

Added `.gitattributes` (`* text=auto eol=lf`, plus binary markers for png/pdf/pbix) and
`.editorconfig` before any code exists, deliberately. Both are cheap now and annoying
later: normalising line endings after files are committed produces a churn commit that
touches every line of every file, and the Power BI artifact extension (`.pbix`) needs
the binary marker in place *before* the first one is added, or Git will try to
line-ending-normalise a binary file and corrupt it.

## 2026-07-23 — Scaffold

Repo created: README (project thesis: internal data + governance-first + legacy-SQL →
Databricks modernization), docs skeleton (this file, DECISIONS, GLOSSARY), .gitignore.
No code yet. First real phase: the synthetic data generators for Halcyon's internal
domains, with sensitivity tiers labeled at generation time.
