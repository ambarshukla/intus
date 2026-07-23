-- Silver facts: the DML half of the star schema's fact side — the
-- lakehouse's equivalent of `warehouse/transform/{60..150}_fact_*.sql`,
-- ported file-for-file in the same order (including the numeric gap between
-- fact_subscription and fact_invoice, preserved for the same reason: invoice
-- references subscription_id as a degenerate column, so subscription must
-- load first).
--
-- Runs after 21_silver_dimensions.sql. Every fact here is truncate-and-reload,
-- same reasoning as Postgres: nothing downstream references a fact row by
-- surrogate key, so there is no key-stability requirement forcing a MERGE.

-- --------------------------------------------------------------------------
-- fact_compensation
-- --------------------------------------------------------------------------

CREATE OR REPLACE TEMPORARY VIEW tmp_compensation_source AS
SELECT
    compensation_id,
    employee_id,
    CAST(effective_from AS DATE)              AS effective_from,
    nullif(pay_grade, '')                     AS pay_grade,
    CAST(annual_salary_usd AS DECIMAL(18, 6)) AS annual_salary_usd,
    CAST(bonus_target_pct AS DECIMAL(18, 6))  AS bonus_target_pct,
    CAST(nullif(equity_units, '') AS INT)     AS equity_units,
    nullif(currency, '')                      AS currency,
    nullif(change_reason, '')                 AS change_reason
FROM intus.bronze.hr_compensation;

-- ----------------------------------------------------------------------
-- Rule HR_SALARY_OUTLIER — error, flagged
-- ----------------------------------------------------------------------
-- The extract's own median, not a fixed threshold — same reasoning as
-- Postgres. `percentile_cont(...) WITHIN GROUP (ORDER BY ...)` is identical
-- syntax on this platform; `CROSS JOIN LATERAL` ported as-is too, both
-- confirmed live rather than assumed.
INSERT INTO intus.silver.dq_exception (
    dataset, rule_code, severity, disposition, target_key, detail, detected_at
)
SELECT
    'hr_compensation',
    'HR_SALARY_OUTLIER',
    'error',
    'flagged',
    compensation_id,
    concat('annual_salary_usd ', annual_salary_usd, ' exceeds 5x the extract median of ',
        round(median.value, 2)),
    current_timestamp()
FROM tmp_compensation_source
CROSS JOIN LATERAL (
    SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY annual_salary_usd) AS value
    FROM tmp_compensation_source
) AS median
WHERE annual_salary_usd > 5 * median.value;

TRUNCATE TABLE intus.silver.fact_compensation;

INSERT INTO intus.silver.fact_compensation (
    compensation_id, employee_key, date_key, pay_grade, annual_salary_usd,
    bonus_target_pct, equity_units, currency, change_reason
)
SELECT
    source.compensation_id,
    coalesce(intus.silver.employee_key_best(source.employee_id, source.effective_from), -1),
    CAST(date_format(source.effective_from, 'yyyyMMdd') AS INT),
    source.pay_grade,
    source.annual_salary_usd,
    source.bonus_target_pct,
    source.equity_units,
    source.currency,
    source.change_reason
FROM tmp_compensation_source AS source;

-- --------------------------------------------------------------------------
-- fact_performance_review — no seeded defects, no rule section
-- --------------------------------------------------------------------------

CREATE OR REPLACE TEMPORARY VIEW tmp_review_source AS
SELECT
    review_id,
    employee_id,
    nullif(reviewer_id, '')                AS reviewer_id,
    CAST(submitted_date AS DATE)           AS submitted_date,
    nullif(review_period, '')              AS review_period,
    CAST(rating AS SMALLINT)               AS rating,
    nullif(rating_label, '')               AS rating_label,
    CAST(promotion_recommended AS BOOLEAN) AS promotion_recommended
FROM intus.bronze.hr_performance_review;

TRUNCATE TABLE intus.silver.fact_performance_review;

