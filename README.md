# intus

*intus* (Latin: "within") is a complete internal data platform for **Halcyon**, a
fictional mid-size B2B software company: synthetic data → a legacy Postgres star schema
→ a Databricks lakehouse → governance → a BI dashboard. Everything is deterministic,
tested and documented, so each layer can be checked against the one below it.

## The pieces

**`gen/` — synthetic data.** Twelve datasets across six domains (HR, sales and revenue,
product usage, LLM/AI cost, budgets vs. actuals, access logs), ~1.8M rows at full scale,
generated deterministically from a seed. Nineteen data-quality defects are seeded
deliberately and recorded in a ground-truth manifest, and every column carries a
sensitivity tier (public / internal / confidential / restricted) declared at generation
time — so governance downstream is testable against known truth.

**`warehouse/` — the legacy warehouse.** Postgres 16, plain-SQL ETL, a forward-only
migration runner. Five dimensions (including a type-2 `dim_employee` whose no-overlap
invariant is a GiST exclusion constraint), ten facts, and seven reporting views, each
built around a different window-function technique. `intus-wh dq-score` grades detected
problems against the seeded defects and reports false positives too: 19/19 defect types
at 100% recall, zero false positives.

**`lakehouse/` — the migration to Databricks.** The same model as bronze/silver/gold in
Unity Catalog SQL. `intus-lakehouse parity` compares every row of all seven view pairs
across both platforms against one shared extract: **7/7 match exactly**. Doing that
found two real dialect rewrites and one genuine bug in the original Postgres view.
`docs/CUTOVER_PLAN.md` writes up the parallel run.

**Governance.** Unity Catalog row filters and column masks attached to silver and
inherited automatically by the gold views on top, for seven personas. Row scope and
column capability are independent axes — a department manager can see that a
compensation record exists without seeing the amount. Every restricted-tier column the
generators declare is masked, checked against the classification rather than by hand.
`docs/ACCESS_REVIEW.md` and `docs/CHANGE_CONTROL.md` are the SOX-style evidence.

**`powerbi/` — consumption.** A DirectQuery dashboard over `intus.gold.*` with RLS roles
mirroring the Unity Catalog personas, so the same access rules hold at the last hop.

All phases are complete. `docs/BUILD_LOG.md` has the running narrative,
`docs/DECISIONS.md` the design decisions with alternatives considered, and
`docs/data-catalog.md` (generated) the full column-level classification.

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

The warehouse (Postgres on port 5433, so it can coexist with a local instance on the
default port):

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

`make warehouse` then `make psql` is also a decent standalone SQL sandbox: a realistic
star schema with messy-but-known data already loaded, and seven reporting views
(`reporting.rpt_headcount_trend`, `rpt_attrition_by_department`,
`rpt_sales_pipeline_by_rep`, `rpt_revenue_trend`, `rpt_product_usage_trend`,
`rpt_ai_cost_by_department`, `rpt_budget_variance`) to read or take apart.

Generated data is gitignored: a deterministic generator plus a committed manifest is a
better record than several hundred megabytes of CSV. The same seed produces
byte-identical output on any machine, which is what makes the manifest's per-file
SHA-256 worth recording.

The Databricks lakehouse (Unity Catalog catalog `intus`, on a shared workspace):

```bash
databricks auth login --host <workspace URL>  # one-time, browser-based
make land         # upload data/raw/*.csv to the Unity Catalog landing volume
make deploy-job   # deploy the bundle in databricks.yml
make run-job      # run the lakehouse build (deploy-job's changes must be on main first)
```

`DATABRICKS_HOST` (in `.env`, see `.env.example`) points all of these at the right
workspace.

The dashboard: open `powerbi/intus_exec_dashboard.pbix` in
[Power BI Desktop](https://www.microsoft.com/en-us/power-platform/products/power-bi/desktop)
(free, Windows only). It's a live DirectQuery connection, so it needs a personal access
token for the workspace; `docs/POWERBI_MODEL.md` has the connection spec, DAX measures
and RLS role definitions. The screenshots below are Desktop's **View As** feature
previewing the same report as each role:

| Executive (no row filter) | Department Manager – Engineering (row-filtered) |
|---|---|
| ![Executive view: all departments visible](docs/screenshots/powerbi_view_as_executive.png) | ![Department Manager view: Engineering only](docs/screenshots/powerbi_view_as_dept_manager_engineering.png) |

Headcount, attrition, budget variance and AI cost all shrink to Engineering-only under
the second role; revenue and open pipeline (neither has a department column) are
identical in both — the row-scope-vs-column-capability split from Unity Catalog,
reproduced at the BI layer.

## Relationship to `parvum`

[parvum](https://github.com/ambarshukla/parvum) is this project's sibling: a
client-facing portfolio-data platform (custody-file ingestion → lakehouse → serving APIs
→ dashboards). *intus* takes the other half — internal data, strict access control, and
platform modernization. Shared DNA, different problems.
