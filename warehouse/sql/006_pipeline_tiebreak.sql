-- Fixes rpt_sales_pipeline_by_rep's running total, which 005 shipped with a
-- genuine non-determinism: two opportunities created by the same rep on the
-- same day tie on `ORDER BY created_date` inside the window function, and
-- SUM() OVER (... ROWS UNBOUNDED PRECEDING) — unlike RANK(), which has
-- well-defined tie semantics — actually depends on the order tied rows are
-- summed in. Each tied row's own cumulative_pipeline_usd value is therefore
-- whatever order the query planner happens to pick, which can (and, caught
-- live against the lakehouse port, did) differ between two engines given the
-- identical data. Discovered by Phase 3c's parity check, not by inspection:
-- the eventual per-rep total was identical on both platforms, so nothing
-- about the view "looked" wrong until intermediate rows were compared
-- row-for-row. This is a new migration, not an edit to 005 — migrations are
-- immutable once applied (see docs/DECISIONS.md D-008).
--
-- Fix: `opportunity_id` as an explicit secondary sort key, both in the
-- window's ORDER BY (makes the running total itself deterministic) and the
-- view's own display ORDER BY (makes row order deterministic too, for the
-- same reason). Postgres has no natural row order to fall back on, and
-- neither does Databricks — an unqualified ORDER BY that doesn't fully
-- determine row order is always an under-specification, not a portability
-- quirk; this fix would have been correct even if the lakehouse migration
-- had never existed to surface it.

CREATE OR REPLACE VIEW reporting.rpt_sales_pipeline_by_rep AS
WITH open_pipeline AS (
    SELECT
        opportunity.owner_employee_key,
        employee.employee_id AS owner_employee_id,
        employee.full_name AS owner_name,
        employee.region,
        opportunity.opportunity_id,
        opportunity.account_key,
        account.account_name,
        date.full_date AS created_date,
        opportunity.stage,
        opportunity.amount_usd
    FROM warehouse.fact_opportunity AS opportunity
    JOIN warehouse.dim_employee AS employee ON employee.employee_key = opportunity.owner_employee_key
    JOIN warehouse.dim_account AS account ON account.account_key = opportunity.account_key
    JOIN warehouse.dim_date AS date ON date.date_key = opportunity.created_date_key
    WHERE opportunity.stage NOT LIKE 'Closed%'
),
rep_totals AS (
    SELECT owner_employee_key, sum(amount_usd) AS total_open_pipeline_usd
    FROM open_pipeline
    GROUP BY owner_employee_key
)
SELECT
    open_pipeline.owner_employee_id,
    open_pipeline.owner_name,
    open_pipeline.region,
    open_pipeline.opportunity_id,
    open_pipeline.account_name,
    open_pipeline.created_date,
    open_pipeline.stage,
    open_pipeline.amount_usd,
    sum(open_pipeline.amount_usd) OVER (
        PARTITION BY open_pipeline.owner_employee_key
        ORDER BY open_pipeline.created_date, open_pipeline.opportunity_id
        ROWS UNBOUNDED PRECEDING
    ) AS cumulative_pipeline_usd,
    rep_totals.total_open_pipeline_usd,
    rank() OVER (ORDER BY rep_totals.total_open_pipeline_usd DESC) AS pipeline_rank
FROM open_pipeline
JOIN rep_totals ON rep_totals.owner_employee_key = open_pipeline.owner_employee_key
ORDER BY pipeline_rank, open_pipeline.created_date, open_pipeline.opportunity_id;

COMMENT ON VIEW reporting.rpt_sales_pipeline_by_rep IS
    'Open opportunities per rep with a running pipeline total and a rank leaderboard.';