INSERT INTO intus.silver.fact_performance_review (
    review_id, employee_key, reviewer_employee_key, date_key,
    review_period, rating, rating_label, promotion_recommended
)
SELECT
    source.review_id,
    coalesce(intus.silver.employee_key_best(source.employee_id, source.submitted_date), -1),
    CASE
        WHEN source.reviewer_id IS NOT NULL
        THEN intus.silver.employee_key_best(source.reviewer_id, source.submitted_date)
    END,
    CAST(date_format(source.submitted_date, 'yyyyMMdd') AS INT),
    source.review_period,
    source.rating,
    source.rating_label,
    source.promotion_recommended
FROM tmp_review_source AS source;

-- --------------------------------------------------------------------------
-- fact_subscription — also defect-free; must run before fact_invoice
-- --------------------------------------------------------------------------

CREATE OR REPLACE TEMPORARY VIEW tmp_subscription_source AS
SELECT
    subscription_id,
    account_id,
    product_code,
    CAST(start_date AS DATE)              AS start_date,
    CAST(nullif(end_date, '') AS DATE)    AS end_date,
    CAST(seats AS INT)                    AS seats,
    CAST(arr_usd AS DECIMAL(18, 6))       AS arr_usd,
    nullif(billing_frequency, '')         AS billing_frequency
FROM intus.bronze.crm_subscription;

TRUNCATE TABLE intus.silver.fact_subscription;

INSERT INTO intus.silver.fact_subscription (
    subscription_id, account_key, product_key, start_date_key, end_date_key,
    seats, arr_usd, billing_frequency
)
SELECT
    source.subscription_id,
    coalesce(account.account_key, -1),
    coalesce(product.product_key, -1),
    CAST(date_format(source.start_date, 'yyyyMMdd') AS INT),
    CASE WHEN source.end_date IS NOT NULL THEN CAST(date_format(source.end_date, 'yyyyMMdd') AS INT) END,
    source.seats,
    source.arr_usd,
    source.billing_frequency
FROM tmp_subscription_source AS source
LEFT JOIN intus.silver.dim_account AS account ON account.account_id = source.account_id
LEFT JOIN intus.silver.dim_product AS product ON product.product_code = source.product_code;

-- --------------------------------------------------------------------------
-- fact_invoice
-- --------------------------------------------------------------------------

CREATE OR REPLACE TEMPORARY VIEW tmp_invoice_source AS
SELECT
    invoice_id,
    account_id,
    subscription_id,
    CAST(issue_date AS DATE)             AS issue_date,
    CAST(due_date AS DATE)               AS due_date,
    CAST(nullif(paid_date, '') AS DATE)  AS paid_date,
    CAST(amount_usd AS DECIMAL(18, 6))   AS amount_usd,
    nullif(currency, '')                 AS currency,
    nullif(status, '')                   AS status
FROM intus.bronze.crm_invoice;

-- ----------------------------------------------------------------------
-- Rule CRM_NEGATIVE_INVOICE — error, flagged
-- ----------------------------------------------------------------------
INSERT INTO intus.silver.dq_exception (
    dataset, rule_code, severity, disposition, target_key, detail, detected_at
)
SELECT
    'crm_invoice',
    'CRM_NEGATIVE_INVOICE',
    'error',
    'flagged',
    invoice_id,
    concat('amount_usd ', amount_usd, ' is negative with status ', coalesce(status, 'NULL')),
    current_timestamp()
FROM tmp_invoice_source
WHERE amount_usd < 0
  AND coalesce(status, '') NOT ILIKE '%credit%';

TRUNCATE TABLE intus.silver.fact_invoice;

INSERT INTO intus.silver.fact_invoice (
    invoice_id, account_key, subscription_id, issue_date_key, due_date_key,
    paid_date_key, amount_usd, currency, status
)
SELECT
    source.invoice_id,
    coalesce(account.account_key, -1),
    source.subscription_id,
    CAST(date_format(source.issue_date, 'yyyyMMdd') AS INT),
    CAST(date_format(source.due_date, 'yyyyMMdd') AS INT),
    CASE WHEN source.paid_date IS NOT NULL THEN CAST(date_format(source.paid_date, 'yyyyMMdd') AS INT) END,
    source.amount_usd,
    source.currency,
    source.status
