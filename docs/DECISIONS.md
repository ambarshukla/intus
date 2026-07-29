# Decisions

A running log of real design choices — what was decided, what the alternatives were,
and why. Numbered D-001 onward, append-only.

## D-001 — Project scope: internal data + governance, framed as a modernization (2026-07-23)

**Decision.** Build an internal enterprise lakehouse for a fictional company
(Halcyon), with three deliberate emphases that its sibling project `parvum` does not
cover: (1) internal/corporate data domains (HR, sales, finance, product usage, AI
usage, systems), (2) fine-grained access control and SOX-style audit evidence as a
first-class design driver, and (3) an explicit legacy-SQL-warehouse → Databricks
migration arc, including parity testing between the two.

**Alternatives considered.** (a) Extending `parvum` with a governance phase — rejected
because parvum's domain (client portfolio data served to advisory firms) doesn't
naturally exercise internal-data governance personas, and mixing narratives weakens
both. (b) A full second parvum-scale platform — rejected as low marginal learning;
everything parvum already proves (ingestion, medallion, orchestration, serving) is
reused knowledge here, not the point.

**Consequences.** The project stays small and pointed: the legacy warehouse and the
governance layer get the depth; ingestion mechanics stay simple. Sensitivity labels
are assigned at data-generation time so every access-control claim can be tested
against ground truth.

## D-002 — Two-repo split, same discipline as parvum (2026-07-23)

Public repo (`intus`) carries technical content only and is self-contained; working
notes live in a separate private repo. Same rationale as parvum: the public repo is a
reference project, not a diary.

## D-003 — Determinism via per-stream SHA-256 seeds (2026-07-23)

**Decision.** Every random draw derives from `sha256(run_seed : stream_name :
entity_key)`, where the stream name is a tuple of labels identifying what is being
generated. Parts are joined with the ASCII unit separator (`\x1f`) so that
`("ab", "c")` and `("a", "bc")` cannot collide.

**Alternatives considered.** (a) Python's built-in `hash()` — rejected outright: it is
salted per process for `str` and `bytes`, so the generator would produce different data
on every run, a bug that hides on the machine that wrote it and appears on a reviewer's.
(b) A single `Random(seed)` threaded through every generator in sequence — rejected
because it makes the entire dataset order-dependent. Adding a domain, or generating one
extra employee, shifts every subsequent draw, so "regenerable byte-for-byte" would hold
only until the next code change. (c) Truncating the digest to 8 bytes — rejected as a
false economy; `Random` accepts arbitrary-width integers, so the full 256 bits are free
and put stream collisions out of reach at any dataset size.

**Consequences.** Streams are independent: a new domain does not perturb existing
output. Any single entity can be regenerated in isolation for debugging, without
replaying the run that produced it. A pinned digest in the test suite means a future
"harmless" change to the separator or digest width cannot pass silently — if it ever
fails, every previously generated dataset has become unreproducible.

## D-004 — Sensitivity declared beside the schema, enforced at import (2026-07-23)

**Decision.** Each dataset declares its columns with a sensitivity tier (public /
internal / confidential / restricted) in the same module as the record type. A
`Dataset` validates on construction that its declared columns match the record's
dataclass fields exactly, including order, and that the primary key names real columns.
`docs/data-catalog.md` is generated from these declarations, and CI fails if the
committed copy is stale.

**Alternatives considered.** (a) Per-row sensitivity tags — rejected as modelling
something no real catalog does; classification is a property of a column, and per-row
tags would be a mechanism no downstream engine (Unity Catalog, Postgres RLS, a BI
semantic layer) can consume. (b) Documenting tiers in prose — rejected because free
text cannot be asserted against, so every access-control test would restate the
classification and the restatement is what would drift. (c) Validating in a test rather
than at import — kept as well, but the constructor check is stricter: a column added
without a tier fails the moment the module loads, so it cannot reach the warehouse
unclassified.

**Consequences.** The governance phase enumerates what it must protect by reading the
catalog rather than by restating it. Column *order* becomes part of the contract, which
is what keeps the CSV header from disagreeing with the rows beneath it.

## D-005 — One shared world, so the domains actually join (2026-07-23)

**Decision.** Employees, org structure, customer accounts, products and subscriptions
are generated once into a `HalcyonWorld` and handed to every domain generator. Salary
*actuals* in the finance domain are derived from the HR population rather than invented
independently.

**Alternatives considered.** Letting each domain generate its own keys — rejected
because the result is six unrelated files rather than a dataset. The warehouse phase
needs the same `employee_id` in HR records, AI-usage logs, security logs and as a CRM
account owner; the governance phase needs a salesperson's region to be the same fact
everywhere, because that is what a row-level-security predicate filters on.

**Consequences.** Cross-domain referential integrity is testable, and is tested. The
derived salary expense gives the warehouse phase a genuine reconciliation to build
(payroll expense should tie to headcount times compensation) and gives the governance
phase its most interesting case: that reconciliation crosses a classification boundary,
because the inputs are `restricted` and the output is ordinary management reporting.
Generating the two independently would have designed that problem away.

## D-006 — Deliberate defects with a ground-truth manifest (2026-07-23)

**Decision.** Generators build clean data first; a separate injection pass then corrupts
a known subset, appending every corruption to a manifest recording the defect, dataset,
key and a before/after description. Defect definitions live with the domain that
understands them, not in a central switch.

**Alternatives considered.** (a) Generating defects inline — rejected because the clean
dataset then never exists, so there is nothing to diff against and no way to produce a
defect-free run for comparison (`--no-defects` gives one). (b) A generic corrupter
applied uniformly — rejected because it can only manage the uninteresting defects,
nulls and type errors, which no realistic pipeline struggles with. The defects that
matter are semantic: a login after termination needs to know what a termination is.

**Consequences.** A data-quality framework can be *scored* — "did we catch all seeded
defects?" — rather than merely run. The tests verify the manifest against the data
rather than trusting it, because a defect that silently no-opped while still reporting
itself would corrupt every future detection metric and would look like a passing suite.

## D-007 — Output contract: CSV, LF, Decimal money, and a manifest with no clock (2026-07-23)

**Decision.** UTF-8 CSV with LF line endings, ISO-8601 dates, empty string for NULL, and
monetary columns as `Decimal`. A `manifest.json` records the inputs, every file's
SHA-256 and row count, the classification summary, and the defect ground truth — and
contains no wall-clock time or machine details.

