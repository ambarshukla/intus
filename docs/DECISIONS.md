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