FROM tmp_invoice_source AS source
LEFT JOIN intus.silver.dim_account AS account ON account.account_id = source.account_id;

-- --------------------------------------------------------------------------
-- fact_opportunity
-- --------------------------------------------------------------------------

CREATE OR REPLACE TEMPORARY VIEW tmp_opportunity_source AS
SELECT
    opportunity_id,
    account_id,
    owner_employee_id,
    product_code,
    nullif(opportunity_type, '')          AS opportunity_type,
    CAST(created_date AS DATE)            AS created_date,
    CAST(nullif(close_date, '') AS DATE)  AS close_date,
    nullif(stage, '')                     AS stage,
    CAST(amount_usd AS DECIMAL(18, 6))    AS amount_usd,
    CAST(nullif(probability_pct, '') AS SMALLINT) AS probability_pct,
    CAST(nullif(is_won, '') AS BOOLEAN)   AS is_won
FROM intus.bronze.crm_opportunity;

-- ----------------------------------------------------------------------
-- Rule CRM_ORPHAN_OPPORTUNITY — error, rejected
-- ----------------------------------------------------------------------
INSERT INTO intus.silver.dq_exception (
    dataset, rule_code, severity, disposition, target_key, detail, detected_at
)
SELECT
    'crm_opportunity',
    'CRM_ORPHAN_OPPORTUNITY',
    'error',
    'rejected',
    source.opportunity_id,
    concat('account_id ', source.account_id, ' is not in the account extract'),
    current_timestamp()
FROM tmp_opportunity_source AS source
WHERE NOT EXISTS (
    SELECT 1 FROM intus.silver.dim_account AS account WHERE account.account_id = source.account_id
);

-- ----------------------------------------------------------------------
-- Rule CRM_CLOSED_BEFORE_CREATED — error, flagged
-- ----------------------------------------------------------------------
INSERT INTO intus.silver.dq_exception (
    dataset, rule_code, severity, disposition, target_key, detail, detected_at
)
SELECT
    'crm_opportunity',
    'CRM_CLOSED_BEFORE_CREATED',
    'error',
    'flagged',
    opportunity_id,
    concat('close_date ', close_date, ' precedes created_date ', created_date),
    current_timestamp()
FROM tmp_opportunity_source
WHERE close_date IS NOT NULL AND close_date < created_date;

TRUNCATE TABLE intus.silver.fact_opportunity;

INSERT INTO intus.silver.fact_opportunity (
    opportunity_id, account_key, owner_employee_key, product_key,
    created_date_key, close_date_key, opportunity_type, stage, amount_usd,
    probability_pct, is_won
)
SELECT
    source.opportunity_id,
    account.account_key,
    coalesce(
        intus.silver.employee_key_best(source.owner_employee_id, source.created_date), -1
    ),
    coalesce(product.product_key, -1),
    CAST(date_format(source.created_date, 'yyyyMMdd') AS INT),
    CASE WHEN source.close_date IS NOT NULL THEN CAST(date_format(source.close_date, 'yyyyMMdd') AS INT) END,
    source.opportunity_type,
    source.stage,
    source.amount_usd,
    source.probability_pct,
    source.is_won
FROM tmp_opportunity_source AS source
-- Inner join: this is where CRM_ORPHAN_OPPORTUNITY rows are actually
-- excluded. The rule above records the exception; this join enforces it.
JOIN intus.silver.dim_account AS account ON account.account_id = source.account_id
LEFT JOIN intus.silver.dim_product AS product ON product.product_code = source.product_code;

-- --------------------------------------------------------------------------
-- fact_usage_daily — largest fact by row count, most rule traffic
-- --------------------------------------------------------------------------

