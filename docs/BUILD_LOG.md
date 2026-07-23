# Build log

Newest first. One entry per merged change; what was built and what was learned.

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
