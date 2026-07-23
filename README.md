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

Just started — scaffold only. See `docs/BUILD_LOG.md` for the running narrative and
`docs/DECISIONS.md` for design decisions as they're made.

## Relationship to `parvum`

[parvum](https://github.com/ambarshukla/parvum) is this project's sibling: a
client-facing portfolio-data platform (custody-file ingestion → lakehouse → serving
APIs → dashboards). *intus* deliberately explores the other half of enterprise data
engineering: internal data, strict access control, and platform modernization. Shared
DNA (small, honest, end-to-end, documented); different problems.