CREATE OR REPLACE TEMPORARY VIEW tmp_usage_source AS
SELECT
    CAST(usage_date AS DATE)      AS usage_date,
    account_id,
    product_code,
    CAST(active_users AS INT)     AS active_users,
    CAST(sessions AS INT)         AS sessions,
    CAST(api_calls AS INT)        AS api_calls,
    CAST(storage_gb AS DECIMAL(18, 6)) AS storage_gb,
    CAST(avg_latency_ms AS INT)   AS avg_latency_ms,
    CAST(error_count AS INT)      AS error_count
FROM intus.bronze.usage_daily;

-- ----------------------------------------------------------------------
-- Rule USAGE_DUPLICATE_EVENT — error, rejected
-- ----------------------------------------------------------------------
INSERT INTO intus.silver.dq_exception (
    dataset, rule_code, severity, disposition, target_key, detail, detected_at
)
SELECT
    'usage_daily',
    'USAGE_DUPLICATE_EVENT',
    'error',
    'rejected',
    concat(usage_date, '|', account_id, '|', product_code),
    concat('grain repeated ', count(*), ' times in the extract; kept 1, rejected ', count(*) - 1),
    current_timestamp()
FROM tmp_usage_source
GROUP BY usage_date, account_id, product_code
HAVING count(*) > 1;

-- ----------------------------------------------------------------------
-- Rule USAGE_UNKNOWN_ACCOUNT — error, rejected
-- ----------------------------------------------------------------------
INSERT INTO intus.silver.dq_exception (
    dataset, rule_code, severity, disposition, target_key, detail, detected_at
)
SELECT
    'usage_daily',
    'USAGE_UNKNOWN_ACCOUNT',
    'error',
    'rejected',
    concat(source.usage_date, '|', source.account_id, '|', source.product_code),
    concat('account_id ', source.account_id, ' is not in the account extract'),
    current_timestamp()
FROM (SELECT DISTINCT usage_date, account_id, product_code FROM tmp_usage_source) AS source
WHERE NOT EXISTS (
    SELECT 1 FROM intus.silver.dim_account AS account WHERE account.account_id = source.account_id
);

-- ----------------------------------------------------------------------
-- Rule USAGE_NEGATIVE_SESSIONS — error, flagged
-- ----------------------------------------------------------------------
INSERT INTO intus.silver.dq_exception (
    dataset, rule_code, severity, disposition, target_key, detail, detected_at
)
SELECT
    'usage_daily',
    'USAGE_NEGATIVE_SESSIONS',
    'error',
    'flagged',
    concat(usage_date, '|', account_id, '|', product_code),
    concat('sessions ', sessions, ' is negative'),
    current_timestamp()
FROM tmp_usage_source
WHERE sessions < 0;

-- QUALIFY + ROW_NUMBER, same DISTINCT ON substitute as dim_account.
CREATE OR REPLACE TEMPORARY VIEW tmp_usage_final AS
SELECT
    usage_date, account_id, product_code, active_users, sessions, api_calls,
    storage_gb, avg_latency_ms, error_count
FROM tmp_usage_source
WHERE EXISTS (
    SELECT 1 FROM intus.silver.dim_account AS account WHERE account.account_id = tmp_usage_source.account_id
)
QUALIFY row_number() OVER (
    PARTITION BY usage_date, account_id, product_code ORDER BY active_users, sessions
) = 1;

TRUNCATE TABLE intus.silver.fact_usage_daily;

INSERT INTO intus.silver.fact_usage_daily (
    date_key, account_key, product_key, active_users, sessions, api_calls,
    storage_gb, avg_latency_ms, error_count
)
SELECT
    CAST(date_format(source.usage_date, 'yyyyMMdd') AS INT),
    account.account_key,
    coalesce(product.product_key, -1),
    source.active_users,
    source.sessions,
    source.api_calls,
    source.storage_gb,
    source.avg_latency_ms,
    source.error_count
FROM tmp_usage_final AS source
JOIN intus.silver.dim_account AS account ON account.account_id = source.account_id
LEFT JOIN intus.silver.dim_product AS product ON product.product_code = source.product_code;

