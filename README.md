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

See `docs/BUILD_LOG.md` for the running narrative, `docs/DECISIONS.md` for design
decisions with the alternatives considered, and `docs/data-catalog.md` (generated) for
the full column-level classification.

Next: the legacy Postgres warehouse — star schemas, SCD2 dimensions, plain-SQL ETL.

## Running it

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync                  # create the environment from uv.lock
make test                # run the suite
make lint                # ruff format check + lint
make generate            # full dataset into data/raw (~90s)
SCALE=small make generate # a fast subset for iterating
make catalog             # regenerate docs/data-catalog.md from the schemas
```

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
