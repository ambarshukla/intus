-- Reporting views: what a persona actually queries, over the star schema
-- built by 003/004. Views only — reporting.* holds no tables (enforced by
-- test_reporting_schema_holds_no_tables) — so a report can never quietly
-- disagree with the facts underneath it.
--
-- Views are structure, not data, which is why they live in a migration
-- rather than a transform: their SQL text is versioned and checksummed like
-- any other DDL, and they recompute live at query time with no load step of
-- their own.
--
-- Each view below is built around a genuinely different window-function
-- technique, deliberately: this phase is as much the SQL drill track as it
-- is the warehouse, and seven variations on the same LAG/SUM pattern would
-- teach nothing seven times over.
--
-- None of these expose RESTRICTED-tier data at individual grain — no raw
-- salary, no per-employee performance rating. That is not an oversight; it
-- is the boundary Phase 4 (row-level security and column masking) exists to
-- own. Building an ungoverned compensation report now, ahead of the
-- machinery that would protect it, would be building the exact thing the
-- governance phase is supposed to prevent.

-- --------------------------------------------------------------------------
-- rpt_headcount_trend  (HR analyst)
-- --------------------------------------------------------------------------

-- Technique: LAG for period-over-period change, a frame clause for a
-- trailing moving average.
--
-- Headcount needs a month that has *no* hires or terminations to still
-- produce a row — unlike every other view here, which is naturally
-- event-driven (an opportunity, an invoice, a usage row already carries its
-- own date). That forces a manufactured spine of months, and the spine is
-- bounded by the employee population's own extent (earliest hire to latest
-- known date) rather than dim_date's full 2010-2035 range or the wall
-- clock — a view that showed a flat zero for twenty empty years on either
-- side of the real data would be technically correct and useless.
CREATE OR REPLACE VIEW reporting.rpt_headcount_trend AS
WITH bounds AS (
    SELECT min(hire_date) AS earliest, max(coalesce(termination_date, valid_from)) AS latest
    FROM warehouse.dim_employee
    WHERE employee_key <> -1
),
month_spine AS (
    SELECT generate_series(
        date_trunc('month', (SELECT earliest FROM bounds)),
        date_trunc('month', (SELECT latest FROM bounds)),
        INTERVAL '1 month'
    )::date AS month_start
),
department_months AS (
    SELECT department.department_code, department.department_name, spine.month_start,
           (spine.month_start + INTERVAL '1 month' - INTERVAL '1 day')::date AS month_end
    FROM month_spine AS spine
    CROSS JOIN warehouse.dim_department AS department
    WHERE department.department_key <> -1
),
headcount AS (
    SELECT
        dm.department_code,
        dm.department_name,
        dm.month_start,
        dm.month_end,
        count(*) AS headcount
    FROM department_months AS dm
    JOIN warehouse.dim_employee AS employee
      ON employee.department_code = dm.department_code
     AND employee.employee_key <> -1
     AND employee.valid_from <= dm.month_end
     AND (employee.valid_to IS NULL OR dm.month_end < employee.valid_to)
    GROUP BY dm.department_code, dm.department_name, dm.month_start, dm.month_end
)
SELECT
    department_code,
    department_name,
    month_start,
    headcount,
    headcount - lag(headcount) OVER (
        PARTITION BY department_code ORDER BY month_start
    ) AS headcount_change_mom,
    round(
        avg(headcount) OVER (
            PARTITION BY department_code ORDER BY month_start
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
        ),
        1
    ) AS headcount_3mo_avg
FROM headcount
ORDER BY department_code, month_start;

COMMENT ON VIEW reporting.rpt_headcount_trend IS
    'Monthly headcount by department for the HR analyst persona: month-over-month change and a 3-month rolling average.';

-- --------------------------------------------------------------------------
-- rpt_attrition_by_department  (HR analyst / exec)
-- --------------------------------------------------------------------------

