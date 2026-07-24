-- Gold: the seven reporting views, one dialect over — the lakehouse's
-- equivalent of warehouse/sql/005_reporting_views.sql, same persona mapping,
-- same window-function technique per view, same names (rpt_* under `gold`
-- instead of `reporting`). Runs after 22_silver_facts.sql (task ordering in
-- databricks.yml, not a transaction).
--
-- Views only, same reasoning as reporting.*: a report that could disagree
-- with the facts underneath it because a refresh was missed is exactly what
-- "views, not tables" exists to prevent. No RESTRICTED-tier data at
-- individual grain here either — same D-020 boundary, checked by
-- test_gold_schema.py the same way test_reporting_schema.py checks Postgres.
--
-- --------------------------------------------------------------------------
-- Where this file's dialect substitutions come from, and why they are
-- structural, not cosmetic
-- --------------------------------------------------------------------------
--
-- `generate_series` has no Databricks equivalent (same as 21_silver_dimensions
-- .sql's dim_date generation) — `explode(sequence(...))` substitutes
-- throughout. Postgres's `date_trunc('month', d)::date` becomes `trunc(d,
-- 'MM')`, which returns DATE directly rather than TIMESTAMP, so there is no
-- cast to chase afterward. A month's last day is `date_sub(add_months(d, 1),
-- 1)` rather than `(d + INTERVAL '1 month' - INTERVAL '1 day')::date` — both
-- say the same thing, but date+interval arithmetic on this platform returns
-- TIMESTAMP for some interval units and DATE for others depending on
-- version, confirmed inconsistent enough live to be worth avoiding rather
-- than relying on; the two `date_*`/`add_months` functions are unambiguous
-- about their return type.
--
-- rpt_revenue_trend's correlated scalar subqueries
-- (`(SELECT date_key FROM dim_date WHERE full_date = month_ends.month_end)`)
-- do NOT port unchanged — confirmed live
-- (`UNSUPPORTED_SUBQUERY_EXPRESSION_CATEGORY.MUST_AGGREGATE_CORRELATED_SCALAR_SUBQUERY`).
-- Postgres trusts the planner to prove at most one row matches
-- `full_date = month_ends.month_end` at runtime; Databricks' optimiser
-- requires syntactic proof — an aggregate in the subquery — and a bare
-- equality predicate doesn't count even though `full_date` is genuinely
-- unique. The view below joins `dim_date` a second time to resolve
-- `month_end`'s own `date_key` instead of correlating for it, which sidesteps
-- the restriction entirely rather than working around it with an aggregate.
--
-- rpt_budget_variance drops the `::numeric` cast on `percent_rank()` before
-- `round()` — Postgres needs it because `percent_rank()` returns
-- `double precision` and Postgres's `round()` has no double-precision
-- overload; Databricks' `round()` accepts DOUBLE directly, so the cast has
-- nothing to do here.
--
-- rpt_sales_pipeline_by_rep's running-total window includes `opportunity_id`
-- as an explicit tiebreaker, matching warehouse/sql/006_pipeline_tiebreak.sql
-- — not a dialect substitution but a genuine bug in the *original* view this
-- migration ported, caught by the parity check itself: two opportunities
-- created by the same rep on the same day tie on `ORDER BY created_date`,
-- and unlike RANK() (well-defined tie semantics), a running SUM() actually
-- depends on the order tied rows are summed in. Both platforms picked a
-- different (and each internally consistent) order for the tied pair, so the
-- per-rep *total* matched while each tied row's own intermediate cumulative
-- value did not — see docs/DECISIONS.md and BUILD_LOG for the discovery.

-- --------------------------------------------------------------------------
-- rpt_headcount_trend  (HR analyst)
-- --------------------------------------------------------------------------

CREATE OR REPLACE VIEW intus.gold.rpt_headcount_trend AS
WITH bounds AS (
    SELECT min(hire_date) AS earliest, max(coalesce(termination_date, valid_from)) AS latest
    FROM intus.silver.dim_employee
    WHERE employee_key <> -1
),
month_spine AS (
    SELECT explode(sequence(
        trunc(bounds.earliest, 'MM'),
        trunc(bounds.latest, 'MM'),
        INTERVAL 1 MONTH
    )) AS month_start
    FROM bounds
),
department_months AS (
    SELECT department.department_code, department.department_name, spine.month_start,
           date_sub(add_months(spine.month_start, 1), 1) AS month_end
    FROM month_spine AS spine
    CROSS JOIN intus.silver.dim_department AS department
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
    JOIN intus.silver.dim_employee AS employee
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

COMMENT ON VIEW intus.gold.rpt_headcount_trend IS
    'Monthly headcount by department for the HR analyst persona: month-over-month change and a 3-month rolling average.';

-- --------------------------------------------------------------------------
-- rpt_attrition_by_department  (HR analyst / exec)
-- --------------------------------------------------------------------------

CREATE OR REPLACE VIEW intus.gold.rpt_attrition_by_department AS
WITH as_of AS (
    SELECT max(valid_from) AS reporting_date FROM intus.silver.dim_employee WHERE employee_key <> -1
),
window_bounds AS (
    SELECT reporting_date, add_months(reporting_date, -12) AS window_start
    FROM as_of
),
leavers AS (
    SELECT department_code, count(DISTINCT employee_id) AS terminations
    FROM intus.silver.dim_employee, window_bounds
    WHERE employee_key <> -1
      AND is_current
      AND termination_date IS NOT NULL
      AND termination_date BETWEEN window_bounds.window_start AND window_bounds.reporting_date
    GROUP BY department_code
),
-- Same DISTINCT-employee counting as the Postgres original (D-021) — this is
-- ported logic, not a fresh design, so the 4883%-attrition bug that logic
-- fixed does not need re-discovering here.
headcount_at_window_start AS (
    SELECT department_code, count(DISTINCT employee_id) AS headcount
    FROM intus.silver.dim_employee, window_bounds
    WHERE employee_key <> -1
      AND valid_from <= window_bounds.window_start
      AND (valid_to IS NULL OR window_bounds.window_start < valid_to)
    GROUP BY department_code
),
headcount_at_reporting_date AS (
    SELECT department_code, count(DISTINCT employee_id) AS headcount
    FROM intus.silver.dim_employee, window_bounds
    WHERE employee_key <> -1
      AND valid_from <= window_bounds.reporting_date
      AND (valid_to IS NULL OR window_bounds.reporting_date < valid_to)
    GROUP BY department_code
),
average_headcount AS (
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
FROM intus.silver.dim_department AS department
JOIN average_headcount ON average_headcount.department_code = department.department_code
LEFT JOIN leavers ON leavers.department_code = department.department_code
WHERE department.department_key <> -1
ORDER BY attrition_rank;

COMMENT ON VIEW intus.gold.rpt_attrition_by_department IS
    'Trailing-12-month attrition rate by department, ranked highest to lowest.';

-- --------------------------------------------------------------------------
-- rpt_sales_pipeline_by_rep  (sales ops)
-- --------------------------------------------------------------------------

CREATE OR REPLACE VIEW intus.gold.rpt_sales_pipeline_by_rep AS
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
    FROM intus.silver.fact_opportunity AS opportunity
    JOIN intus.silver.dim_employee AS employee ON employee.employee_key = opportunity.owner_employee_key
    JOIN intus.silver.dim_account AS account ON account.account_key = opportunity.account_key
    JOIN intus.silver.dim_date AS date ON date.date_key = opportunity.created_date_key
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

COMMENT ON VIEW intus.gold.rpt_sales_pipeline_by_rep IS
    'Open opportunities per rep with a running pipeline total and a rank leaderboard.';

-- --------------------------------------------------------------------------
-- rpt_revenue_trend  (FP&A / exec)
-- --------------------------------------------------------------------------

CREATE OR REPLACE VIEW intus.gold.rpt_revenue_trend AS
WITH month_end_dates AS (
    SELECT DISTINCT date_sub(add_months(trunc(date.full_date, 'MM'), 1), 1) AS month_end
    FROM intus.silver.dim_date AS date
    JOIN intus.silver.fact_subscription AS subscription ON subscription.start_date_key = date.date_key
),
-- Resolves each month_end's own date_key via a join rather than the
-- correlated subquery Postgres uses for the same lookup — see this file's
-- header for why that shape doesn't port unchanged.
month_ends AS (
    SELECT month_end_dates.month_end, month_end_date.date_key AS month_end_date_key
    FROM month_end_dates
    JOIN intus.silver.dim_date AS month_end_date ON month_end_date.full_date = month_end_dates.month_end
),
monthly_arr AS (
    SELECT
        month_ends.month_end,
        sum(subscription.arr_usd) AS total_arr_usd
    FROM month_ends
    JOIN intus.silver.fact_subscription AS subscription
      ON subscription.start_date_key <= month_ends.month_end_date_key
     AND (
         subscription.end_date_key IS NULL
         OR subscription.end_date_key > month_ends.month_end_date_key
     )
    GROUP BY month_ends.month_end
),
-- Same nested-window restriction as Postgres ("window function calls cannot
-- be nested") — net_new_arr_usd is materialised here before the running
-- SUM() below reads it as a plain column, identical reasoning, same fix.
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

COMMENT ON VIEW intus.gold.rpt_revenue_trend IS
    'Monthly ARR with period-over-period growth and cumulative net-new ARR.';

-- --------------------------------------------------------------------------
-- rpt_product_usage_trend  (product / exec)
-- --------------------------------------------------------------------------

CREATE OR REPLACE VIEW intus.gold.rpt_product_usage_trend AS
WITH daily AS (
    SELECT
        date.full_date,
        product.product_code,
        product.product_name,
        sum(usage.active_users) AS active_users,
        sum(usage.sessions) AS sessions
    FROM intus.silver.fact_usage_daily AS usage
    JOIN intus.silver.dim_date AS date ON date.date_key = usage.date_key
    JOIN intus.silver.dim_product AS product ON product.product_key = usage.product_key
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

COMMENT ON VIEW intus.gold.rpt_product_usage_trend IS
    'Daily active users and sessions by product, with a 7-day moving average and week-over-week comparison.';

-- --------------------------------------------------------------------------
-- rpt_ai_cost_by_department  (IT / FP&A — AI cost governance)
-- --------------------------------------------------------------------------

CREATE OR REPLACE VIEW intus.gold.rpt_ai_cost_by_department AS
WITH monthly_cost AS (
    SELECT
        date.fiscal_period,
        department.department_code,
        department.department_name,
        sum(usage.cost_usd) AS total_cost_usd,
        count(*) AS request_count
    FROM intus.silver.fact_ai_usage AS usage
    JOIN intus.silver.dim_date AS date ON date.date_key = usage.date_key
    JOIN intus.silver.dim_department AS department ON department.department_key = usage.department_key
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

COMMENT ON VIEW intus.gold.rpt_ai_cost_by_department IS
    'Monthly AI usage cost by department: share of that month''s total spend and a within-month rank.';

-- --------------------------------------------------------------------------
-- rpt_budget_variance  (FP&A)
-- --------------------------------------------------------------------------

CREATE OR REPLACE VIEW intus.gold.rpt_budget_variance AS
WITH budget_by_period AS (
    SELECT department_key, fiscal_period, sum(budget_usd) AS budget_usd
    FROM intus.silver.fact_budget
    GROUP BY department_key, fiscal_period
),
actual_by_period AS (
    SELECT department_key, fiscal_period, sum(amount_usd) AS actual_usd
    FROM intus.silver.fact_gl_actual
    GROUP BY department_key, fiscal_period
),
combined AS (
    SELECT
        department.department_code,
        department.department_name,
        coalesce(budget_by_period.fiscal_period, actual_by_period.fiscal_period) AS fiscal_period,
        coalesce(budget_by_period.budget_usd, 0) AS budget_usd,
        coalesce(actual_by_period.actual_usd, 0) AS actual_usd
    FROM intus.silver.dim_department AS department
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
        percent_rank() OVER (PARTITION BY fiscal_period ORDER BY actual_usd - budget_usd),
        3
    ) AS overspend_percentile_in_period
FROM combined
ORDER BY department_code, fiscal_period;

COMMENT ON VIEW intus.gold.rpt_budget_variance IS
    'Budget vs. actual by department and fiscal period: cumulative variance and each department''s overspend percentile among its peers that period.';
