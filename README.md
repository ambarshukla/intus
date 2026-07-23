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

The star schema is complete: a type-2 `dim_employee` whose no-overlap invariant is
enforced by a GiST exclusion constraint, a type-1 `dim_account`, `dim_date`,
`dim_department`, `dim_product`, and ten fact tables spanning every domain the
generators produce. Data-quality rules classify every problem they find as *rejected*,
*repaired* or *flagged*, and `intus-wh dq-score` grades detections against the
generator's seeded defects — reporting recall **and** false positives, because a rule
that rejects everything scores perfect recall. All **19 of 19** defect types are now
covered, at 100% recall with zero false positives, verified at both small and full
scale. Reporting views are next.

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
many were missed, and how many exceptions no seeded defect explains.

Generated data is gitignored: a deterministic generator plus a committed manifest is a
better record than several hundred megabytes of committed CSV. The same seed produces
byte-identical output on any machine, which is what makes the manifest's per-file
SHA-256 worth recording.

## Relationship to `parvum`

[parvum](https://github.com/ambarshukla/parvum) is this project's sibling: a
client-facing portfolio-data platform (custody-file ingestion → lakehouse → serving
APIs → dashboards). *intus* deliberately explores the other half of enterprise data
engineering: internal data, strict access control, and platform modernization. Shared
DNA (small, honest, end-to-end, documented); different problems.
