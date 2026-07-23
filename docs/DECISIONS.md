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