-- --------------------------------------------------------------------------
-- fact_ai_usage — internal LLM usage and cost, two governance rules
-- --------------------------------------------------------------------------

CREATE OR REPLACE TEMPORARY VIEW tmp_ai_usage_source AS
SELECT
    event_id,
    employee_id,
    department_code,
    CAST(event_ts AS TIMESTAMP)         AS event_ts,
    nullif(model, '')                   AS model,
    nullif(feature, '')                 AS feature,
    CAST(prompt_tokens AS INT)          AS prompt_tokens,
    CAST(completion_tokens AS INT)      AS completion_tokens,
    CAST(cost_usd AS DECIMAL(18, 6))    AS cost_usd,
    CAST(latency_ms AS INT)             AS latency_ms,
    CAST(flagged_by_policy AS BOOLEAN)  AS flagged_by_policy
FROM intus.bronze.ai_usage_event;

-- ----------------------------------------------------------------------
-- Rule AI_COST_MISMATCH — error, flagged
-- ----------------------------------------------------------------------
-- A second copy of MODELS in intus_gen.domains.ai_usage, same reasoning as
-- Postgres (D-010, "duplicate small reference data, test for drift") —
-- see tests/test_dq.py::test_ai_pricing_matches_the_generator there, and
-- lakehouse/tests/test_silver_dq_rates.py here for the same drift check
-- against this copy.
INSERT INTO intus.silver.dq_exception (
    dataset, rule_code, severity, disposition, target_key, detail, detected_at
)
SELECT
    'ai_usage_event',
    'AI_COST_MISMATCH',
    'error',
    'flagged',
    source.event_id,
    concat('cost_usd ', source.cost_usd, ' does not reconcile to ',
        round(expected.cost_usd, 6), ' from token counts at the known rate'),
    current_timestamp()
FROM tmp_ai_usage_source AS source
JOIN (
    VALUES
        ('atlas-large', 0.0030, 0.0150),
        ('atlas-mini',  0.0008, 0.0040),
        ('orion-pro',   0.0050, 0.0200),
        ('orion-lite',  0.0004, 0.0016)
) AS rates (model, input_usd_per_1k, output_usd_per_1k)
  ON rates.model = source.model
CROSS JOIN LATERAL (
    SELECT source.prompt_tokens / 1000.0 * rates.input_usd_per_1k
         + source.completion_tokens / 1000.0 * rates.output_usd_per_1k AS cost_usd
) AS expected
WHERE abs(source.cost_usd - expected.cost_usd) > 0.10 * expected.cost_usd;

-- ----------------------------------------------------------------------
-- Rule AI_UNKNOWN_MODEL — warning, flagged
-- ----------------------------------------------------------------------
INSERT INTO intus.silver.dq_exception (
    dataset, rule_code, severity, disposition, target_key, detail, detected_at
)
SELECT
    'ai_usage_event',
    'AI_UNKNOWN_MODEL',
    'warning',
    'flagged',
    event_id,
    concat('model ', coalesce(model, 'NULL'), ' is not in the approved catalog'),
    current_timestamp()
FROM tmp_ai_usage_source
WHERE model NOT IN ('atlas-large', 'atlas-mini', 'orion-pro', 'orion-lite');

TRUNCATE TABLE intus.silver.fact_ai_usage;

INSERT INTO intus.silver.fact_ai_usage (
    event_id, employee_id, employee_key, department_key, date_key, event_ts,
    model, feature, prompt_tokens, completion_tokens, cost_usd, latency_ms,
    flagged_by_policy
)
SELECT
    source.event_id,
    source.employee_id,
    coalesce(intus.silver.employee_key_best(source.employee_id, CAST(source.event_ts AS DATE)), -1),
    coalesce(department.department_key, -1),
    CAST(date_format(source.event_ts, 'yyyyMMdd') AS INT),
    source.event_ts,
    source.model,
    source.feature,
    source.prompt_tokens,
    source.completion_tokens,
    source.cost_usd,
    source.latency_ms,
    source.flagged_by_policy
