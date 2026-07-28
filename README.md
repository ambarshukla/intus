# intus

An internal enterprise data lakehouse, built end to end as a reference project — and a
deliberate story of **modernizing a legacy SQL warehouse onto Databricks** with
governance as a first-class concern.

*intus* (Latin: "within") models the data estate of **Halcyon**, a fictional mid-size
B2B software company. Where a client-facing data platform answers "what do our
customers hold?", an internal one answers "how is the company itself doing?" — and has
to do so while enforcing strict, auditable limits on who can see what.

## What this project demonstrates

1. **Synthetic internal data, generated honestly.** Deterministic generators for the
   kinds of data every enterprise runs on: HR (headcount, compensation), sales and
   revenue (pipeline, bookings, invoices), product usage telemetry, LLM/AI usage and
   cost, budgets vs. actuals, and systems/access logs. Deliberate data-quality defects
   with ground-truth manifests, and **sensitivity tiers labeled at generation time**
   (public / internal / confidential / restricted) so governance downstream can be
   tested against known truth.
2. **The "before" state: a legacy SQL warehouse.** A classic Postgres star-schema
   warehouse with plain-SQL ETL — built properly, because you can't tell a credible
   modernization story without a credible legacy.
3. **The migration: legacy → Databricks lakehouse.** Medallion architecture, parity
   checks proving the new platform reproduces the old warehouse's numbers, and a
   documented cutover plan.
4. **Governance and compliance as the centerpiece.** Role-based access by persona
   (HR analyst, sales ops, FP&A, executive), row-level filters and column masks
   (compensation masked, revenue restricted), audit trail, access-review and
   change-control evidence — the controls a SOX-adjacent environment actually needs.
5. **Consumption.** A BI semantic layer and executive dashboards over the gold layer,
   with the access rules enforced at every hop.

## Status

**Phase 1 complete: the synthetic data generators.** `gen/` holds `intus_gen`, which
produces twelve datasets across the six domains above — about 1.8M rows at full scale —
deterministically from a seed, with sensitivity tiers declared beside each schema and
nineteen deliberate data-quality defects recorded in a ground-truth manifest.

**Phase 2 in progress: the legacy warehouse.** `warehouse/` holds `intus_warehouse` —
Postgres 16 in Docker, a forward-only SQL migration runner, and a `COPY`-based loader
that lands the extracts into an untyped `staging` schema (1.8M rows in ~3 seconds),
verifying each file against the generator's manifest hash before it loads.

**Phase 2 complete.** The star schema: a type-2 `dim_employee` whose no-overlap
invariant is enforced by a GiST exclusion constraint, a type-1 `dim_account`,
`dim_date`, `dim_department`, `dim_product`, and ten fact tables spanning every domain
the generators produce. Data-quality rules classify every problem they find as
*rejected*, *repaired* or *flagged*, and `intus-wh dq-score` grades detections against
the generator's seeded defects — reporting recall **and** false positives, because a
rule that rejects everything scores perfect recall. All **19 of 19** defect types are
covered, at 100% recall with zero false positives, verified at both small and full
scale.

Seven reporting views close it out — one per persona named in the target posting (HR
analyst, sales ops, FP&A, exec), each built around a different window-function
technique: month-over-month change (`LAG`), a moving average (a frame clause), running
totals (`SUM() OVER`), leaderboards (`RANK()`), a ratio-to-total, and relative standing
(`PERCENT_RANK()`). None expose restricted-tier data at individual grain — that
boundary belongs to Phase 4 (row-level security and column masking), next.

**Phase 3 complete: migration to Databricks.** `lakehouse/` holds the full medallion
build — bronze (`lakehouse/sql/10_bronze.sql`, twelve extracts landed as untyped Delta
tables via `read_files()`), silver (`20_silver_schema.sql`/`21_silver_dimensions.sql`/
`22_silver_facts.sql`, the same five dimensions and ten facts as the Postgres
warehouse, ported to Unity Catalog SQL), and gold (`30_gold_views.sql`, the same seven
`reporting.*` views as `intus.gold.*`). **`intus-lakehouse parity`** compares every
row of both platforms' views against one reconciled shared extract: **7/7 views match
exactly.** Two genuine Databricks-dialect rewrites and one real bug in the *original*
Postgres view (a non-deterministic tie in a running total) were found only by running
the comparison, not by reading the SQL — see `docs/DECISIONS.md` D-026/D-027.
`docs/CUTOVER_PLAN.md` closes the phase: a phased-by-persona parallel-run plan with a
recurring parity gate, written as if the reporting views had real consumers already.

**Phase 4 complete: governance.** `lakehouse/sql/40_governance_schema.sql` /
`41_governance_apply.sql` attach Unity Catalog row filters and column masks directly to
the silver layer — inherited automatically by every gold view built on top, confirmed
live even through a `GROUP BY`. Row-level scope and column-level capability are tracked
as two independent axes (a department manager sees that a compensation record exists
without seeing the amount), enforced for seven personas
(`grp_exec`/`grp_hr_analyst`/`grp_total_rewards`/`grp_security`/`grp_fp_a`/
`grp_sales_ops`/a narrow department-manager persona). Every RESTRICTED-tier column the
generators declare is masked — checked against the classification directly, not
restated by hand (`test_governance_coverage.py`). Verified end to end against the live
workspace by toggling real Databricks group membership: default-deny with no group,
department-scoped rows with masked values for a narrow persona, real values once a
capability is also granted — see `docs/DECISIONS.md` D-029 through D-033 for three real
platform constraints found only by running this live (a parameter-shadowing bug that
silently permitted everything, a CHECK-constraint/row-filter conflict, and a
group-membership propagation delay significant enough to change how an access review
must be read). `docs/ACCESS_REVIEW.md` and `docs/CHANGE_CONTROL.md` are the SOX-style
evidence this phase exists to produce.

