-- fact_opportunity: pipeline and closed deals, with two rules of different
-- severity — one an unattributable row, the other an implausible date the
-- warehouse has no business correcting.

CREATE TEMP TABLE tmp_opportunity_source ON COMMIT DROP AS
SELECT
    opportunity_id,
    account_id,
    owner_employee_id,
    product_code,
    nullif(opportunity_type, '')      AS opportunity_type,
    created_date::date                AS created_date,
    nullif(close_date, '')::date      AS close_date,
    nullif(stage, '')                 AS stage,
    amount_usd::numeric               AS amount_usd,
    nullif(probability_pct, '')::smallint AS probability_pct,
    nullif(is_won, '')::boolean       AS is_won
FROM staging.crm_opportunity;

-- --------------------------------------------------------------------------
-- Rule CRM_ORPHAN_OPPORTUNITY — error, rejected
-- --------------------------------------------------------------------------

-- A deal cannot be attributed to a customer that does not exist in the
-- account extract; unlike an orphan manager in HR, there is no lesser fact
-- here to preserve by falling back to an unknown member. Rejected outright.
INSERT INTO warehouse.dq_exception (
    run_id, dataset, rule_code, severity, disposition, target_key, detail
)
SELECT
    current_setting('intus.run_id')::bigint,
    'crm_opportunity',
    'CRM_ORPHAN_OPPORTUNITY',
    'error',
    'rejected',
    source.opportunity_id,
    'account_id ' || source.account_id || ' is not in the account extract'
FROM tmp_opportunity_source AS source
WHERE NOT EXISTS (
    SELECT 1 FROM warehouse.dim_account AS account WHERE account.account_id = source.account_id
);

-- --------------------------------------------------------------------------
-- Rule CRM_CLOSED_BEFORE_CREATED — error, flagged
-- --------------------------------------------------------------------------

-- A chronologically impossible pair of dates, but still a real deal with a
-- real amount; dropping it would erase pipeline value over a date typo the
-- warehouse cannot know how to correct.
INSERT INTO warehouse.dq_exception (
    run_id, dataset, rule_code, severity, disposition, target_key, detail
)
SELECT
    current_setting('intus.run_id')::bigint,
    'crm_opportunity',
    'CRM_CLOSED_BEFORE_CREATED',
    'error',
    'flagged',
    opportunity_id,
    'close_date ' || close_date || ' precedes created_date ' || created_date
FROM tmp_opportunity_source
WHERE close_date IS NOT NULL AND close_date < created_date;

TRUNCATE warehouse.fact_opportunity;

INSERT INTO warehouse.fact_opportunity (
    opportunity_id, account_key, owner_employee_key, product_key,
    created_date_key, close_date_key, opportunity_type, stage, amount_usd,
    probability_pct, is_won
)
SELECT
    source.opportunity_id,
    account.account_key,
    coalesce(
        warehouse.employee_key_best(source.owner_employee_id, source.created_date), -1
    ),
    coalesce(product.product_key, -1),
    (to_char(source.created_date, 'YYYYMMDD'))::integer,
    CASE WHEN source.close_date IS NOT NULL THEN (to_char(source.close_date, 'YYYYMMDD'))::integer END,
    source.opportunity_type,
    source.stage,
    source.amount_usd,
    source.probability_pct,
    source.is_won
FROM tmp_opportunity_source AS source
-- Inner join: this is where CRM_ORPHAN_OPPORTUNITY rows are actually
-- excluded. The rule above records the exception; this join enforces it.
JOIN warehouse.dim_account AS account ON account.account_id = source.account_id
LEFT JOIN warehouse.dim_product AS product ON product.product_code = source.product_code;