FROM tmp_ai_usage_source AS source
LEFT JOIN intus.silver.dim_department AS department
       ON department.department_code = source.department_code;

-- --------------------------------------------------------------------------
-- fact_access_event — the centrepiece governance domain, three rules
-- --------------------------------------------------------------------------

CREATE OR REPLACE TEMPORARY VIEW tmp_access_source AS
SELECT
    event_id,
    nullif(employee_id, '')          AS employee_id,
    department_code,
    CAST(event_ts AS TIMESTAMP)      AS event_ts,
    nullif(system, '')               AS system,
    nullif(action, '')               AS action,
    nullif(resource, '')             AS resource,
    nullif(source_ip, '')            AS source_ip,
    nullif(source_country, '')       AS source_country,
    nullif(result, '')               AS result,
    CAST(mfa_used AS BOOLEAN)        AS mfa_used
FROM intus.bronze.sec_access_event;

-- ----------------------------------------------------------------------
-- Rule SEC_MISSING_ACTOR — error, flagged
-- ----------------------------------------------------------------------
INSERT INTO intus.silver.dq_exception (
    dataset, rule_code, severity, disposition, target_key, detail, detected_at
)
SELECT
    'sec_access_event',
    'SEC_MISSING_ACTOR',
    'error',
    'flagged',
    event_id,
    'employee_id is NULL; event is unattributable',
    current_timestamp()
FROM tmp_access_source
WHERE employee_id IS NULL;

-- ----------------------------------------------------------------------
-- Rule SEC_LOGIN_AFTER_TERMINATION — error, flagged (the centrepiece)
-- ----------------------------------------------------------------------
-- Built directly on employee_key_as_of returning NULL, same as Postgres.
-- `event_ts::date - person.termination_date` (Postgres date subtraction
-- returns an integer day count) becomes `datediff(..., ...)` here.
INSERT INTO intus.silver.dq_exception (
    dataset, rule_code, severity, disposition, target_key, detail, detected_at
)
SELECT
    'sec_access_event',
    'SEC_LOGIN_AFTER_TERMINATION',
    'error',
    'flagged',
    source.event_id,
    concat('successful login by ', source.employee_id, ' at ', source.event_ts,
        ', ', datediff(CAST(source.event_ts AS DATE), person.termination_date),
        ' day(s) after termination on ', person.termination_date),
    current_timestamp()
FROM tmp_access_source AS source
JOIN intus.silver.dim_employee AS person
  ON person.employee_id = source.employee_id AND person.is_current
WHERE source.employee_id IS NOT NULL
  AND source.action = 'LOGIN'
  AND source.result = 'SUCCESS'
  AND person.termination_date IS NOT NULL
  AND CAST(source.event_ts AS DATE) >= person.termination_date
  AND intus.silver.employee_key_as_of(source.employee_id, CAST(source.event_ts AS DATE)) IS NULL;

-- ----------------------------------------------------------------------
-- Rule SEC_IMPOSSIBLE_TRAVEL — error, flagged
-- ----------------------------------------------------------------------
-- Same two non-obvious points as the Postgres original (see there for the
-- full argument): only the later event has to be a successful login, and it
-- is *region* that must differ, not raw country text — a second copy of
-- intus_gen.domains.access._COUNTRY_BY_REGION, kept honest by
-- lakehouse/tests/test_silver_dq_rates.py, same pattern as the AI pricing
-- table above. `extract(epoch FROM ...) / 60` becomes `timestampdiff(MINUTE,
-- ..., ...)`; `QUALIFY` + `ROW_NUMBER()` replaces `DISTINCT ON` once more.
INSERT INTO intus.silver.dq_exception (
    dataset, rule_code, severity, disposition, target_key, detail, detected_at
)
SELECT
    'sec_access_event',
    'SEC_IMPOSSIBLE_TRAVEL',
    'error',
    'flagged',
    later.event_id,
    concat(later.employee_id, ' logged in from ', later.source_country, ' (', later_region.region,
        ') ', timestampdiff(MINUTE, earlier.event_ts, later.event_ts),
        ' minute(s) after an event from ', earlier.source_country, ' (', earlier_region.region, ')'),
    current_timestamp()
