-- fact_usage_daily: the largest fact by row count (roughly a million rows at
-- full scale), and the one with the most rule traffic — duplicate delivery,
-- a negative counter, and telemetry for a customer the CRM has never heard
-- of.

CREATE TEMP TABLE tmp_usage_source ON COMMIT DROP AS
SELECT
    usage_date::date            AS usage_date,
    account_id,
    product_code,
    active_users::integer        AS active_users,
    sessions::integer            AS sessions,
    api_calls::integer           AS api_calls,
    storage_gb::numeric          AS storage_gb,
    avg_latency_ms::integer      AS avg_latency_ms,
    error_count::integer         AS error_count
FROM staging.usage_daily;

CREATE INDEX ON tmp_usage_source (usage_date, account_id, product_code);

-- --------------------------------------------------------------------------
-- Rule USAGE_DUPLICATE_EVENT — error, rejected
-- --------------------------------------------------------------------------

-- The at-least-once delivery bug: the same day's usage landed twice. Only the
-- surplus copies count as the exception (n-1 per group), matching
-- CRM_DUPLICATE_ACCOUNT's convention — one representative row still loads.
INSERT INTO warehouse.dq_exception (
    run_id, dataset, rule_code, severity, disposition, target_key, detail
)
SELECT
    current_setting('intus.run_id')::bigint,
    'usage_daily',
    'USAGE_DUPLICATE_EVENT',
    'error',
    'rejected',
    usage_date || '|' || account_id || '|' || product_code,
    'grain repeated ' || count(*) || ' times in the extract; kept 1, rejected ' || (count(*) - 1)
FROM tmp_usage_source
GROUP BY usage_date, account_id, product_code
HAVING count(*) > 1;

-- --------------------------------------------------------------------------
-- Rule USAGE_UNKNOWN_ACCOUNT — error, rejected
-- --------------------------------------------------------------------------

INSERT INTO warehouse.dq_exception (
    run_id, dataset, rule_code, severity, disposition, target_key, detail
)
SELECT
    current_setting('intus.run_id')::bigint,
    'usage_daily',
    'USAGE_UNKNOWN_ACCOUNT',
    'error',
    'rejected',
    source.usage_date || '|' || source.account_id || '|' || source.product_code,
    'account_id ' || source.account_id || ' is not in the account extract'
FROM (SELECT DISTINCT usage_date, account_id, product_code FROM tmp_usage_source) AS source
WHERE NOT EXISTS (
    SELECT 1 FROM warehouse.dim_account AS account WHERE account.account_id = source.account_id
);

-- --------------------------------------------------------------------------
-- Rule USAGE_NEGATIVE_SESSIONS — error, flagged
-- --------------------------------------------------------------------------

-- Loaded as-is rather than corrected to abs(): a negative count from a reset
-- counter and a genuinely mis-signed value look identical from here, and
-- guessing which is which would silently rewrite telemetry.
INSERT INTO warehouse.dq_exception (
    run_id, dataset, rule_code, severity, disposition, target_key, detail
)
SELECT
    current_setting('intus.run_id')::bigint,
    'usage_daily',
    'USAGE_NEGATIVE_SESSIONS',
    'error',
    'flagged',
    usage_date || '|' || account_id || '|' || product_code,
    'sessions ' || sessions || ' is negative'
FROM tmp_usage_source
WHERE sessions < 0;

CREATE TEMP TABLE tmp_usage_final ON COMMIT DROP AS
SELECT DISTINCT ON (usage_date, account_id, product_code)
    usage_date, account_id, product_code, active_users, sessions, api_calls,
    storage_gb, avg_latency_ms, error_count
FROM tmp_usage_source
WHERE EXISTS (
    SELECT 1 FROM warehouse.dim_account AS account WHERE account.account_id = tmp_usage_source.account_id
)
-- Deterministic tie-break among duplicates, for the same reason as
-- dim_account's dedup: "whichever row the planner emits first" would make
-- the load's output depend on execution plan rather than on data.
ORDER BY usage_date, account_id, product_code, active_users, sessions;

TRUNCATE warehouse.fact_usage_daily;

INSERT INTO warehouse.fact_usage_daily (
    date_key, account_key, product_key, active_users, sessions, api_calls,
    storage_gb, avg_latency_ms, error_count
)
SELECT
    (to_char(source.usage_date, 'YYYYMMDD'))::integer,
    account.account_key,
    coalesce(product.product_key, -1),
    source.active_users,
    source.sessions,
    source.api_calls,
    source.storage_gb,
    source.avg_latency_ms,
    source.error_count
FROM tmp_usage_final AS source
JOIN warehouse.dim_account AS account ON account.account_id = source.account_id
LEFT JOIN warehouse.dim_product AS product ON product.product_code = source.product_code;
