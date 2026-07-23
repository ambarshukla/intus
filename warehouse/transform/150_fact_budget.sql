-- fact_budget: the approved operating budget, and a segregation-of-duties
-- check on who approved it.

CREATE TEMP TABLE tmp_budget_source ON COMMIT DROP AS
SELECT
    budget_id,
    fiscal_period,
    fiscal_year::smallint             AS fiscal_year,
    fiscal_quarter::smallint          AS fiscal_quarter,
    cost_center,
    department_code,
    nullif(gl_account, '')            AS gl_account,
    nullif(gl_account_name, '')       AS gl_account_name,
    budget_usd::numeric               AS budget_usd,
    approved_by,
    approved_date::date               AS approved_date
FROM staging.fin_budget;

-- --------------------------------------------------------------------------
-- Rule FIN_UNAUTHORISED_APPROVER — error, flagged
-- --------------------------------------------------------------------------

-- approved_by names someone outside the employee population entirely, not
-- merely someone not yet a version on this date, so this checks existence in
-- dim_employee directly rather than going through employee_key_as_of. Kept
-- and mapped to the unknown member, not rejected: the approved amount is a
-- real budget line regardless of who is recorded as approving it, and the
-- finding — a budget with no legitimate approver — is exactly the evidence
-- an access review needs to see, not something to make disappear.
INSERT INTO warehouse.dq_exception (
    run_id, dataset, rule_code, severity, disposition, target_key, detail
)
SELECT
    current_setting('intus.run_id')::bigint,
    'fin_budget',
    'FIN_UNAUTHORISED_APPROVER',
    'error',
    'flagged',
    source.budget_id,
    'approved_by ' || source.approved_by || ' is not a known employee'
FROM tmp_budget_source AS source
WHERE NOT EXISTS (
    SELECT 1 FROM warehouse.dim_employee AS person WHERE person.employee_id = source.approved_by
);

TRUNCATE warehouse.fact_budget;

INSERT INTO warehouse.fact_budget (
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
    coalesce(warehouse.employee_key_best(source.approved_by, source.approved_date), -1),
    (to_char(source.approved_date, 'YYYYMMDD'))::integer
FROM tmp_budget_source AS source
LEFT JOIN warehouse.dim_department AS department
       ON department.department_code = source.department_code;
