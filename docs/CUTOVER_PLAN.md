# Migration & cutover plan: legacy Postgres warehouse → Databricks lakehouse

This is the artifact Phase 3 has been building toward: not just "the lakehouse
reproduces the warehouse's numbers" (Phase 3c, `intus-lakehouse parity`, 7/7 views
matching against a shared extract), but a written plan for how a real organisation
would actually cut over from one to the other without a customer- or
executive-visible incident. Everything below is written as if Halcyon's reporting
consumers were real production traffic, because that is the point of the exercise.

## 1. What is actually moving

The unit of migration is a **consumer of a reporting view**, not a table. Seven
`reporting.*` views in Postgres have exact counterparts in `intus.gold.*`:

| Persona | View | Technique | Sensitivity boundary |
|---|---|---|---|
| HR analyst | `rpt_headcount_trend` | `LAG` (month-over-month) | department-level only |
| HR analyst | `rpt_attrition_by_department` | distinct-count ratio | department-level only |
| Sales ops | `rpt_sales_pipeline_by_rep` | running `SUM()` | rep-level (no compensation) |
| FP&A | `rpt_revenue_trend` | moving average (frame clause) | department-level only |
| FP&A | `rpt_budget_variance` | ratio-to-total | cost-centre level |
| Product/exec | `rpt_product_usage_trend` | `RANK()` | account-level, no PII |
| IT/AI governance | `rpt_ai_cost_by_department` | `PERCENT_RANK()` | department-level only |

No view in either system exposes RESTRICTED-tier data (individual compensation,
individual performance ratings) at individual grain — that boundary is enforced by
schema today (tested in both `warehouse/tests` and `lakehouse/tests/test_gold_schema.py`)
and will be enforced by row filters/column masks once Phase 4 lands. **This plan
assumes Phase 4's access-control parity is a precondition for cutting over any
persona whose source data includes RESTRICTED columns upstream of the view** (all
seven, since every gold/reporting view is built from silver/warehouse tables that
carry RESTRICTED columns even though the views themselves project them out) — see
§6.

What is *not* moving in this phase: there is no live BI tool pointed at either
platform yet (Phase 5). This plan is deliberately written to be actionable the
day a real consumer exists, rather than deferred until one does — a cutover plan
written after the first consumer is already live is a much weaker artifact.

## 2. Strategy chosen: phased-by-persona parallel run, not big-bang

**Decision: run both platforms side by side per persona, promote a persona only
after its own view has passed parity on N consecutive scheduled runs, and never
migrate all seven views in one event.**

Three strategies were considered:

- **(a) Big-bang cutover** — flip every consumer from Postgres to Databricks in a
  single maintenance window, once Phase 3c's one-time parity check passes.
  Rejected: a one-time parity check proves the *current* extract reproduces
  correctly, not that the two platforms will keep agreeing as new data lands
  under real production load and scheduling. It also means every persona's
  reporting is on the line at once — a single dialect edge case discovered
  post-cutover (Phase 3c already found two purely from executing gold once; there
  is no reason to assume zero remain) takes down every stakeholder's numbers
  simultaneously, not just one team's.
- **(b) Parallel run, all seven views at once, cut over together after a fixed
  soak period** — a partial improvement (recurring parity checks instead of one),
  but still bundles unrelated failure domains. A dialect issue specific to
  `rpt_ai_cost_by_department` (say, a floating-point aggregation order
  difference under the AI-cost domain's wide token-count variance, the same kind
  of statistical fragility D-010's rate-card duplication test already exists to
  catch elsewhere) would block HR and FP&A from cutting over for a problem that
  has nothing to do with their data.
- **(c) Phased-by-persona parallel run (chosen)** — each persona's view(s) run on
  both platforms concurrently; each is promoted independently once its own
  parity history clears the gate in §4. Lower blast radius per promotion, and it
  matches how the posting's own stakeholders are organised (HR analyst, sales
  ops, FP&A, exec each consume their own view and would notice their own
  numbers changing, not another team's).

Sequencing order (lowest risk first, by data volatility and consumer count):

1. `rpt_headcount_trend`, `rpt_attrition_by_department` (HR) — slowest-changing
   source data (`dim_employee` SCD2 spans), smallest row counts, already the two
   views D-020 built first.
2. `rpt_ai_cost_by_department` (IT/AI governance) — moderate volatility, and the
   domain most likely to surface a floating-point/statistical dialect gap early
   while the blast radius is still one persona.
3. `rpt_budget_variance`, `rpt_revenue_trend` (FP&A) — higher stakeholder
   sensitivity (numbers feed real financial reporting in a SOX-adjacent
   environment), promoted only after the pattern is proven on lower-stakes
   personas first.
4. `rpt_sales_pipeline_by_rep` (sales ops) — this project's own found bug
   (D-027, the running-total tiebreak) lived here; promoted after its fix has
   had the longest possible soak time under the recurring gate.
5. `rpt_product_usage_trend` (product/exec) — last, since it is the one an
   executive is most likely to look at personally, and by this point three
   other personas' parallel-run history is the evidence that the pattern works.

## 3. What "parallel run" means concretely here

Both platforms already build from the same source extracts in principle (Phase
3c reconciled them onto one shared extract for verification), but a real
parallel run additionally requires:

- **A recurring build cadence on both sides**, not a one-time rebuild. The
  Postgres warehouse already rebuilds via `make warehouse`; the lakehouse via
  the `lakehouse_build` bundle job. Both would need to run on the *same*
  schedule against the *same* incremental data during the parallel-run window —
  today they are triggered manually, which is fine for a reference project but
  is called out explicitly here as a real production gap (§7).
- **A recurring parity check, not a point-in-time one.** `intus-lakehouse
  parity` exists and passed once (Phase 3c). Making it a cutover gate means
  running it after every build cycle during the parallel-run window and
  recording the result, not re-running it by hand before a decision. This is
  exactly the open item already on file — wiring `DATABRICKS_HOST` and a
  service-principal token as CI secrets — which this plan promotes from "nice
  to have for CI" to "the actual mechanism the cutover gate depends on."
- **Consumers read from exactly one platform at a time per persona**, selected
  by a routing point that can flip without a data migration — e.g. a view alias,
  a BI semantic-model data-source parameter (relevant once Phase 5 exists), or a
  connection string behind a name consumers don't hardcode. Nothing in this
  project builds that routing layer yet; it is scoped here as a Phase 5
  prerequisite, not invented speculatively now.

## 4. The promotion gate

A persona's view(s) may be promoted from Postgres to Databricks when **all** of
the following hold:

1. `intus-lakehouse parity` reports an exact match (not "within tolerance except
   for known issues") for that persona's view(s) on **five consecutive scheduled
   runs**, spanning at least one full week of real data movement — not five runs
   back-to-back against the same static extract, which would just re-check the
   same point-in-time state five times.
2. No open dialect finding for that view (the kind D-026 found) is unresolved.
3. Phase 4's access-control parity holds for that view's source tables — the
   persona sees the same rows and the same masked/unmasked columns on both
   platforms. (Blocked until Phase 4 ships; see §6.)
4. A rollback has been rehearsed at least once for that persona (§5) — not
   merely documented as possible.

Five consecutive runs is a deliberately arbitrary-but-stated number, chosen the
same way D-014's small-scale test seed was chosen: enough to make a one-off
timing fluke implausible, small enough to be achievable in a single week rather
than turning the plan into an excuse to never cut over. A real production
rollout would tune this per environment's actual change frequency; the number
itself is not the point, having a stated, falsifiable number instead of "when it
feels ready" is.

## 5. Rollback plan

Because both platforms run in parallel until promotion, **rollback during the
parallel-run window is free**: it is exactly the routing-point flip in §3, in
the opposite direction, and does not touch data on either side since Postgres
was never decommissioned. This is the entire reason phased parallel-run was
chosen over big-bang (§2) — a big-bang cutover's rollback would require either
keeping Postgres warm and in sync anyway (in which case it wasn't really a
big-bang) or reconstructing state, which is a much worse position to discover
you need mid-incident.

Rollback *after* a persona's source warehouse tables have been decommissioned
(§6) is a different and harder problem — this plan's answer is to not get there
until §6's criteria are met, rather than to design a rollback path for a state
this plan recommends never reaching without those criteria satisfied first.

## 6. Decommissioning the legacy warehouse

The Postgres warehouse is not decommissioned persona-by-persona as each is
promoted — it stays live, serving whichever personas haven't promoted yet, until
**all seven** views have cleared §4's gate. Only then:

- Retain a final Postgres backup/export per whatever data-retention policy
  applies (a SOX-adjacent environment needs evidence that historical figures
  remain reconstructable, not just that the current numbers are right — this is
  a compliance requirement, not an engineering one, and is flagged here rather
  than designed, since Phase 4 owns the audit/evidence design).
- Confirm no scheduled job, ad-hoc analyst query, or BI report still points at
  `reporting.*` — the same kind of "is anything still calling this" check any
  legacy-system sunset needs, here made concrete by the fact that every
  consumer in this project is enumerable (§1's table is the complete list).
- Only then stop the Postgres build schedule and, after a further stated
  retention window, tear down the container/volume.

**Explicit dependency on Phase 4, stated plainly**: this plan does not
recommend promoting any persona whose governance controls (row filters, column
masks) haven't been proven equivalent on both platforms first. A migration that
reproduces the right *numbers* but a different *access boundary* is not a safe
migration in a SOX-adjacent design — it is the kind of gap an access review is
specifically meant to catch, and catching it before cutover is strictly cheaper
than after.

## 7. Gaps this plan surfaces, carried forward as open items

Writing this plan as if the reporting views had real consumers (§1) surfaced
infrastructure this project
doesn't have yet, listed here rather than silently assumed away:

- **No recurring build schedule on either platform today** — both are triggered
  manually (`make warehouse`, `databricks bundle run`). A real parallel run
  needs both on the same cadence. Not built in this phase; noted as a
  prerequisite for actually exercising this plan rather than just describing it.
- **`DATABRICKS_HOST` + service-principal CI secrets** (open item since Phase
  3a) move from "nice to have" to "the mechanism §4's gate depends on" — the
  recurring parity check this plan requires can't run unattended without it.
- **No routing/aliasing layer exists yet** for consumers to flip between
  platforms — scoped here as a Phase 5 prerequisite (the BI semantic model is
  the first real consumer), not built speculatively before a consumer exists to
  use it.

## Related reading

`docs/DECISIONS.md` D-026/D-027 for the dialect and parity-methodology detail
this plan builds on; `docs/BUILD_LOG.md`'s Phase 3a–3c entries for what was
actually verified live at each layer; `docs/GLOSSARY.md` for term definitions
used above.