**Phase 5 complete: Power BI.** `powerbi/intus_exec_dashboard.pbix` — DirectQuery
over `intus.gold.*`, six DAX measures, and two RLS roles (`Executive`, `Department
Manager - Engineering`) mirroring the exact personas already proven live in Unity
Catalog. Found live before building it: two of the seven gold views inherit
Phase 4's row filters, so the dashboard's own connecting identity needed a real
persona grant (`grp_exec`) or those two panels would show zero rows — not a bug,
governance correctly denying an unprovisioned connection (D-034). Proven with
Power BI Desktop's own **View As** feature — the BI-layer analogue of toggling
Databricks group membership — screenshotted in `docs/screenshots/`: headcount,
attrition, budget variance, and AI cost all shrink to Engineering-only under the
department-manager role while revenue and pipeline (neither department-scoped)
stay unchanged, the same row-scope proof Phase 4 made in Unity Catalog, reproduced
independently one layer up.

See `docs/BUILD_LOG.md` for the running narrative, `docs/DECISIONS.md` for design
decisions with the alternatives considered, and `docs/data-catalog.md` (generated) for
the full column-level classification.

## Running it

Requires [uv](https://docs.astral.sh/uv/), Python 3.12, and Docker for the warehouse.

```bash
uv sync                   # create the environment from uv.lock
make test                 # run every suite
make lint                 # ruff format check + lint
make generate             # full dataset into data/raw (~90s)
SCALE=small make generate # a fast subset for iterating
make catalog              # regenerate docs/data-catalog.md from the schemas
```

The legacy warehouse (Postgres on port 5433, so it can coexist with another local
instance on the default port):

```bash
make warehouse   # up + migrate + load + build + score, from scratch
make up          # start Postgres and wait for it
make migrate     # apply pending SQL migrations
make load        # truncate and reload staging from data/raw
make build       # run the transforms that build the star schema
make dq-score    # grade detections against the generator's defect manifest
make psql        # a psql shell in the container
make db-status   # connection and migration state
make down        # stop, keeping data;  make db-clean  also drops the volume
```

`make dq-score` prints, per rule, how many defects were seeded, how many were found, how
many were missed, and how many exceptions no seeded defect explains. The reporting views
(`reporting.rpt_headcount_trend`, `rpt_attrition_by_department`,
`rpt_sales_pipeline_by_rep`, `rpt_revenue_trend`, `rpt_product_usage_trend`,
`rpt_ai_cost_by_department`, `rpt_budget_variance`) are applied by `make migrate` /
`make warehouse`; query them directly with `make psql`.

Generated data is gitignored: a deterministic generator plus a committed manifest is a
better record than several hundred megabytes of committed CSV. The same seed produces
byte-identical output on any machine, which is what makes the manifest's per-file
SHA-256 worth recording.

The Databricks lakehouse (Unity Catalog catalog `intus`, on a shared workspace):

```bash
databricks auth login --host <workspace URL>  # one-time, browser-based
make land         # upload data/raw/*.csv to the Unity Catalog landing volume
make deploy-job   # deploy the bundle in databricks.yml
make run-job      # run the lakehouse build (needs deploy-job's changes merged to main first)
```

`DATABRICKS_HOST` (in `.env`) points every one of these at the right workspace; see
`.env.example`.

The Power BI exec dashboard: open `powerbi/intus_exec_dashboard.pbix` in
[Power BI Desktop](https://www.microsoft.com/en-us/power-platform/products/power-bi/desktop)
(free, Windows only). It's a live DirectQuery connection to `intus.gold.*`, so it
needs a personal access token for this project's Databricks workspace to actually
load data — `docs/POWERBI_MODEL.md` has the full connection spec, DAX measures, and
RLS role definitions. The two screenshots below are Power BI Desktop's own **View As**
feature applied to the same dashboard — no second login, no second workspace, just
previewing the report as each role would see it:

| Executive (no row filter) | Department Manager – Engineering (row-filtered) |
|---|---|
| ![Executive view: all departments visible](docs/screenshots/powerbi_view_as_executive.png) | ![Department Manager view: Engineering only](docs/screenshots/powerbi_view_as_dept_manager_engineering.png) |

Headcount, attrition, budget variance, and AI cost all shrink to Engineering-only
under the second role; revenue and open pipeline (neither has a department column)
are identical in both — the same row-scope-vs-column-capability split
`docs/DECISIONS.md` D-029 built into Unity Catalog, reproduced independently at the
BI layer (D-034).

## Relationship to `parvum`

[parvum](https://github.com/ambarshukla/parvum) is this project's sibling: a
client-facing portfolio-data platform (custody-file ingestion → lakehouse → serving
APIs → dashboards). *intus* deliberately explores the other half of enterprise data
engineering: internal data, strict access control, and platform modernization. Shared
DNA (small, honest, end-to-end, documented); different problems.
