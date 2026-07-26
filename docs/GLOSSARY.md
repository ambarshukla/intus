# Glossary

Terms used in this project, expanded on first use in the docs and kept here.

- **Medallion architecture** — the bronze (raw) → silver (cleaned/conformed) → gold
  (business-ready) layering convention for a lakehouse.
- **Row-level security (RLS)** — access rules that filter which *rows* of a table a
  given principal can see (e.g., an HR analyst sees only their region's employees).
- **Column masking** — access rules that redact or transform specific *columns* for
  unauthorized principals (e.g., compensation shows as NULL or a band, not a number).
- **SOX (Sarbanes-Oxley Act)** — US financial-reporting law; for data platforms it
  implies auditable change control, separation of duties, access reviews, and evidence
  that controls actually operated.
- **Parity check** — during a platform migration, an automated comparison proving the
  new system reproduces the old system's numbers before cutover.
- **Sensitivity tier** — a label (here: public / internal / confidential / restricted)
  attached to data at generation time, driving which governance controls apply.
- **SCD2 (slowly changing dimension, type 2)** — a dimension table that keeps history by
  storing one row per period of validity, with effective-from/effective-to dates, rather
  than overwriting an attribute when it changes. `hr_employee_history` is generated in
  this shape so the warehouse phase builds its employee dimension from honestly
  effective-dated source data instead of inferring history from a snapshot.
- **ARR (annual recurring revenue)** — the annualised value of a subscription, net of
  discount; the headline revenue metric for a subscription business.
- **GL (general ledger) account** — the code an accounting entry is booked against
  (e.g. 6000 Salaries, 6700 Cloud Infrastructure), determining how it rolls up in
  financial reporting.
- **Cost centre** — the organisational unit a cost is charged to; here one per
  department, and the unit budgets and actuals are compared at.
- **Fiscal period** — the accounting month a transaction belongs to, which is not
  always the month it was posted in. The gap between the two is what period-close
  controls exist to police.
- **Segregation of duties** — the control principle that no single person both initiates
  and approves a transaction. Modelled here by keeping budget approvers and ledger
  posters distinct populations.
- **ITGC (IT general controls)** — the baseline IT controls a financial audit relies on:
  access provisioning and removal, change management, and operations. "A terminated
  employee's account still logging in" is a classic ITGC exception.
- **Data steward** — the business function accountable for a dataset; the approver an
  access review routes to, recorded per dataset in the catalog.
- **Retention window** — how long a dataset keeps history. Security logs here retain 180
  days while every other domain keeps full history, as they do in practice.
- **Run manifest** — the file written alongside generated data recording the inputs
  (seed, scale, as-of date), each output file's SHA-256 and row count, and the
  ground-truth list of injected defects.
- **Defect manifest / ground truth** — the record of exactly which rows were
  deliberately corrupted, against which a data-quality framework's detection rate can be
  scored rather than merely demonstrated.
- **Property-based testing** — testing a universally quantified claim ("for *any* seed,
  two runs agree") by having the framework generate many inputs, rather than asserting
  against a handful of hand-picked examples. Done here with `hypothesis`.
- **Impossible travel** — two authentications from locations too far apart to have been
  travelled between in the elapsed time; a standard credential-sharing signal.
- **Shadow AI** — use of AI models outside the approved catalog, invisible to the
  governance and cost-allocation processes built around the approved ones.
- **uv workspace** — a set of Python packages sharing one lockfile, so every member
  resolves identical dependency versions and a skew between them is unrepresentable.
- **Staging layer** — the schema raw extracts land in before any transformation: here
  every column is `text` with no constraints, so a malformed value is rejected by the
  transform with a business reason rather than by the loader with a parse error.
- **Star schema** — the dimensional modelling pattern: a central *fact* table of
  measurements joined to surrounding *dimension* tables of descriptive attributes.
- **Fact / dimension** — facts are the things you measure (an invoice, a day's usage);
  dimensions are the things you slice by (employee, account, product, date).
- **COPY** — Postgres's bulk load statement, streaming a whole file server-side in one
  statement instead of one round trip per row.
- **Truncate-and-reload** — replacing a table's contents wholesale each run, rather than
  merging changes into it. Makes a rerun idempotent by construction.
- **Transactional DDL** — Postgres allows schema changes inside a transaction, so a
  migration that fails partway leaves nothing behind. Not all engines do this.
- **Savepoint** — a nested, partial rollback point *within* a transaction. Rolling back
  to one undoes work since the savepoint but does not commit anything, which is why code
  that assumes a savepoint is a transaction silently loses durability.
- **Migration checksum** — a hash of a migration file recorded when it is applied, so
  that editing an already-applied migration is detected instead of quietly leaving the
  schema and the file that claims to have built it out of step.
- **Forward-only migrations** — no `down` scripts; a mistake is corrected by writing a
  new migration rather than reversing an old one.
- **Load audit** — a durable record of what was loaded, from which file, with which
  content hash, so the provenance of the data currently in the warehouse is answerable.
- **Conformed dimension** — one agreed definition of a business entity shared by every
  fact that references it, so an HR headcount report and a finance spend report slice by
  the same "department" rather than by two lookups that disagree.
- **Surrogate key** — a warehouse-generated identifier (`employee_key`) standing in for
  the source's natural key. Facts reference it, which is why it must stay stable across
  loads.
- **SCD type 1 / type 2** — type 1 overwrites an attribute when it changes, keeping only
  current state; type 2 keeps a dated version per change. The right choice depends on
  what the source can evidence, not on which is more sophisticated.
- **MERGE** — a single SQL statement that inserts, updates or deletes depending on
  whether a source row matches the target. Postgres gained it in 15; `WHEN NOT MATCHED
  BY SOURCE` arrived in 17.
- **Exclusion constraint** — a Postgres constraint rejecting rows whose values *overlap*
  an existing row under a chosen operator. Used here with `daterange` to make
  overlapping SCD2 versions impossible to store.
- **Partial index** — an index over a subset of rows (`WHERE is_current`). A partial
  *unique* index enforces "at most one current version per employee" without forbidding
  the historical ones.
- **Disposition (data quality)** — what was done about a detected problem: the row was
  `rejected`, `repaired` (kept with a value corrected), or `flagged` (kept unchanged and
  recorded). A layer that only rejects silently loses data.
- **Recall / false positive** — of the defects actually present, the share detected; and
  detections that no real defect explains. Recall alone is not a quality measure, since
  rejecting everything scores 100%.
- **Idempotent** — running it twice leaves the same result as running it once. The
  defining property of a transform, as against a migration.
- **Point-in-time join** — resolving a type-2 dimension's surrogate key as of a specific
  date, rather than joining on the natural key alone (which would return every version).
  The standard way a fact table connects to an SCD2 dimension.
- **SCIM (System for Cross-domain Identity Management)** — the standard API shape for
  managing users and groups. Used here to create Databricks account/workspace groups and
  toggle test membership without a browser, the same way Terraform or an HR system would
  provision access in a real deployment.
- **Default-deny** — a control's failure direction: a principal with no matching grant
  sees nothing, rather than everything. `intus.governance.department_scope` is
  deliberately built this way — a group with zero rows in it is locked out of every
  department-scoped table, not implicitly trusted.
- **Row filter (Unity Catalog)** — a SQL function attached to a table via
  `ALTER TABLE ... SET ROW FILTER`, evaluated per row to decide whether the querying
  principal may see it. The Databricks-native mechanism this project's row-level
  security is built on, distinct from the generic RLS concept above in being an
  engine-enforced table property rather than a view's `WHERE` clause.
- **Capability grant** — in this project's governance design, a flag saying whether a
  persona sees a masked column's real value, tracked independently of which *rows* that
  persona can see (row scope). Two separate tables, two separate questions — see
  `docs/DECISIONS.md` D-029.
- **Unknown member** — a sentinel dimension row (here, surrogate key `-1`) that a fact's
  foreign key falls back to when it cannot resolve a real one, so downstream joins and
  aggregates never have to special-case a NULL foreign key.
- **Degenerate dimension** — a natural-key or descriptive attribute kept directly on a
  fact table rather than modelled as its own dimension (e.g. `subscription_id` on
  `fact_invoice`). Used here also for identity columns kept alongside a foreign key that
  can fall back to an unknown member, so the real value stays recoverable.
- **Deferred constraint** — a foreign key or other constraint checked at transaction
  commit rather than after each statement (`DEFERRABLE INITIALLY DEFERRED`). What allows
  a parent row to be removed and a child row to be reloaded against the new parent set
  within one transaction, so long as both sides agree by commit time.
- **Fact grain** — the level of detail one fact row represents (e.g. one row per
  date/account/product for `fact_usage_daily`). Determines what a natural key for the
  table would be, and whether a surrogate key is worth having at all.
- **Window function** — a SQL function computed across a set of related rows
  (a "window") without collapsing them into one row, unlike a `GROUP BY`
  aggregate. `LAG`/`LEAD` (a neighbouring row's value), running `SUM()` (a
  cumulative total), `RANK()` (a leaderboard position), and `PERCENT_RANK()`
  (relative standing, 0–1) are the four used across `reporting.*`.
- **Frame clause** — the `ROWS BETWEEN ... AND ...` part of a window function
  that narrows the window to a sliding range (e.g. `ROWS BETWEEN 6 PRECEDING
  AND CURRENT ROW` for a 7-day moving average) rather than the whole partition.
- **RANK() vs. dense ranking** — `RANK()` leaves gaps after a tie (1, 1, 3);
  `DENSE_RANK()` does not (1, 1, 2). Assuming no gaps without checking for
  ties is a real mistake this project's own tests caught.
- **Ratio-to-total** — a value's share of a group's total, expressed with an
  unpartitioned or differently-partitioned `SUM() OVER ()` as the denominator
  inside the same query that computes the numerator, rather than a second
  aggregate query joined back.
- **Unity Catalog (UC)** — Databricks's governance layer over data assets: a three-level
  namespace (catalog.schema.table), plus the access-control, lineage, and (eventually,
  Phase 4) row/column-security machinery built on top of it.
- **Catalog / schema / volume** — Unity Catalog's namespace levels. A *catalog* is the
  top-level container (`intus`, isolated from the shared workspace's `parvum`); a
  *schema* groups objects within it (`bronze`, `silver`, `gold`, `landing`); a *volume*
  is a managed area for non-tabular files (`intus.landing.raw`, where uploaded CSVs
  land before `read_files()` ingests them).
- **Delta Lake / Delta table** — the table format every Unity Catalog managed table
  uses: versioned, ACID, with automatic history (`DESCRIBE HISTORY`) recording every
  write. The lakehouse's answer to what `staging.load_audit` does by hand in Postgres.
- **`read_files()`** — a Databricks SQL table-valued function that reads files (here,
  CSVs in a volume) directly into a query or `CREATE TABLE ... AS SELECT`, with an
  explicit schema overriding type inference — the mechanism bronze uses for untyped
  landing, playing the role Postgres's `COPY` plays for staging.
- **U2M (user-to-machine) OAuth** — the browser-based login flow a human uses to
  authorize the Databricks CLI, as against machine-to-machine auth (a service
  principal's client secret) that would run unattended in CI. Phase 3 needed this once,
  interactively, before any workspace object could be created.
- **Databricks Asset Bundle** — a job/pipeline definition as YAML (`databricks.yml`)
  deployed via `databricks bundle deploy`, so a data pipeline is reviewable and in git
  rather than clicked together in the Workflows UI.
- **`git_source`** — a bundle job setting that has each run check out a git branch and
  execute code from that checkout, rather than from whatever was last deployed. Means
  the running pipeline can never drift from what's on `main`, since there is no second
  deployed copy to go stale.
- **SQL warehouse** — Databricks's SQL-optimized compute, addressed by a `warehouse_id`,
  that runs both ad-hoc queries (via the SQL Statement Execution API) and `sql_task`
  bundle jobs. Not a credential — safe to write in plaintext, unlike a workspace host.
- **`QUALIFY`** — a `WHERE`-like clause that filters on a window function's result
  directly, without wrapping the query in a subquery just to reference it in a `WHERE`.
  Silver's substitute for Postgres's `SELECT DISTINCT ON (...) ... ORDER BY ...`, which
  does not exist on this platform (`DISTINCT ON` parses as a function call named `ON`
  here, confirmed live).
- **CHECK constraint enforcement, single-row vs. cross-row** — Delta CHECK constraints
  may only reference the row being written; a constraint referencing another row (e.g.
  "no other row for this key overlaps this one") is rejected at `ADD CONSTRAINT` time.
  Postgres's `EXCLUDE USING gist` has no Delta equivalent for exactly this reason — see
  docs/DECISIONS.md D-024.
- **PRIMARY KEY / FOREIGN KEY, informational-only** — Unity Catalog accepts these
  constraint declarations, but does not enforce them: a duplicate primary key inserts
  without error. They exist for the query optimiser and BI/catalog tooling to read, not
  as a guarantee a write can violate and be rejected for.
- **Gold layer** — the medallion architecture's business-ready tier: the layer a report
  or BI tool actually queries. Here, the seven `intus.gold.rpt_*` views, the lakehouse
  counterpart to Postgres's `reporting.*`.
- **Correlated subquery** — a subquery whose predicate references a column from the
  surrounding query, evaluated once per outer row rather than once overall. Postgres
  trusts the planner to prove a correlated scalar subquery returns at most one row at
  runtime; Databricks SQL requires syntactic proof (an aggregate), rejecting an
  otherwise-safe bare equality predicate outright — see docs/DECISIONS.md D-026.
- **SQL Statement Execution API session** — each call to
  `/api/2.0/sql/statements` is, by default, its own ephemeral session: state created by
  one call (a `CREATE TEMPORARY VIEW`, a `DECLARE VARIABLE`) is gone by the next. A
  session created via `/api/2.0/sql/sessions` and passed as `session_id` on later calls
  binds them together, the REST equivalent of one open JDBC/ODBC connection. Needed here
  because `21_silver_dimensions.sql` and `22_silver_facts.sql` build on temp views across
  many statements.
- **Parity check (this project's, specifically)** — `intus-lakehouse parity`, comparing
  every `intus.gold.*` view's full row set against its `reporting.*` counterpart after
  both are built from the same landed extract: column names, row counts, and every cell
  (within a small numeric tolerance for floating-point rounding noise), independent of
  either view's own `ORDER BY`. See D-027.