FROM tmp_access_source AS later
JOIN tmp_access_source AS earlier
  ON  earlier.employee_id = later.employee_id
  AND earlier.event_id <> later.event_id
  AND earlier.event_ts < later.event_ts
  AND later.event_ts - earlier.event_ts <= INTERVAL 1 HOUR
JOIN (
    VALUES
        ('US', 'Americas'), ('CA', 'Americas'), ('BR', 'Americas'),
        ('GB', 'EMEA'), ('IE', 'EMEA'), ('NL', 'EMEA'), ('DE', 'EMEA'),
        ('SG', 'APAC'), ('AU', 'APAC'), ('IN', 'APAC'), ('JP', 'APAC')
) AS later_region (country, region) ON later_region.country = later.source_country
JOIN (
    VALUES
        ('US', 'Americas'), ('CA', 'Americas'), ('BR', 'Americas'),
        ('GB', 'EMEA'), ('IE', 'EMEA'), ('NL', 'EMEA'), ('DE', 'EMEA'),
        ('SG', 'APAC'), ('AU', 'APAC'), ('IN', 'APAC'), ('JP', 'APAC')
) AS earlier_region (country, region) ON earlier_region.country = earlier.source_country
WHERE later.employee_id IS NOT NULL
  AND later.action = 'LOGIN' AND later.result = 'SUCCESS'
  AND later_region.region <> earlier_region.region
QUALIFY row_number() OVER (
    PARTITION BY later.event_id ORDER BY later.event_ts - earlier.event_ts ASC
) = 1;

TRUNCATE TABLE intus.silver.fact_access_event;

INSERT INTO intus.silver.fact_access_event (
    event_id, employee_id, employee_key, department_key, date_key, event_ts,
    system, action, resource, source_ip, source_country, result, mfa_used
)
SELECT
    source.event_id,
    source.employee_id,
    coalesce(
        CASE
            WHEN source.employee_id IS NOT NULL
            THEN intus.silver.employee_key_best(source.employee_id, CAST(source.event_ts AS DATE))
        END,
        -1
    ),
    coalesce(department.department_key, -1),
    CAST(date_format(source.event_ts, 'yyyyMMdd') AS INT),
    source.event_ts,
    source.system,
    source.action,
    source.resource,
    source.source_ip,
    source.source_country,
    source.result,
    source.mfa_used
FROM tmp_access_source AS source
LEFT JOIN intus.silver.dim_department AS department
       ON department.department_code = source.department_code;

-- --------------------------------------------------------------------------
-- fact_gl_actual
-- --------------------------------------------------------------------------

CREATE OR REPLACE TEMPORARY VIEW tmp_gl_actual_source AS
SELECT
    actual_id,
    fiscal_period,
    CAST(posting_date AS DATE)       AS posting_date,
    cost_center,
    department_code,
    nullif(gl_account, '')           AS gl_account,
    nullif(gl_account_name, '')      AS gl_account_name,
    CAST(amount_usd AS DECIMAL(18, 6)) AS amount_usd,
    nullif(vendor, '')               AS vendor,
    nullif(description, '')          AS description,
    posted_by
FROM intus.bronze.fin_actual;

-- ----------------------------------------------------------------------
-- Rule FIN_ORPHAN_COST_CENTER — error, flagged
-- ----------------------------------------------------------------------
INSERT INTO intus.silver.dq_exception (
    dataset, rule_code, severity, disposition, target_key, detail, detected_at
)
SELECT
    'fin_actual',
    'FIN_ORPHAN_COST_CENTER',
    'error',
    'flagged',
    source.actual_id,
    concat('cost_center ', source.cost_center, ' does not match any known cost centre'),
    current_timestamp()
FROM tmp_gl_actual_source AS source
WHERE NOT EXISTS (
    SELECT 1 FROM intus.silver.dim_department AS department
    WHERE department.cost_center = source.cost_center
);