**Alternatives considered.** (a) Parquet — deferred to the lakehouse phase; the legacy
warehouse is the first consumer and a flat delimited extract is what a legacy warehouse
is actually fed. (b) Default `csv.writer` line endings (`\r\n`) — rejected because the
same generator would then produce a different SHA-256 on Windows and on CI, and a hash
that only matches on the machine that wrote it verifies nothing. (c) `float` for money —
rejected; binary floating point cannot represent most decimal fractions exactly, and
Postgres `numeric` and Spark `decimal` both preserve what `double` loses. (d) Stamping
the manifest with `generated_at` and the Python version, as most build metadata does —
rejected because it would make two otherwise identical runs differ, costing the property
that makes the manifest useful: reproducibility can be checked by comparing manifests
instead of re-reading gigabytes of CSV.

**Consequences.** Regenerability is verifiable rather than asserted. Generated data is
gitignored, because a deterministic generator plus a committed manifest is a better
record than 400 MB of committed CSV.

## D-008 — A hand-rolled migration runner, not Flyway or Alembic (2026-07-23)

**Decision.** Migrations are `NNN_name.sql` files applied in order by ~100 lines of
Python, recorded in `public.schema_migration` with a SHA-256 checksum, each in its own
committed transaction, forward-only.

**Alternatives considered.** (a) Flyway — the obvious choice and a better tool, rejected
because it needs a JVM (not normally on PATH on this machine) and because it would add a
configuration vocabulary without changing a line of the SQL that this phase exists to
demonstrate. (b) Alembic — designed around SQLAlchemy models and autogeneration, which
is the opposite of what a hand-written star schema wants. (c) Down-migrations — rejected
as reassuring but rarely correct: the interesting failures involve data, which a schema
rollback cannot restore. Rolling forward with a new migration is the honest fix.

**Consequences.** Editing an applied migration is an error rather than silent
divergence. Checksums normalise line endings before hashing, or a CRLF checkout would
report tampering that never happened. The runner commits after each migration — without
that, on a non-autocommit connection `connection.transaction()` opens a savepoint rather
than a transaction, and the whole run becomes atomic instead of each migration, which
inverts the property transactional DDL exists to give.

## D-009 — Staging is untyped, unconstrained, and truncate-and-reload (2026-07-23)

**Decision.** Every staging column is `text`; no primary keys, foreign keys or checks.
Each load truncates and reloads, in one transaction, via `COPY`.

**Alternatives considered.** (a) Typed staging columns — rejected because the extracts
deliberately contain malformed data, so COPY would fail on the first bad row and one
field would sink a million-row load with an error pointing at a byte offset rather than
at a business problem. Untyped staging moves rejection to the transform, where a row can
be rejected *with a reason* and recorded. (b) A primary key on staging — rejected
because two of the seeded defects are duplicate rows; a key would reject them at the
door, when the goal is to detect and report them. (c) Incremental/append loading —
rejected because staging holds the current extract, not history; the warehouse layer is
where history lives, and wholesale reload makes a rerun idempotent by construction.
(d) `INSERT` instead of `COPY` — rejected on measurement: 1.8M rows load in 3.2 seconds
via COPY.

**Consequences.** A load either fully replaces the previous extract or leaves it
untouched. Provenance is verified rather than trusted: each file's SHA-256 is checked
against the generator manifest before loading, and recorded with the seed and as-of date
in `staging.load_audit`, which accumulates across loads even though staging itself does
not.

## D-010 — Hand-written staging DDL, with drift caught by a test (2026-07-23)

**Decision.** The staging DDL is written by hand and kept in step with the generators by
a test that compares the live `information_schema` against Phase 1's `Dataset` registry.

**Alternatives considered.** Generating the DDL from the registry — genuinely tempting,
since it would make drift *impossible* rather than merely detected. Rejected because
this phase's purpose is to be a credible legacy warehouse and the SQL brush-up vehicle;
generated DDL would hide the artefact the phase exists to produce. A reviewer should be
able to read the schema as SQL.

**Consequences.** Column names and order in staging must mirror the generator schemas
exactly, which is what lets `COPY` work without a column list. That correspondence is
asserted rather than commented, because "mirrors the generator exactly" is the kind of
claim that is true when written and false six months later.

## D-011 — Transforms are separate from migrations (2026-07-23)

**Decision.** Two runners and two directories. `warehouse/sql/` holds versioned,
checksummed, run-once DDL. `warehouse/transform/` holds idempotent DML re-run on every
load by `intus-wh build`, executed as one transaction and recorded in
`warehouse.transform_run`.

**Alternatives considered.** (a) Populating dimensions from within migrations — rejected
because a migration is applied once and never re-run, so the warehouse could only ever
be built from the extract that happened to be in staging the day it was applied.
(b) One runner with a flag distinguishing the two — rejected because the properties are
genuinely different (immutable vs. repeatable, checksummed vs. not), and a shared runner
would have to disable half its own guarantees depending on the flag.

**Consequences.** Transforms must be idempotent, which is asserted rather than assumed.
The run id is published to the SQL through a session setting (`SET LOCAL intus.run_id`)
rather than string interpolation, keeping the transform files parameter-free and
ensuring no code path splices a value into SQL text.

## D-012 — MERGE, not truncate-and-rebuild, so surrogate keys survive (2026-07-23)

**Decision.** Every dimension is reconciled with `MERGE`, plus a separate `DELETE` for
rows the source no longer carries.

**Alternatives considered.** Truncate-and-rebuild — simpler, obviously correct for a
full extract, and rejected for one concrete reason: facts reference `employee_key`, and
rebuilding reissues every surrogate key, orphaning every fact that pointed at them. This
is the whole justification, so there is a test asserting keys are stable across a rerun.
Postgres 16's `MERGE` lacks `WHEN NOT MATCHED BY SOURCE` (added in 17), hence the
separate `DELETE`, which must run *before* the merge or a row being removed can collide
with a row being inserted under the no-overlap constraint.

**Consequences.** `is_current` is cleared in a separate statement before being
recomputed: the partial unique index is checked as each row is written, so flipping the
flag from an old version to a new one inside a single `MERGE` can transiently violate it
depending on row order.