-- Technique: RANK() over a computed metric — the classic leaderboard.
--
-- Trailing-12-month attrition as of the most recent month the data actually
-- covers, not "today": the extract's as-of date is a generation parameter
-- (see intus_gen), and a view that assumed the wall clock would silently
-- disagree with the rest of the warehouse the moment it was queried a day
-- after the data was generated.
CREATE OR REPLACE VIEW reporting.rpt_attrition_by_department AS
WITH as_of AS (
    SELECT max(valid_from) AS reporting_date FROM warehouse.dim_employee WHERE employee_key <> -1
),
window_bounds AS (
    SELECT reporting_date, (reporting_date - INTERVAL '12 months')::date AS window_start
    FROM as_of
),
leavers AS (
    SELECT department_code, count(DISTINCT employee_id) AS terminations
    FROM warehouse.dim_employee, window_bounds
    WHERE employee_key <> -1
      AND is_current
      AND termination_date IS NOT NULL
      AND termination_date BETWEEN window_bounds.window_start AND window_bounds.reporting_date
    GROUP BY department_code
),
-- Headcount at each end of the window, counted as DISTINCT employee_id — not
-- averaged directly over dim_employee's rows, which is one row per SCD2 span
-- rather than one row per employee. Averaging the raw rows was the first
-- version of this view, and it silently produced attrition rates over 1000%:
-- most spans cover neither boundary date at all, so they contribute 0+0 and
-- drag a per-row average toward zero regardless of true headcount.
headcount_at_window_start AS (
    SELECT department_code, count(DISTINCT employee_id) AS headcount
    FROM warehouse.dim_employee, window_bounds
    WHERE employee_key <> -1
      AND valid_from <= window_bounds.window_start
      AND (valid_to IS NULL OR window_bounds.window_start < valid_to)
    GROUP BY department_code
),
headcount_at_reporting_date AS (
    SELECT department_code, count(DISTINCT employee_id) AS headcount
    FROM warehouse.dim_employee, window_bounds
    WHERE employee_key <> -1
      AND valid_from <= window_bounds.reporting_date
      AND (valid_to IS NULL OR window_bounds.reporting_date < valid_to)
    GROUP BY department_code
),
average_headcount AS (
    -- The average of the trailing-12-month start and end headcount — a
    -- standard, simple attrition-rate denominator; a true daily average
    -- would need the same month-spine machinery as rpt_headcount_trend for
    -- a metric this view does not need at that resolution.
    SELECT
        coalesce(start_count.department_code, end_count.department_code) AS department_code,
        (coalesce(start_count.headcount, 0) + coalesce(end_count.headcount, 0)) / 2.0 AS avg_headcount
    FROM headcount_at_window_start AS start_count
    FULL OUTER JOIN headcount_at_reporting_date AS end_count
                  ON end_count.department_code = start_count.department_code
)
SELECT
    department.department_code,
    department.department_name,
    coalesce(leavers.terminations, 0) AS trailing_12mo_terminations,
    round(average_headcount.avg_headcount, 1) AS avg_headcount,
    round(
        100.0 * coalesce(leavers.terminations, 0) / nullif(average_headcount.avg_headcount, 0), 1
    ) AS attrition_rate_pct,
    rank() OVER (
        ORDER BY 100.0 * coalesce(leavers.terminations, 0) / nullif(average_headcount.avg_headcount, 0) DESC NULLS LAST
    ) AS attrition_rank
FROM warehouse.dim_department AS department
JOIN average_headcount ON average_headcount.department_code = department.department_code
LEFT JOIN leavers ON leavers.department_code = department.department_code
WHERE department.department_key <> -1
ORDER BY attrition_rank;

COMMENT ON VIEW reporting.rpt_attrition_by_department IS
    'Trailing-12-month attrition rate by department, ranked highest to lowest.';

-- --------------------------------------------------------------------------
-- rpt_sales_pipeline_by_rep  (sales ops)
-- --------------------------------------------------------------------------

-- Technique: a running total via SUM() OVER (... ORDER BY ...), plus
-- RANK() for the rep leaderboard.
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
        PARTITION BY open_pipeline.owner_employee_key ORDER BY open_pipeline.created_date
        ROWS UNBOUNDED PRECEDING
    ) AS cumulative_pipeline_usd,
    rep_totals.total_open_pipeline_usd,
    rank() OVER (ORDER BY rep_totals.total_open_pipeline_usd DESC) AS pipeline_rank
FROM open_pipeline
JOIN rep_totals ON rep_totals.owner_employee_key = open_pipeline.owner_employee_key
ORDER BY pipeline_rank, open_pipeline.created_date;

