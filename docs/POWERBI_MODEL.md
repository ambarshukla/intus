# Power BI: semantic model, DAX measures, and RLS roles

Phase 5's model, written as a spec + runbook rather than a binary `.pbix` diff —
Power BI Desktop's file format isn't something to hand-author blind, and this
project's own discipline throughout has been "verify against a live system," not
"assume the file will open." Everything below is text a person executes in Power BI
Desktop; the result (the `.pbix` plus screenshots) is committed once that's done.

## Why DirectQuery, and why this is the first phase where the *connecting identity*
matters as much as the SQL

Every table in this model is `intus.gold.*`, connected via **DirectQuery** (not
Import) — the whole point of Phase 4's governance layer is that it enforces live,
and an imported snapshot would go stale the moment `department_scope`/
`capability_grant` changes without anyone re-importing.

That has a real consequence, checked live before writing this doc: **four of the
seven gold views inherit Phase 4's row filters**, because they're built on
`fact_gl_actual`, `fact_budget`, or `fact_ai_usage` — tables Phase 4 attached a
department-scoped row filter to. Confirmed by direct query as the account's own
identity, ungrouped: `rpt_budget_variance` and `rpt_ai_cost_by_department` returned
**zero rows**, while `rpt_headcount_trend` and `rpt_attrition_by_department`
(built only from `dim_employee`/`dim_department`, which carry masks but no row
filter) returned complete data. An empty dashboard panel here isn't a Power BI bug
to debug — it's the same governance layer working exactly as designed, applied to
a connection that happens to hold no persona grant. **The connecting identity
needs its own persona, the same as any other consumer** — this is the concrete
case `docs/CUTOVER_PLAN.md` anticipated when it named "the BI semantic model" as
Phase 5's first real consumer needing its own access provisioned, not assumed.