## D-013 — Type 2 for employees, type 1 for accounts (2026-07-23)

**Decision.** `dim_employee` keeps full history with effective-dated versions; a GiST
exclusion constraint makes overlapping versions unrepresentable. `dim_account` holds
current state only.

**Alternatives considered.** Making both type 2 for consistency — rejected because the
CRM extract carries no history, so type 2 would manufacture versions the source cannot
substantiate: the dimension would claim to know *when* an account changed segment, and
it does not. The dimension type is a statement about what the source can evidence.

**Consequences.** Rejecting a corrupt span leaves a gap in an employee's history rather
than closing it by stretching a neighbour, which would invent effective dates and make a
point-in-time query return a confidently wrong answer instead of no answer.

## D-014 — Three dispositions for data-quality exceptions, and a scored manifest (2026-07-23)

**Decision.** `warehouse.dq_exception` records `rejected`, `repaired`, or `flagged`
alongside a severity, and `intus-wh dq-score` compares detections against Phase 1's
defect manifest, reporting recall *and* false positives per rule.

**Alternatives considered.** (a) A single "rejects" table — rejected because not every
problem justifies discarding the row; an employee whose manager is absent from the
extract is still an employee, and dropping them to fix a pointer loses a person from
headcount. (b) Reporting recall alone — rejected because a rule that rejects every row
scores perfect recall; false positives are what make the number mean anything. (c)
Reporting unimplemented rules as 0% — rejected as misleading: a partially built
warehouse should state its coverage, not look broken.

**Consequences.** The generator's `target_key` format and the transform's must agree
exactly; nothing else in either codebase would notice if they drifted, so a test checks
them against each other. CI runs the scorecard with `--strict`, making a regression in a
rule a build failure rather than a quietly lowered number.

## D-015 — Facts truncate-and-reload; only dimensions MERGE (2026-07-23)

**Decision.** Every fact transform truncates its table and reloads from staging on each
run, in contrast to D-012's MERGE for dimensions.

**Alternatives considered.** Using the same MERGE pattern everywhere for consistency —
rejected because the reason dimensions need it does not apply to facts: nothing
downstream references a fact row's own key (`compensation_id`, `event_id`, ...) as a
foreign key, so there is no surrogate-key stability to protect. MERGE's DELETE-then-merge
dance exists solely to serve that requirement; applying it where the requirement does not
hold would be needless complexity with no correctness benefit.

**Consequences.** Fact loads are simpler and — for the largest table, `fact_usage_daily`
at roughly a million rows — meaningfully faster than an equivalent MERGE would be.

## D-016 — Two employee-key lookup functions: strict and forgiving (2026-07-23)

**Decision.** `warehouse.employee_key_as_of(employee_id, date)` returns the SCD2 version
in force on a date, or NULL if none covers it. `warehouse.employee_key_best` wraps it,
falling back to the employee's nearest known version instead of NULL. Every
`dq_exception` detection query uses the strict function; every fact's stored
`employee_key` column uses the forgiving one.

**Alternatives considered.** One function only, always forgiving — rejected because the
strict NULL is not incidental, it is the detection mechanism: `SEC_LOGIN_AFTER_TERMINATION`
fires exactly when strict resolution fails for a date after termination. One function
only, always strict — rejected because it is wrong whenever an event's date legitimately
predates the person's earliest known version for reasons that have nothing to do with
data quality (a budget's `approved_date` is set to the prior November for every fiscal
period, routinely before a newer approver's first HR record), and it would resolve the
centrepiece rule's own fact row to the unknown member — an audit trail whose most
important finding anonymises the person it is about.

**Consequences.** A fact's stored `employee_key` and its associated `dq_exception` (if
any) can legitimately point at different realities: the fact shows the best-known
identity, the exception records that strict resolution failed. Both are correct,
answering different questions.

## D-017 — Unknown members at key -1, with an ordering hazard to remember (2026-07-23)

**Decision.** Every dimension carries one sentinel row at surrogate key `-1`, inserted by
migration, for a fact whose foreign key cannot resolve to anything real.

**Alternatives considered.** A nullable foreign key — rejected because it pushes the
problem onto every downstream query: every join becomes a LEFT JOIN, every aggregate
needs its own COALESCE or silently drops the row from a GROUP BY. An unknown member
absorbs that once, here, instead of in every report.

**Consequences, learned by breaking it twice.** The sentinel is never a match in any
dimension transform's source data, by construction — which means any statement that
operates on "rows the source no longer carries" or "rows currently flagged current"
without an explicit `key <> -1` exclusion will remove or corrupt it on the very next run.
Both `dim_employee`'s reconciliation DELETE and its `is_current`-clearing UPDATE had this
bug before a test caught it.

## D-018 — Deferred foreign keys on every fact-to-dimension reference (2026-07-23)

