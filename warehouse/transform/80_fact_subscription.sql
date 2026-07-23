-- fact_subscription: also defect-free at the source. Its natural key,
-- subscription_id, is what fact_invoice references as a degenerate column,
-- so this must run before 90_fact_invoice.sql — the one true ordering
-- dependency between fact transforms, and the reason for the numeric gaps
-- between files (80, 90, ...) rather than tight sequential numbering.

CREATE TEMP TABLE tmp_subscription_source ON COMMIT DROP AS
SELECT
    subscription_id,
    account_id,
    product_code,
    start_date::date               AS start_date,
    nullif(end_date, '')::date     AS end_date,
    seats::integer                 AS seats,
    arr_usd::numeric               AS arr_usd,
    nullif(billing_frequency, '')  AS billing_frequency
FROM staging.crm_subscription;

TRUNCATE warehouse.fact_subscription;

INSERT INTO warehouse.fact_subscription (
    subscription_id, account_key, product_key, start_date_key, end_date_key,
    seats, arr_usd, billing_frequency
)
SELECT
    source.subscription_id,
    coalesce(account.account_key, -1),
    coalesce(product.product_key, -1),
    (to_char(source.start_date, 'YYYYMMDD'))::integer,
    CASE WHEN source.end_date IS NOT NULL THEN (to_char(source.end_date, 'YYYYMMDD'))::integer END,
    source.seats,
    source.arr_usd,
    source.billing_frequency
FROM tmp_subscription_source AS source
LEFT JOIN warehouse.dim_account AS account ON account.account_id = source.account_id
LEFT JOIN warehouse.dim_product AS product ON product.product_code = source.product_code;
