-- fact_gl_actual: posted ledger transactions. Both rules here are the kind a
-- financial system cannot simply reject its way out of — a posting is real
-- money that has to stay reportable even when something about it is wrong,
-- which is why both dispositions are "flagged" rather than "rejected".

CREATE TEMP TABLE tmp_gl_actual_source ON COMMIT DROP AS
SELECT
    actual_id,
    fiscal_period,
    posting_date::date               AS posting_date,
    cost_center,
    department_code,
    nullif(gl_account, '')           AS gl_account,
    nullif(gl_account_name, '')      AS gl_account_name,
    amount_usd::numeric              AS amount_usd,
    nullif(vendor, '')               AS vendor,
    nullif(description, '')          AS description,
    posted_by
FROM staging.fin_actual;

-- --------------------------------------------------------------------------
-- Rule FIN_ORPHAN_COST_CENTER — error, flagged
-- --------------------------------------------------------------------------

-- The defect corrupts cost_center only; department_code (and therefore
-- department attribution) survives intact, so this is checked directly
-- against dim_department's cost_center rather than by a failed join —
-- fact_gl_actual does not carry a raw cost_center column at all, because the
-- correct value is always available via department_key. The check exists
-- purely to catch source data that disagrees with itself.
INSERT INTO warehouse.dq_exception (
    run_id, dataset, rule_code, severity, disposition, target_key, detail
)
SELECT
    current_setting('intus.run_id')::bigint,
    'fin_actual',
    'FIN_ORPHAN_COST_CENTER',
    'error',
    'flagged',
    source.actual_id,
    'cost_center ' || source.cost_center || ' does not match any known cost centre'
FROM tmp_gl_actual_source AS source
WHERE NOT EXISTS (
    SELECT 1 FROM warehouse.dim_department AS department
    WHERE department.cost_center = source.cost_center
);

-- --------------------------------------------------------------------------
-- Rule FIN_CLOSED_PERIOD_POSTING — error, flagged
-- --------------------------------------------------------------------------

-- A SOX-relevant defect, not merely a dirty one: the posting_date says one
-- period, fiscal_period says another, meaning the entry landed in a period
-- the ledger had already reported. Recomputing the period from posting_date
-- mirrors intus_gen.fiscal.period_for(); a test keeps the two in step.
INSERT INTO warehouse.dq_exception (
    run_id, dataset, rule_code, severity, disposition, target_key, detail
)
SELECT
    current_setting('intus.run_id')::bigint,
    'fin_actual',
    'FIN_CLOSED_PERIOD_POSTING',
    'error',
    'flagged',
    actual_id,
    'fiscal_period ' || fiscal_period || ' disagrees with posting_date ' || posting_date
        || ' (expected ' || ('FY' || extract(year FROM posting_date) || '-M' || to_char(posting_date, 'MM')) || ')'
FROM tmp_gl_actual_source
WHERE fiscal_period <> 'FY' || extract(year FROM posting_date) || '-M' || to_char(posting_date, 'MM');

TRUNCATE warehouse.fact_gl_actual;

INSERT INTO warehouse.fact_gl_actual (
    actual_id, department_key, date_key, fiscal_period, gl_account,
    gl_account_name, amount_usd, vendor, description, posted_by_employee_key
)
SELECT
    source.actual_id,
    coalesce(department.department_key, -1),
    (to_char(source.posting_date, 'YYYYMMDD'))::integer,
    source.fiscal_period,
    source.gl_account,
    source.gl_account_name,
    source.amount_usd,
    source.vendor,
    source.description,
    coalesce(warehouse.employee_key_best(source.posted_by, source.posting_date), -1)
FROM tmp_gl_actual_source AS source
LEFT JOIN warehouse.dim_department AS department
       ON department.department_code = source.department_code;
