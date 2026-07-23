-- fact_compensation: one row per hr_compensation record, at the grain the
-- source already carries.
--
-- Truncate-and-reload, not MERGE — and this is the point where facts
-- deliberately diverge from dimensions. Nothing downstream references
-- fact_compensation.compensation_id as a foreign key, so there is no
-- surrogate key whose stability matters. Reloading wholesale is simpler,
-- correct for a full extract, and does not need the DELETE-then-MERGE dance
-- dimensions require. Every fact transform in this file follows the same
-- shape for the same reason.

CREATE TEMP TABLE tmp_compensation_source ON COMMIT DROP AS
SELECT
    compensation_id,
    employee_id,
    effective_from::date               AS effective_from,
    nullif(pay_grade, '')               AS pay_grade,
    annual_salary_usd::numeric          AS annual_salary_usd,
    bonus_target_pct::numeric           AS bonus_target_pct,
    nullif(equity_units, '')::integer   AS equity_units,
    nullif(currency, '')                AS currency,
    nullif(change_reason, '')           AS change_reason
FROM staging.hr_compensation;

-- --------------------------------------------------------------------------
-- Rule HR_SALARY_OUTLIER — error, flagged
-- --------------------------------------------------------------------------

-- A fixed dollar threshold would be a magic number nobody could justify six
-- months from now. The extract's own median is the threshold instead:
-- percentile_cont computes it directly, so the rule stays correct however
-- the pay scale in the generator changes, and reads as what it is — "five
-- times typical" — rather than as a number someone once picked.
INSERT INTO warehouse.dq_exception (
    run_id, dataset, rule_code, severity, disposition, target_key, detail
)
SELECT
    current_setting('intus.run_id')::bigint,
    'hr_compensation',
    'HR_SALARY_OUTLIER',
    'error',
    'flagged',
    compensation_id,
    'annual_salary_usd ' || annual_salary_usd || ' exceeds 5x the extract median of '
        || median.value
FROM tmp_compensation_source
CROSS JOIN LATERAL (
    SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY annual_salary_usd) AS value
    FROM tmp_compensation_source
) AS median
WHERE annual_salary_usd > 5 * median.value;

TRUNCATE warehouse.fact_compensation;

INSERT INTO warehouse.fact_compensation (
    compensation_id, employee_key, date_key, pay_grade, annual_salary_usd,
    bonus_target_pct, equity_units, currency, change_reason
)
SELECT
    source.compensation_id,
    coalesce(warehouse.employee_key_best(source.employee_id, source.effective_from), -1),
    (to_char(source.effective_from, 'YYYYMMDD'))::integer,
    source.pay_grade,
    source.annual_salary_usd,
    source.bonus_target_pct,
    source.equity_units,
    source.currency,
    source.change_reason
FROM tmp_compensation_source AS source;
