-- fact_invoice: billing history, plus the negative-amount rule.

CREATE TEMP TABLE tmp_invoice_source ON COMMIT DROP AS
SELECT
    invoice_id,
    account_id,
    subscription_id,
    issue_date::date               AS issue_date,
    due_date::date                 AS due_date,
    nullif(paid_date, '')::date    AS paid_date,
    amount_usd::numeric            AS amount_usd,
    nullif(currency, '')           AS currency,
    nullif(status, '')             AS status
FROM staging.crm_invoice;

-- --------------------------------------------------------------------------
-- Rule CRM_NEGATIVE_INVOICE — error, flagged
-- --------------------------------------------------------------------------

-- Flagged rather than repaired: flipping the sign back would be a guess
-- about which value is the mistake. A genuine credit note also has a
-- negative amount, so the rule fires on the combination — negative *and* a
-- status that does not say so — rather than on sign alone, which would
-- misclassify every real credit as corrupt.
INSERT INTO warehouse.dq_exception (
    run_id, dataset, rule_code, severity, disposition, target_key, detail
)
SELECT
    current_setting('intus.run_id')::bigint,
    'crm_invoice',
    'CRM_NEGATIVE_INVOICE',
    'error',
    'flagged',
    invoice_id,
    'amount_usd ' || amount_usd || ' is negative with status ' || coalesce(status, 'NULL')
FROM tmp_invoice_source
WHERE amount_usd < 0
  AND coalesce(status, '') NOT ILIKE '%credit%';

TRUNCATE warehouse.fact_invoice;

INSERT INTO warehouse.fact_invoice (
    invoice_id, account_key, subscription_id, issue_date_key, due_date_key,
    paid_date_key, amount_usd, currency, status
)
SELECT
    source.invoice_id,
    coalesce(account.account_key, -1),
    source.subscription_id,
    (to_char(source.issue_date, 'YYYYMMDD'))::integer,
    (to_char(source.due_date, 'YYYYMMDD'))::integer,
    CASE WHEN source.paid_date IS NOT NULL THEN (to_char(source.paid_date, 'YYYYMMDD'))::integer END,
    source.amount_usd,
    source.currency,
    source.status
FROM tmp_invoice_source AS source
LEFT JOIN warehouse.dim_account AS account ON account.account_id = source.account_id;