-- ----------------------------------------------------------------------
-- Rule FIN_CLOSED_PERIOD_POSTING — error, flagged
-- ----------------------------------------------------------------------
INSERT INTO intus.silver.dq_exception (
    dataset, rule_code, severity, disposition, target_key, detail, detected_at
)
SELECT
    'fin_actual',
    'FIN_CLOSED_PERIOD_POSTING',
    'error',
    'flagged',
    actual_id,
    concat('fiscal_period ', fiscal_period, ' disagrees with posting_date ', posting_date,
        ' (expected ', concat('FY', year(posting_date), '-M', date_format(posting_date, 'MM')), ')'),
    current_timestamp()
FROM tmp_gl_actual_source
WHERE fiscal_period <> concat('FY', year(posting_date), '-M', date_format(posting_date, 'MM'));

TRUNCATE TABLE intus.silver.fact_gl_actual;

INSERT INTO intus.silver.fact_gl_actual (
    actual_id, department_key, date_key, fiscal_period, gl_account,
    gl_account_name, amount_usd, vendor, description, posted_by_employee_key
)
SELECT
    source.actual_id,
    coalesce(department.department_key, -1),
    CAST(date_format(source.posting_date, 'yyyyMMdd') AS INT),
    source.fiscal_period,
    source.gl_account,
    source.gl_account_name,
    source.amount_usd,
    source.vendor,
    source.description,
    coalesce(intus.silver.employee_key_best(source.posted_by, source.posting_date), -1)
FROM tmp_gl_actual_source AS source
LEFT JOIN intus.silver.dim_department AS department
       ON department.department_code = source.department_code;

-- --------------------------------------------------------------------------
-- fact_budget
-- --------------------------------------------------------------------------

CREATE OR REPLACE TEMPORARY VIEW tmp_budget_source AS
SELECT
    budget_id,
    fiscal_period,
    CAST(fiscal_year AS SMALLINT)      AS fiscal_year,
    CAST(fiscal_quarter AS SMALLINT)   AS fiscal_quarter,
    cost_center,
    department_code,
    nullif(gl_account, '')             AS gl_account,
    nullif(gl_account_name, '')        AS gl_account_name,
    CAST(budget_usd AS DECIMAL(18, 6)) AS budget_usd,
    approved_by,
    CAST(approved_date AS DATE)        AS approved_date
FROM intus.bronze.fin_budget;

-- ----------------------------------------------------------------------
-- Rule FIN_UNAUTHORISED_APPROVER — error, flagged
-- ----------------------------------------------------------------------
INSERT INTO intus.silver.dq_exception (
    dataset, rule_code, severity, disposition, target_key, detail, detected_at
)
SELECT
    'fin_budget',
    'FIN_UNAUTHORISED_APPROVER',
    'error',
    'flagged',
    source.budget_id,
    concat('approved_by ', source.approved_by, ' is not a known employee'),
    current_timestamp()
FROM tmp_budget_source AS source
WHERE NOT EXISTS (
    SELECT 1 FROM intus.silver.dim_employee AS person WHERE person.employee_id = source.approved_by
);

TRUNCATE TABLE intus.silver.fact_budget;

INSERT INTO intus.silver.fact_budget (
    budget_id, department_key, fiscal_period, fiscal_year, fiscal_quarter,
    gl_account, gl_account_name, budget_usd, approved_by_employee_key,
    approved_date_key
)
SELECT
    source.budget_id,
    coalesce(department.department_key, -1),
    source.fiscal_period,
    source.fiscal_year,
    source.fiscal_quarter,
    source.gl_account,
    source.gl_account_name,
    source.budget_usd,
    coalesce(intus.silver.employee_key_best(source.approved_by, source.approved_date), -1),
    CAST(date_format(source.approved_date, 'yyyyMMdd') AS INT)
FROM tmp_budget_source AS source
LEFT JOIN intus.silver.dim_department AS department
       ON department.department_code = source.department_code;