Fixed the same way any other persona is provisioned: the account used to connect
Power BI (a personal access token from this workspace's account) was added to
`grp_exec` — company-wide row scope, per D-029 — permanently, not as a toggle
test. This is a real, intentional design decision, not leftover test state: **the
BI semantic-layer connection is itself modelled as the executive persona**, which
is also the correct default for an exec dashboard specifically. Re-verify before
connecting:

```sql
SELECT is_account_group_member('grp_exec');            -- expect true
SELECT count(*) FROM intus.gold.rpt_budget_variance;   -- expect > 0
SELECT count(*) FROM intus.gold.rpt_ai_cost_by_department;  -- expect > 0
```

## Connection details

| Setting | Value |
|---|---|
| Connector | Databricks (Power BI Desktop → Get Data → More → Databricks) |
| Server hostname | `dbc-8a8be026-0247.cloud.databricks.com` |
| HTTP path | `/sql/1.0/warehouses/0fb6ed828ed1e874` |
| Data connectivity mode | **DirectQuery** |
| Authentication | Personal access token (generate one for this workspace's account; never commit it — same rule as `.env`) |
| Catalog / schema | `intus` / `gold` |

Import all seven `rpt_*` views. No relationships need to be defined between them —
each gold view is already a flat, persona-scoped rollup (that's what "gold" means
in this project's medallion layering), not a star schema needing joins in the BI
layer.

## Measures (DAX)

One real gotcha worth flagging before the measures themselves: `rpt_sales_pipeline_
by_rep`'s `cumulative_pipeline_usd` and `total_open_pipeline_usd` columns are
window-function outputs — the same value repeats across every row for a given rep
(D-027's `SUM() OVER (...)` shape). `SUM()`-ing either of those in a measure would
multiply a rep's real total by however many opportunities they have — the exact
same class of bug as Phase 2d's 4883%-attrition incident (`docs/BUILD_LOG.md`),
here in a BI tool instead of SQL. The measures below sum `amount_usd` (one real
value per opportunity row) instead.

```dax
-- rpt_headcount_trend
Current Headcount =
VAR LatestMonth = MAX('rpt_headcount_trend'[month_start])
RETURN CALCULATE(SUM('rpt_headcount_trend'[headcount]), 'rpt_headcount_trend'[month_start] = LatestMonth)

-- rpt_attrition_by_department
-- Ratio of sums, not an average of per-department rates — the same lesson
-- Phase 2d's attrition bug taught: AVG() over a rate column double-weights
-- small departments and answers a different question than "company attrition."
Company Attrition Rate % =
DIVIDE(
    SUM('rpt_attrition_by_department'[trailing_12mo_terminations]),
    SUM('rpt_attrition_by_department'[avg_headcount])
) * 100

-- rpt_budget_variance
Total Budget Variance USD = SUM('rpt_budget_variance'[variance_usd])
Budget Variance % =
DIVIDE(
    SUM('rpt_budget_variance'[actual_usd]) - SUM('rpt_budget_variance'[budget_usd]),
    SUM('rpt_budget_variance'[budget_usd])
) * 100

-- rpt_ai_cost_by_department
Total AI Cost USD = SUM('rpt_ai_cost_by_department'[total_cost_usd])

-- rpt_sales_pipeline_by_rep — sums amount_usd, NOT the running-total columns (see above)
Total Open Pipeline USD = SUM('rpt_sales_pipeline_by_rep'[amount_usd])

-- rpt_revenue_trend
Current ARR USD =
VAR LatestMonth = MAX('rpt_revenue_trend'[month_end])
RETURN CALCULATE(SUM('rpt_revenue_trend'[total_arr_usd]), 'rpt_revenue_trend'[month_end] = LatestMonth)

-- rpt_product_usage_trend
Latest Day Active Users =
VAR LatestDay = MAX('rpt_product_usage_trend'[full_date])
RETURN CALCULATE(SUM('rpt_product_usage_trend'[active_users]), 'rpt_product_usage_trend'[full_date] = LatestDay)
```

## Row-level security roles

Two roles, deliberately mirroring the exact two personas already proven live in
Unity Catalog (`docs/ACCESS_REVIEW.md`'s review) rather than inventing new ones —
the point of this phase is showing the *same* governance model holds at the BI
semantic layer, not a parallel design that happens to also be called RLS.

**Role: `Executive`** — no filter on any table. Matches `grp_exec`'s company-wide
row scope; this is the role the connecting identity itself effectively has, and
the role a real Power BI Service viewer with executive access would be assigned.

**Role: `Department Manager - Engineering`** — a DAX filter on exactly the four
tables that carry a `department_name` column, matching Phase 4's own choice not
to row-scope the three views with no department dimension at all
(`rpt_sales_pipeline_by_rep` is region-scoped, not department-scoped;
`rpt_revenue_trend` and `rpt_product_usage_trend` are company/product-wide by
nature, same as Phase 4 left `fact_ai_usage`'s siblings `fact_opportunity`/
`fact_subscription`/`fact_invoice` ungoverned by row — see D-029):

| Table | Filter DAX |
|---|---|
| `rpt_headcount_trend` | `[department_name] = "Engineering"` |
| `rpt_attrition_by_department` | `[department_name] = "Engineering"` |
| `rpt_ai_cost_by_department` | `[department_name] = "Engineering"` |
| `rpt_budget_variance` | `[department_name] = "Engineering"` |

Set both roles up in Power BI Desktop: **Modeling → Manage Roles**, one role per
row above, table filters entered in DAX view for each table listed.

**Verification, not just configuration** — the same discipline as every governance
claim so far in this project: **Modeling → View As Roles**, tick `Department
Manager - Engineering`, and confirm the headcount/attrition/AI-cost/budget visuals
show Engineering only while revenue/product-usage/pipeline visuals are unaffected;
then tick `Executive` (or no role) and confirm every department reappears. This
pair of states is what the two required screenshots (below) capture — the BI-layer
equivalent of toggling group membership and re-querying, which is exactly how
Phase 4's own row filters were verified live.

## A pattern documented, not built: per-rep dynamic RLS

`rpt_sales_pipeline_by_rep` naturally wants a third role — a sales rep sees only
their own opportunities (`[owner_employee_id] = <their id>`) — using Power BI's
standard dynamic-RLS pattern (`USERPRINCIPALNAME()` joined against a
principal→employee mapping table, the exact BI-layer analogue of Phase 4's
`intus.governance.employee_department` mapping table, D-032). Not built here: this
project has no real second Power BI viewer to assign the role to and test
against — the same "no independent principal to test against" limitation
`docs/ACCESS_REVIEW.md` already names for Unity Catalog's own groups, recurring
one layer up. Named as the next real RLS role to add, not silently skipped.

## Dashboard and screenshots (the manual part)

Build one report page: KPI cards for the six measures above, a bar chart
(headcount or budget variance by department), a line chart (revenue trend), and a
table (AI cost by department, or sales pipeline leaderboard). Layout specifics are
a matter of taste, not correctness — nothing here dictates exact visual placement.

**Two screenshots are the load-bearing evidence**, committed to
`docs/screenshots/`:

1. `powerbi_view_as_executive.png` — **View As: Executive**, every department
   visible.
2. `powerbi_view_as_dept_manager_engineering.png` — **View As: Department Manager
   - Engineering**, only Engineering visible on the four department-scoped
   visuals, everything else unchanged.

Save the file as `powerbi/intus_exec_dashboard.pbix` (`.pbix` already has a binary
`.gitattributes` marker from Phase 0, so Git won't try to line-ending-normalise
it). **Never embed the personal access token in the saved file's connection
details in a way that would commit it** — Power BI Desktop prompts for
credentials per-machine by default and does not save them into the `.pbix` unless
explicitly configured to; leave that default alone.