COMMENT ON VIEW reporting.rpt_sales_pipeline_by_rep IS
    'Open opportunities per rep with a running pipeline total and a rank leaderboard.';

-- --------------------------------------------------------------------------
-- rpt_revenue_trend  (FP&A / exec)
-- --------------------------------------------------------------------------

-- Technique: LAG-based period-over-period growth, plus a running total for
-- cumulative net-new ARR.
--
-- ARR is measured as of each month's end from fact_subscription directly
-- (start_date_key <= month end < end_date_key or still open) rather than via
-- a manufactured spine: unlike headcount, a month with zero subscription
-- activity does not need its own row to be meaningful here, since the
-- month-spine problem this view actually has is bounded by subscriptions
-- that already exist, one join away.
CREATE OR REPLACE VIEW reporting.rpt_revenue_trend AS
WITH month_ends AS (
    SELECT DISTINCT (date_trunc('month', full_date) + INTERVAL '1 month' - INTERVAL '1 day')::date AS month_end
    FROM warehouse.dim_date AS date
    JOIN warehouse.fact_subscription AS subscription ON subscription.start_date_key = date.date_key
),
monthly_arr AS (
    SELECT
        month_ends.month_end,
        sum(subscription.arr_usd) AS total_arr_usd
    FROM month_ends
    JOIN warehouse.fact_subscription AS subscription
      ON subscription.start_date_key <= (SELECT date_key FROM warehouse.dim_date WHERE full_date = month_ends.month_end)
     AND (
         subscription.end_date_key IS NULL
         OR subscription.end_date_key > (SELECT date_key FROM warehouse.dim_date WHERE full_date = month_ends.month_end)
     )
    GROUP BY month_ends.month_end
),
-- A window function's result cannot be fed straight into another window
-- function's argument (Postgres: "window function calls cannot be nested"),
-- so net_new_arr_usd is materialised in its own CTE layer before the running
-- SUM() below reads it as a plain column.
with_growth AS (
    SELECT
        month_end,
        total_arr_usd,
        total_arr_usd - lag(total_arr_usd) OVER (ORDER BY month_end) AS net_new_arr_usd
    FROM monthly_arr
)
SELECT
    month_end,
    total_arr_usd,
    net_new_arr_usd,
    round(100.0 * net_new_arr_usd / nullif(total_arr_usd - net_new_arr_usd, 0), 1) AS mom_growth_pct,
    sum(coalesce(net_new_arr_usd, total_arr_usd)) OVER (
        ORDER BY month_end ROWS UNBOUNDED PRECEDING
    ) AS cumulative_net_new_arr_usd
FROM with_growth
ORDER BY month_end;

COMMENT ON VIEW reporting.rpt_revenue_trend IS
    'Monthly ARR with period-over-period growth and cumulative net-new ARR.';

-- --------------------------------------------------------------------------
-- rpt_product_usage_trend  (product / exec)
-- --------------------------------------------------------------------------

-- Technique: a 7-day moving average via an explicit ROWS frame — the
-- textbook use case for a frame clause, and deliberately daily grain (not
-- monthly, unlike every other trend view here) because fact_usage_daily
-- already carries a row per day and smoothing day-of-week noise is exactly
-- what this view exists to demonstrate.
CREATE OR REPLACE VIEW reporting.rpt_product_usage_trend AS
WITH daily AS (
    SELECT
        date.full_date,
        product.product_code,
        product.product_name,
        sum(usage.active_users) AS active_users,
        sum(usage.sessions) AS sessions
    FROM warehouse.fact_usage_daily AS usage
    JOIN warehouse.dim_date AS date ON date.date_key = usage.date_key
    JOIN warehouse.dim_product AS product ON product.product_key = usage.product_key
    GROUP BY date.full_date, product.product_code, product.product_name
)
SELECT
    full_date,
    product_code,
    product_name,
    active_users,
    sessions,
    round(
        avg(active_users) OVER (
            PARTITION BY product_code ORDER BY full_date
            ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
        ),
        1
    ) AS active_users_7day_avg,
    active_users - lag(active_users, 7) OVER (PARTITION BY product_code ORDER BY full_date)
        AS active_users_change_vs_last_week
FROM daily
ORDER BY product_code, full_date;