**Decision.** Every fact table's foreign keys are declared `DEFERRABLE INITIALLY
DEFERRED`.

**Alternatives considered.** Immediate (the default) constraint checking — this is what
shipped first, and it failed a real run: regenerating against a different scale or seed
and rebuilding without a fresh database raised `ForeignKeyViolation` when
`dim_employee`'s reconciliation DELETE removed a version that a *previous* run's
not-yet-truncated fact rows still referenced. The violation was real only for an instant
— by the time the transaction committed, the referencing facts would have been truncated
and reloaded against the current dimensions — but an immediate constraint has no way to
know that. Reordering the transform steps (all facts before any dimension delete) was
considered and rejected: it would only relocate the same problem, since some fact surely
depends on a dimension attribute a later dimension step still needs to fix.

**Consequences.** Referential integrity is checked once, at commit, across the whole
transform run rather than after each statement. Reproduced deliberately with a clean
build, a full population swap, and a rebuild in place, to confirm the fix — the failure
does not appear when the same population is simply reloaded, which is what made it easy
to miss initially.

## D-019 — Duplicating small reference data into SQL, checked against the generator by test (2026-07-23)

**Decision.** `AI_COST_MISMATCH` embeds a copy of the AI model rate card; `SEC_IMPOSSIBLE_TRAVEL`
embeds a copy of the country-to-region mapping. Both are literal copies of constants that
also exist in `intus_gen`, kept honest by a dedicated test parsing the SQL and comparing
against the Python source.

**Alternatives considered, and why the obvious ones failed on contact with real
scoring.** Detecting `AI_COST_MISMATCH` statistically (distance from a per-model average
cost) — tried first, and it does not work: token counts are drawn from wide
distributions, so a legitimately long request's cost can sit as far from the mean as a
genuinely corrupted one, and no threshold separates them reliably. Detecting
`SEC_IMPOSSIBLE_TRAVEL` by "different country" alone — also tried first, and produced 138
false positives against 3 seeded defects, because `source_country` is drawn per event
from a multi-country pool *within* one region, so ordinary same-region variation looked
identical to the signal. Generating both reference tables from `intus_gen` at build time
— rejected as more machinery than a rate card or a country list that changes on the order
of once a quarter deserves; this is the same tradeoff D-010 already made for staging DDL.

**Consequences.** The two copies can drift, and nothing except the dedicated test would
notice — which is exactly D-010's bargain, made twice more.

## D-020 — Reporting views live in a migration, one persona-mapped view per window-function technique (2026-07-23)

**Decision.** Seven views under `reporting.*`, defined in migration `005_reporting_views.sql`.
Each maps to a persona named in the target posting and demonstrates a different
window-function technique rather than repeating one pattern seven times.

**Alternatives considered.** (a) Materialized views or summary tables — rejected because
`reporting` is deliberately views-only (enforced by test); a report that could disagree
with the facts underneath it because a refresh was missed is exactly what this schema
split exists to prevent. (b) Placing view definitions in the transform layer — rejected
for the same reason dimension/transform DDL is separated in general: a view's SQL text
is structure, checksummed and versioned like any other DDL, and has no data-manipulation
step of its own to belong in a transform.

**Consequences.** None of the seven views expose RESTRICTED-tier data at individual
grain; that boundary is asserted by a test enumerating restricted column names from the
generator's own classification. Compensation and performance-rating reporting at
individual grain is left for Phase 4, where row-level security and column masking would
make it defensible — building it now, ahead of that machinery, would be the exact thing
the governance phase exists to prevent.

## D-021 — Counting distinct entities, not averaging per-row indicators (2026-07-23)

**Decision.** Rate-style metrics computed from `dim_employee` (headcount, attrition)
must count `DISTINCT employee_id` at each point in time, in its own CTE, rather than
aggregating an indicator expression directly over `dim_employee`'s rows.

**Alternatives considered.** Averaging a per-row 0/1/2 "covers this boundary date"
indicator directly — this is what shipped first, in `rpt_attrition_by_department`, and
it reported 4883% attrition for Engineering. `dim_employee` holds one row per SCD2 span,
not one row per employee, so an aggregate over its rows is implicitly weighted by how
many spans each employee happens to have — an employee with six spans and an employee
with one contribute unequally to what should be a simple headcount, and most spans cover
neither boundary date at all, diluting the result toward zero while the numerator stayed
correct.

**Consequences.** Any future metric derived from `dim_employee` must count distinct
employees explicitly rather than aggregating the table's own row count, which is a span
count, not a person count. `test_attrition_rate_is_a_plausible_percentage` guards the
specific regression; `test_headcount_matches_a_direct_point_in_time_count` cross-checks
`rpt_headcount_trend` against the same distinct-count logic written out independently.

## D-022 — Lakehouse layers as SQL-file bundle tasks, not notebooks or PySpark (2026-07-23)

**Decision.** Bronze, and later silver and gold, are `.sql` files run by Databricks
Asset Bundle `sql_task.file` jobs with `source: GIT` — the same `git_source` mechanism
parvum's `notebook_task`s use, pointed at a SQL file instead of a notebook.

**Alternatives considered.** (a) PySpark notebooks, parvum's pattern — rejected because
the thing this phase exists to demonstrate is a legacy *SQL* warehouse migrating to a
new platform; rewriting the transform logic into a different language would trade the
one comparison that matters (the same query, two SQL dialects) for a rewrite that
obscures it. (b) Databricks SQL notebooks (`.sql` extension, run as `notebook_task`) —
functionally close, but `sql_task.file` is the more direct match for "run this SQL file
against this warehouse," with no notebook-specific ceremony (cell markers, `%sql`
magics) the SQL doesn't need.

**Consequences.** Every lakehouse transform is plain SQL, reviewable by anyone who read
the Postgres migrations, and the dialect differences (below) become the visible content
of the migration story rather than something buried in a rewrite.

## D-023 — Delta table history replaces `staging.load_audit`; no hand-rolled provenance table (2026-07-23)

**Decision.** Bronze does not carry an equivalent of `staging.load_audit`. Provenance
(what loaded, when, how many rows) comes from `DESCRIBE HISTORY intus.bronze.<table>`,
which Delta records for every `CREATE OR REPLACE TABLE` automatically.

**Alternatives considered.** A bronze `load_audit` table mirroring the Postgres one,
populated by a second statement after each `CREATE OR REPLACE TABLE` — rejected because
the platform already tracks exactly this (operation, timestamp, row counts, per
version) with no extra code, and a hand-rolled table doing the same job would be a
second provenance mechanism that could itself drift from what actually happened.

**Consequences.** A parity check that needs "what was in bronze at load time N" reads
Delta's own history rather than a bespoke audit table — one more small way the two
platforms' idiomatic answers to the same problem differ, which is itself part of what
this migration is meant to show.

## D-024 — dim_employee's no-overlap rule moves entirely into the transform (2026-07-23)

**Decision.** `intus.silver.dim_employee`'s SCD2 no-overlap invariant — no two spans for
one employee may cover the same day — is enforced only by the self-join in
`21_silver_dimensions.sql` (HR_OVERLAPPING_SPAN, ported near-verbatim from the Postgres
original) and the `ROW_NUMBER()` that computes `is_current`. Neither is backed by a
database-level constraint on this platform.

**Alternatives considered.** Tested live, before deciding anything: (a) a Delta CHECK
constraint expressing the rule directly — rejected by the platform outright
(`DELTA_UNSUPPORTED_EXPRESSION_CHECK_CONSTRAINT`, "`exists()` cannot be used in a CHECK
constraint"), because Delta CHECK constraints may only reference the row being written,
never another row. (b) A declared `PRIMARY KEY` / partial-uniqueness constraint as a
backstop — also tested live: declared one, inserted a duplicate key anyway, it went in
without complaint. Unity Catalog's PRIMARY KEY and FOREIGN KEY constraints are metadata
for the query optimiser and BI tools, not enforcement, on this platform today. (c) Delta
Live Tables, which has richer validation ("expectations") — rejected as disproportionate:
it is a different compute product from the plain SQL-file bundle tasks D-022 already
chose, and adopting it to backstop one invariant would reopen that decision for a single
rule's sake. (d) A second automated check that scans the finished table for violations
and fails the build if any exist — the stronger option, and the one to revisit if this
table's write path ever gains a second writer; not built now because the write path is a
single sequential job task and the self-join already prevents the condition from ever
being written, so a second check would currently only ever prove nothing was wrong (see
Consequences).

**Consequences.** The guarantee is exactly as strong as the transform's own logic and no
stronger — there is no independent backstop catching a future bug in that logic the way
Postgres's `EXCLUDE USING gist` constraint would. This is a real, load-bearing gap
between the two platforms' guarantees for the same table, not a detail to gloss over; the
mitigating fact is that `21_silver_dimensions.sql` is the only writer of this table, so
"the query that writes it is also the query that must get this right" is a smaller
surface than it would be with multiple writers. If a second writer is ever added, revisit
alternative (d).

## D-025 — dq_exception drops run_id / transform_run bookkeeping (2026-07-23)

**Decision.** `intus.silver.dq_exception` has no `run_id` column and no counterpart to
`warehouse.transform_run`. It is truncated and rebuilt at the start of every run (first
statement of `21_silver_dimensions.sql`), the same truncate-and-reload shape every fact
table already uses.

**Alternatives considered.** Porting `transform_run` (an identity-keyed bookkeeping
table, one row per execution) and a `run_id` column on every `dq_exception` row, set from
a Databricks SQL session variable (`DECLARE VARIABLE` / `SET VARIABLE`, confirmed live to
work within one script session) — rejected for the same reason `staging.load_audit` was
dropped in bronze (D-023): the platform already records what a hand-rolled run-tracking
table exists to record. `DESCRIBE HISTORY intus.silver.dq_exception` gives every past
version of this table, with a timestamp, so "what did run N find" has an answer without a
second bookkeeping mechanism that could itself drift from what actually happened.

**Consequences.** A cross-run comparison (`did this rule's count change since last week?`)
reads Delta table history at a specific version rather than filtering a `run_id` column —
one more small, deliberate divergence from the Postgres schema, alongside D-024, both
driven by the same underlying fact: guarantees Postgres gets from the database itself
have to be re-derived from what Delta actually provides, not assumed to transfer across
unchanged.

## D-026 — Gold ports the seven reporting views verbatim where possible, with two dialect failures found only by running them (2026-07-25)

**Decision.** `lakehouse/sql/30_gold_views.sql` ports every `reporting.*` view to
`intus.gold.*`, same names, same persona mapping, same window-function technique per
view (D-020). Most of the port is line-for-line; two views needed a genuine rewrite,
discovered by executing every statement live against the workspace, not by reading the
SQL and guessing.

**Alternatives considered, and why they failed on contact with the real optimiser.**
(a) `rpt_revenue_trend`'s correlated scalar subqueries
(`(SELECT date_key FROM dim_date WHERE full_date = month_ends.month_end)`) ported
unchanged first — rejected by the platform outright
(`UNSUPPORTED_SUBQUERY_EXPRESSION_CATEGORY.MUST_AGGREGATE_CORRELATED_SCALAR_SUBQUERY`).
Postgres trusts the planner to prove at runtime that at most one row matches; Databricks'
optimiser requires syntactic proof — an aggregate — and a bare equality predicate does
not qualify even though `full_date` is genuinely unique. Fixed by joining `dim_date` a
second time to resolve `month_end`'s own `date_key`, sidestepping the restriction rather
than working around it with a pointless `MIN()`. (b) `dim_employee`'s and
`dim_account`'s MERGE change-detection, `(target.col1, target.col2, ...) IS DISTINCT
FROM (source.col1, ...)`, ported unchanged from `21_silver_dimensions.sql` — failed
building `intus.gold.*` from a freshly reconciled extract with
`DATATYPE_MISMATCH.CAST_WITHOUT_SUGGESTION`, Spark unable to unify the two sides'
struct types. Isolated by elimination across the three tuple comparisons in that file:
`dim_department`'s two-column comparison (no `NOT NULL` `BOOLEAN` column involved) never
fails; `dim_employee` (`is_current BOOLEAN NOT NULL`, plus `COMMENT ON COLUMN` on two of
its compared columns) and `dim_account` (`is_active BOOLEAN NOT NULL`) both fail the same
way. A `NOT NULL` `BOOLEAN` column specifically defeats Spark's implicit struct-cast
during row-value comparison here; Postgres's row-value `IS DISTINCT FROM` has no such
sensitivity. Fixed by rewriting both as an OR-chain of scalar `IS DISTINCT FROM`
comparisons, which constructs no struct at all.

**Consequences.** `21_silver_dimensions.sql` no longer matches `warehouse/transform/`'s
row-value-tuple idiom exactly for these two MERGEs — a real, documented divergence, not
an oversight, and the reason it surfaced now rather than in Phase 3b is that Phase 3b's
own live verification happened to run against data that didn't provoke it (the failure
depends on which specific columns are being compared, not merely on running the file at
all). Every gold view was then verified twice: once for "does it execute" (all seven,
row counts sane) and once for real numeric parity — see D-027.

## D-027 — Parity checked by comparing full row sets from a shared extract, not by inspection (2026-07-25)

**Decision.** `intus-lakehouse parity` fetches every row of all seven `intus.gold.*`
views and all seven `reporting.*` views, normalises types (`Decimal`/`DOUBLE` both to
`float`; dates to ISO strings), sorts each side independently on the full row tuple (not
either view's own `ORDER BY`), and compares column names, row counts, and every cell
within a small absolute tolerance (`0.01`) for floating-point rounding noise. Both
sources are read directly: `warehouse_source.py` over psycopg (typed Python values for
free), `databricks_source.py` over the SQL Statement Execution API (values arrive as
strings against a typed manifest — same converter pattern as parvum's
`parvum_export.gold_source`, reused rather than re-invented).

**Reconciling the two systems onto one shared extract, the design work the project brief
flagged as this phase's own to do.** Before this phase, the Postgres warehouse and the
Databricks lakehouse held data from different generator runs (different seed, different
scale) — parity against different inputs proves nothing. Fixed by generating one fresh
extract (`SCALE=small SEED=20260724`), rebuilding the Postgres warehouse from it
end-to-end (`migrate`, `load`, `build`), landing the identical CSVs to
`intus.landing.raw`, and rebuilding bronze → silver → gold from that same landing.
Rebuilding lakehouse-side could not go through the bundle job — `git_source` checks out
`main`, which does not yet have `30_gold_views.sql` or the new `gold` task (the
parvum-learned rule, carried forward again). Every statement instead ran directly against
the SQL Statement Execution API, the same mechanism Phase 3a/3b's live verification used.

**A session-persistence gap discovered along the way.** `21_silver_dimensions.sql` and
`22_silver_facts.sql` build on `CREATE OR REPLACE TEMPORARY VIEW`s across many
statements in the same file — but each call to `/api/2.0/sql/statements` is its own
ephemeral session by default; confirmed live that a temp view created by one call is
gone by the next. `databricks-sql-connector` (a real persistent connection) was the
obvious fix and was rejected on contact with this machine: it depends on `pandas`, and
`pandas._libs.internals`' compiled extension is blocked by the same Application Control
policy documented in CLAUDE.md, this time against a wheel-installed binary rather than an
interpreter's own DLL — a second, distinct trigger for the same class of block.
`/api/2.0/sql/sessions` (create once, pass `session_id` on every subsequent
`/api/2.0/sql/statements` call) turned out to be the REST-native answer, confirmed live
with a minimal temp-view-then-select probe before trusting it for the real rebuild — the
mechanism this phase's earlier BUILD_LOG entry for Phase 3b called "session by session"
without naming.

**A real bug the parity check caught that neither platform's own execution would ever
surface.** `rpt_sales_pipeline_by_rep`'s running total,
`SUM(...) OVER (PARTITION BY owner_employee_key ORDER BY created_date ROWS UNBOUNDED
PRECEDING)`, ties on `created_date` whenever a rep has two opportunities from the same
day — and unlike `RANK()` (well-defined tie semantics, already tested), a running
`SUM()`'s intermediate value for each tied row genuinely depends on the order tied rows
are summed in, which Postgres and Databricks resolved differently for the same input.
Each rep's *final* total matched on both platforms (order-independent), so nothing about
either view looked wrong until parity compared row-for-row. This is a bug in the
*original* Postgres view (005, already merged and applied), not a lakehouse-porting
error, so migrations' immutability (D-008) applies: fixed by a new migration
(`006_pipeline_tiebreak.sql`) adding `opportunity_id` as an explicit secondary sort key
in both the window and the display `ORDER BY`, ported identically into
`30_gold_views.sql`. See BUILD_LOG for the before/after parity output.

**Alternatives considered for the comparison itself.** (a) Comparing aggregate
checksums (row count + a hash of all values) per view — rejected: a mismatch would say
*that* two views disagree, not *which* row or column, which is far less useful for
actually debugging a divergence, and the seven views here are small enough (≤~700 rows)
that a full row-level diff costs nothing extra. (b) Trusting each view's own `ORDER BY`
and comparing positionally — rejected once the tiebreak bug above was found this way:
several views rank ties, which are not guaranteed to break the same way on both
platforms, so position-based comparison would report false mismatches independent of any
real one.

**Consequences.** All seven views match exactly against the reconciled extract (`views
matched: 7/7`), which is the load-bearing claim of this phase, not merely "the SQL
compiles." `lakehouse/tests/test_parity.py` unit-tests the comparison core in isolation
(tolerance, sort-independence, None handling) with no network or database access, so it
runs in CI even though the live comparison itself cannot yet (no `DATABRICKS_HOST` /
service-principal secret wired up — open item, unchanged from Phase 3a).

## D-028 — Cutover strategy: phased-by-persona parallel run, not big-bang (2026-07-26)

**Decision.** `docs/CUTOVER_PLAN.md` recommends migrating consumers off the legacy
Postgres warehouse persona-by-persona (HR → AI governance → FP&A → sales ops →
product/exec), each promoted independently once its own view clears a recurring
parity gate (five consecutive scheduled `intus-lakehouse parity` matches, not one
point-in-time check), rather than cutting every reporting view over in one event.

**Alternatives considered.** (a) Big-bang cutover once Phase 3c's parity check
passed — rejected because a one-time check proves the extract-at-hand reproduces
correctly, not that the platforms keep agreeing under ongoing production load, and
because it puts every persona's numbers at risk simultaneously for a dialect issue
that might only affect one view (Phase 3c already found two dialect failures from a
single execution; nothing suggests zero remain). (b) Parallel run with all seven
views promoted together after one soak period — an improvement over big-bang
(recurring checks instead of one) but still bundles unrelated failure domains: a
problem specific to `rpt_ai_cost_by_department`'s floating-point aggregation would
block HR's and FP&A's unrelated views from cutting over.

**Consequences.** Rollback during the parallel-run window is free (flip the routing
point back; Postgres was never decommissioned), which is the main argument for this
shape over big-bang — a big-bang rollback either requires keeping Postgres warm and
in sync anyway or reconstructing state mid-incident. The plan also surfaces
infrastructure this project doesn't have yet as explicit prerequisites rather than
assuming them away: a recurring build schedule on both platforms (both are
manually triggered today), and the `DATABRICKS_HOST`/service-principal CI secret
(open item since Phase 3a) is promoted from "nice to have" to "the mechanism the
promotion gate depends on." The plan also makes cutover of any persona explicitly
conditional on Phase 4's access-control parity (row filters/column masks matching
across platforms, not just the numbers) — a migration that reproduces the right
values behind the wrong access boundary is a governance gap, not a success.

## D-029 — Governance: two independent axes, and GRANT vs. row filter chosen per tier's actual shape (2026-07-26)

**Decision.** `lakehouse/sql/40_governance_schema.sql` / `41_governance_apply.sql`
track row-level scope (`department_scope`) and column-level capability
(`capability_grant`) as two separate tables, never one combined permission
check. Every RESTRICTED-tier column the generator declares
(`Dataset.columns_at(Tier.RESTRICTED)`) is masked; CONFIDENTIAL-tier tables get
either a GRANT (CRM: `crm_account`/`crm_opportunity`/`crm_subscription`/
`crm_invoice`, every row already owned by one business function, nothing to
filter within the table) or a department-scoped row filter plus a GRANT
(`fin_actual`/`fin_budget`: rows genuinely span every department in one table).

**Alternatives considered.** (a) One combined grant per persona ("can see this
table" implies "can see everything in it") — rejected because it cannot express
the real case this project's own data demands: a department manager should see
that a compensation row exists (who, when, what changed) without seeing the
amount, which is exactly what a SOX-style access review checks for — over-broad
grants that quietly bundle visibility with disclosure. (b) A row filter
everywhere CONFIDENTIAL data lives, for uniformity — rejected for the CRM
tables specifically: a row filter needs something to differentiate rows *on*,
and every `crm_*` row already belongs to Sales Operations alone, so a filter
there would either always return everything (pointless) or require inventing a
row-level distinction the data doesn't have. The classification's own wording
("restricted to a business function") is a whole-table statement for those
tables and a per-row one for finance's; the mechanism follows which is actually
true of the data, not a single default applied uniformly.

**Consequences.** Getting compensation's amount unmasked (Total Rewards) and
getting compensation's *rows* visible (a department scope) are independently
grantable and were verified independently, live: a persona with only the row
scope sees rows with every RESTRICTED value NULL; a persona with only the
capability (and a company-wide row scope) sees real values company-wide. Seven
persona groups exist as a result (`grp_exec`, `grp_hr_analyst`,
`grp_total_rewards`, `grp_security`, `grp_fp_a`, `grp_sales_ops`,
`grp_dept_manager_engineering`), not the five named personas in the target
posting/reporting views alone — Total Rewards and a narrow department-manager
persona were added specifically because they are the two personas that prove
the row/column independence actually holds, not personas the reporting views
themselves needed.

## D-030 — A row filter's own parameter name can silently defeat it (2026-07-26)

**Decision.** Every governance function parameter is prefixed `p_`
(`p_department_key`, `p_employee_key`, ...), unconditionally, even where it
reads awkwardly.

**What this fixes, found live.** An early draft of the department row filter
took a parameter named `dept` and queried a lookup table whose own column was
also named `dept`: `... WHERE s.dept = dept AND is_account_group_member(...)`.
This creates no error and no warning. Databricks resolves the unqualified
`dept` inside the subquery to the *table* column already in scope there, not
the outer function parameter — so the predicate silently became
`s.dept = s.dept`, which is true for every existing row regardless of which
department was actually asked about. Confirmed by direct comparison: calling
the function for a department with no matching scope row still returned
`true`. This is about as bad as a row-filter bug can be — not a crash, not a
wrong-looking result, just *quietly permits everything* — and would have
shipped invisibly if the function's return value hadn't been spot-checked
against a department deliberately chosen to have no grant.

**Alternatives considered.** Qualifying every reference inside the function
body instead of renaming parameters — rejected as relying on remembering to do
it correctly every time a function is written, the same category of mistake
that caused the bug in the first place. A naming convention that makes the
collision syntactically impossible (a parameter can never coincidentally share
a name with a column when it carries a prefix no column in this project ever
would) fails safe by construction instead of by discipline.

**Consequences.** No other governance function in this project can suffer the
same silent-permit failure mode, verified by construction rather than by
auditing each one by hand. Worth carrying forward as a house rule for any
future row filter or mask function, on this platform or any other: name
parameters so they cannot collide with a column name in any table the function
might ever query, not just the ones it queries today.

## D-031 — dim_employee's CHECK constraint traded for governance (2026-07-26)

**Decision.** `20_silver_schema.sql`'s `ck_dim_employee_span` CHECK constraint
(`valid_to IS NULL OR valid_to > valid_from`, added in Phase 3b) is dropped.
`dim_employee` now carries column masks (`termination_reason`, `job_level`)
instead.

**What forced this, found live.** Attempting to attach either a row filter or
a column mask to `dim_employee` while the CHECK constraint was still in place
failed outright:
`ROW_LEVEL_SECURITY_FEATURE_NOT_SUPPORTED.CHECK_CONSTRAINT` /
`COLUMN_MASKS_FEATURE_NOT_SUPPORTED.CHECK_CONSTRAINT` — Unity Catalog refuses
*either* governance feature on a table that has *any* CHECK constraint, not
just one that conflicts with the specific filter or mask being added. There is
no partial option here: keep the constraint and get no governance on this
table, or drop it and get both.

**Alternatives considered.** (a) Keep the constraint, enforce masking via a
governed VIEW over `dim_employee` instead of the table itself — rejected: a
view-level control is bypassable by anyone with direct table access, which is
a materially weaker guarantee than an engine-level mask that applies no matter
how the table is queried, and this project has already chosen "reproduce the
real control, not a workaround" every other time a platform limitation showed
up (D-024, D-026). (b) Drop the row filter idea for `dim_employee` entirely but
keep the constraint — rejected once it became clear the actually RESTRICTED
columns here (`termination_reason`, `job_level`) needed masks specifically,
not a filter on which employees are visible at all; a company directory
(name, department, title) being broadly visible is also the more realistic
default, so this wasn't a real loss.

**Consequences.** The guarantee `ck_dim_employee_span` gave — no SCD2 span can
be reversed — moves entirely to the transform, the same posture D-024 already
settled on for the harder no-overlap invariant on this identical table. The
risk this accepts is small: the generator's own anniversary-loop bug that could
have produced a zero-length span was already found and fixed at the source
(Phase 1, `world.py`'s `>=` fix), and `HR_OVERLAPPING_SPAN`'s self-join already
re-derives span validity independently of any database constraint. Losing a
redundant last-line-of-defense to gain a real, engine-enforced access control
is the trade worth making here.

## D-032 — A governance-owned identity mapping table, to avoid nested policies (2026-07-26)

**Decision.** `intus.governance.employee_department` — a plain
`(employee_key, department_key)` table, refreshed from `dim_employee`/
`dim_department` every run — exists specifically so `fact_compensation`'s and
`fact_performance_review`'s row filters never query `dim_employee` directly.

**What forced this, found live.** `rf_department_by_employee`'s first draft
joined `dim_employee` directly to resolve an employee's department. Attaching
it to `fact_compensation` (after `dim_employee` already had column masks
attached, D-031) failed:
`UNSUPPORTED_NESTED_ROW_OR_COLUMN_ACCESS_POLICY` — Unity Catalog refuses to let
one table's row filter or mask function scan *another* table that itself
carries a row filter or column mask, even when the function only touches
unrelated, unmasked columns (`department_name`, not `termination_reason`).
The restriction is table-level, not column-level.

**Alternatives considered.** Removing `dim_employee`'s masks so the lookup
would be unrestricted — rejected outright, that gives up the actual point of
D-031. Duplicating the department lookup inline in every function that needs
it — rejected as the same restriction would just resurface the moment
`dim_employee` (or whatever it's joined to) carries any policy of its own,
which is exactly the situation this is in.

**Consequences.** This is the real-world shape of a problem worth naming for
what it is: entitlement/authorization lookups belong in tables the
authorization system owns, not in the governed data itself — separating "what
the data says" from "who gets to see what" all the way down, not just at the
level of `department_scope`/`capability_grant` (D-029) but at the level of
which tables a policy function is even allowed to touch. Real enterprise IAM
systems maintain exactly this kind of denormalised identity/scope table for
this reason; this wasn't an available option so much as the platform enforcing
the same lesson.

## D-033 — Group-membership changes are not immediately visible to a running warehouse (2026-07-26)

**Decision.** No change to the SQL — this is an operational finding about the
platform, recorded because it directly affects how any access-review evidence
built on this governance layer should be read.

**What was found, live.** Creating a new Databricks account-level group (via
`/api/2.0/account/scim/v2/Groups`) and adding the session's own user as a
member did not make `is_account_group_member('<new group>')` return `true`
immediately — it returned `false` for roughly ten to fifteen minutes of real
wall-clock time before flipping to `true`, with no error or warning in between
to distinguish "not a member" from "membership not yet visible here." A
years-old group (`account users`) resolved correctly the entire time, ruling
out a fundamental incompatibility — this is a propagation delay, not a
platform limitation. Restarting the SQL warehouse did not shorten it, which
rules out a per-warehouse cache as the mechanism; the delay is closer to the
identity provider's own directory-sync interval. **The same delay applies
symmetrically to removal**: removing the test user from a group after
verification did not immediately restore the pre-membership (default-deny)
state either, confirmed live by querying immediately after the removal call
returned success and still seeing the granted state.

**Why this matters beyond this project.** GRANT-based access (whether a
principal can query a table at all) resolved *instantly* against the same
newly created groups — only the row-filter/mask policy functions
(`is_account_group_member`) were subject to the delay. A real access-review or
offboarding process built on this mechanism cannot assume a revoked group
membership takes effect the moment the revocation call succeeds; "the API
call returned 200" and "the control now reflects it" are different claims; an
audit needs to check the *effective* state, not the *requested* state, and
should allow for this lag rather than treat any observed gap as a control
failure.

**Consequences.** This project's own live verification (D-029's independent-axes
proof) was paced around this finding rather than fighting it — group creation
and membership changes were made early, other work continued while
propagation happened, and the actual assertions were checked once membership
had visibly taken effect (`is_account_group_member` returning `true`),
recorded rather than assumed. `docs/ACCESS_REVIEW.md` and
`docs/CHANGE_CONTROL.md` both cite this delay explicitly rather than silently
assume synchronous enforcement.

## D-034 — Power BI connects via DirectQuery as a provisioned persona, not an unaccountable admin session (2026-07-28)

**Decision.** The Power BI semantic model (`docs/POWERBI_MODEL.md`) connects to
`intus.gold.*` over DirectQuery using a personal access token that belongs to
`grp_exec` — a permanent grant, not a toggle-test the way earlier persona checks
in D-029's live verification were. Row-level security inside Power BI itself
(`Executive` / `Department Manager - Engineering` roles) mirrors the same two
personas already proven in Unity Catalog, rather than inventing a parallel
access model at the BI layer.

**What forced this, found live.** Two of the seven gold views —
`rpt_budget_variance` (built on `fact_gl_actual` and `fact_budget`) and
`rpt_ai_cost_by_department` (built on `fact_ai_usage`) — inherit Phase 4's row
filters, since gold views select straight from governed silver tables (D-029's
own point: governance enforced once at silver, gold has nothing further to do).
Queried directly as the account's own identity before it held any persona grant:
`rpt_budget_variance` and `rpt_ai_cost_by_department` returned zero rows, while
`rpt_headcount_trend`/`rpt_attrition_by_department` (built only from
`dim_employee`/`dim_department`, masked but not row-filtered) returned complete
data. An empty dashboard panel here would not have been a Power BI misconfiguration
to debug — it would have been the governance layer correctly denying an
unprovisioned identity, exactly as designed.

**Alternatives considered.** (a) Leave the connecting identity ungrouped and treat
the two empty views as a known limitation of the demo — rejected: it would make
the exec dashboard's own headline numbers (budget variance, AI cost) silently
wrong in a way indistinguishable from a real bug, undermining the actual point of
building a dashboard at all. (b) Grant the connecting identity a bespoke
"powerbi_service" capability set narrower than `grp_exec` — rejected as
unnecessary complexity for what this project needs: the BI layer's own natural
persona *is* the executive view (company-wide, aggregate, no masked-column
capability required since none of the six dashboard measures touch a masked
column), so reusing `grp_exec` is the right-sized grant, not an over-broad one
taken for convenience.

**Consequences.** This is the concrete case `docs/CUTOVER_PLAN.md` (D-028)
anticipated in the abstract when it named "the BI semantic model" as Phase 5's
first real consumer needing its own access provisioned before it can be treated
as production-ready — now it actually is provisioned, and verified live (both
previously empty views return real row counts after the grant took effect,
respecting D-033's propagation delay rather than assuming it was instant). Power
BI's own RLS roles are a second, independent enforcement layer on top of Unity
Catalog's — a viewer assigned "Department Manager - Engineering" in Power BI
Service would see Engineering-only rows even though the underlying DirectQuery
connection itself has company-wide access, the same "row scope and column
capability are independent, and here a third layer (BI-tool RLS on top of
platform RLS) is independent too" shape D-029 already established one layer
down.