COMMENT ON VIEW reporting.rpt_product_usage_trend IS
    'Daily active users and sessions by product, with a 7-day moving average and week-over-week comparison.';

-- --------------------------------------------------------------------------
-- rpt_ai_cost_by_department  (IT / FP&A — AI cost governance)
-- --------------------------------------------------------------------------

-- Technique: an unpartitioned SUM() OVER () as the denominator for a
-- ratio-to-total — ordinary aggregation cannot express "this department's
-- share of the *whole* month's spend" in the same query without a self-join,
-- and a window function can.
CREATE OR REPLACE VIEW reporting.rpt_ai_cost_by_department AS
WITH monthly_cost AS (
    SELECT
        date.fiscal_period,
        department.department_code,
        department.department_name,
        sum(usage.cost_usd) AS total_cost_usd,
        count(*) AS request_count
    FROM warehouse.fact_ai_usage AS usage
    JOIN warehouse.dim_date AS date ON date.date_key = usage.date_key
    JOIN warehouse.dim_department AS department ON department.department_key = usage.department_key
    WHERE department.department_key <> -1
    GROUP BY date.fiscal_period, department.department_code, department.department_name
)
SELECT
    fiscal_period,
    department_code,
    department_name,
    request_count,
    total_cost_usd,
    round(
        100.0 * total_cost_usd / sum(total_cost_usd) OVER (PARTITION BY fiscal_period), 1
    ) AS pct_of_month_total,
    rank() OVER (PARTITION BY fiscal_period ORDER BY total_cost_usd DESC) AS department_rank_in_month
FROM monthly_cost
ORDER BY fiscal_period, department_rank_in_month;

COMMENT ON VIEW reporting.rpt_ai_cost_by_department IS
    'Monthly AI usage cost by department: share of that month''s total spend and a within-month rank.';

-- --------------------------------------------------------------------------
-- rpt_budget_variance  (FP&A)
-- --------------------------------------------------------------------------

-- Technique: a running cumulative variance, plus PERCENT_RANK() to place
-- each department's variance among its peers for the same period — "how
-- unusual is this" rather than merely "what is this," which a plain
-- aggregate cannot answer.
CREATE OR REPLACE VIEW reporting.rpt_budget_variance AS
WITH budget_by_period AS (
    SELECT department_key, fiscal_period, sum(budget_usd) AS budget_usd
    FROM warehouse.fact_budget
    GROUP BY department_key, fiscal_period
),
actual_by_period AS (
    SELECT department_key, fiscal_period, sum(amount_usd) AS actual_usd
    FROM warehouse.fact_gl_actual
    GROUP BY department_key, fiscal_period
),
combined AS (
    SELECT
        department.department_code,
        department.department_name,
        coalesce(budget_by_period.fiscal_period, actual_by_period.fiscal_period) AS fiscal_period,
        coalesce(budget_by_period.budget_usd, 0) AS budget_usd,
        coalesce(actual_by_period.actual_usd, 0) AS actual_usd
    FROM warehouse.dim_department AS department
    LEFT JOIN budget_by_period ON budget_by_period.department_key = department.department_key
    LEFT JOIN actual_by_period
           ON actual_by_period.department_key = department.department_key
          AND actual_by_period.fiscal_period = budget_by_period.fiscal_period
    WHERE department.department_key <> -1
      AND (budget_by_period.fiscal_period IS NOT NULL OR actual_by_period.fiscal_period IS NOT NULL)
)
SELECT
    department_code,
    department_name,
    fiscal_period,
    budget_usd,
    actual_usd,
    actual_usd - budget_usd AS variance_usd,
    round(100.0 * (actual_usd - budget_usd) / nullif(budget_usd, 0), 1) AS variance_pct,
    sum(actual_usd - budget_usd) OVER (
        PARTITION BY department_code ORDER BY fiscal_period ROWS UNBOUNDED PRECEDING
    ) AS cumulative_variance_usd,
    round(
        (percent_rank() OVER (PARTITION BY fiscal_period ORDER BY actual_usd - budget_usd))::numeric,
        3
    ) AS overspend_percentile_in_period
FROM combined
ORDER BY department_code, fiscal_period;

COMMENT ON VIEW reporting.rpt_budget_variance IS
    'Budget vs. actual by department and fiscal period: cumulative variance and each department''s overspend percentile among its peers that period.';
