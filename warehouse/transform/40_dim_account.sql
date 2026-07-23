-- dim_account: type 1, plus duplicate detection.
--
-- Type 1 deliberately. The CRM extract carries only current account state, so
-- there is no history to preserve; modelling it as type 2 would manufacture
-- versions the source cannot substantiate. The contrast with dim_employee is
-- the point — the choice between type 1 and type 2 is made by what the source
-- can actually evidence, not by which is more sophisticated.

CREATE TEMP TABLE tmp_account_source ON COMMIT DROP AS
SELECT
    account_id,
    nullif(account_name, '')            AS account_name,
    nullif(region, '')                  AS region,
    nullif(segment, '')                 AS segment,
    nullif(industry, '')                AS industry,
    nullif(created_date, '')::date      AS created_date,
    nullif(owner_employee_id, '')       AS owner_employee_id,
    nullif(status, '')                  AS status,
    nullif(churn_date, '')::date        AS churn_date
FROM staging.crm_account;

-- --------------------------------------------------------------------------
-- Rule CRM_DUPLICATE_ACCOUNT — error, rejected
-- --------------------------------------------------------------------------

-- Staging has no primary key precisely so duplicates can arrive and be counted.
-- Only the surplus copies are rejected: one row per account still loads, so the
-- customer is not lost because the extract stuttered.
INSERT INTO warehouse.dq_exception (
    run_id, dataset, rule_code, severity, disposition, target_key, detail
)
SELECT
    current_setting('intus.run_id')::bigint,
    'crm_account',
    'CRM_DUPLICATE_ACCOUNT',
    'error',
    'rejected',
    account_id,
    'account appears ' || count(*) || ' times in the extract; kept 1, rejected '
        || (count(*) - 1)
FROM tmp_account_source
GROUP BY account_id
HAVING count(*) > 1;

CREATE TEMP TABLE tmp_account_final ON COMMIT DROP AS
SELECT DISTINCT ON (account_id)
    account_id,
    account_name,
    region,
    segment,
    industry,
    created_date,
    owner_employee_id,
    status,
    churn_date,
    -- Derived rather than trusting `status`: a churn date in the past and a
    -- status of Active would otherwise disagree, and the date is the harder
    -- fact.
    (churn_date IS NULL) AS is_active
FROM tmp_account_source
-- DISTINCT ON keeps the first row per account under this ordering. The seeded
-- duplicates are verbatim copies so any of them would do, but the ordering is
-- stated anyway: "whichever row the planner happened to emit" is not a
-- deduplication rule, and it would make the load non-deterministic the moment
-- the copies stopped being identical.
ORDER BY account_id, account_name, created_date;

MERGE INTO warehouse.dim_account AS target
USING tmp_account_final AS source
   ON target.account_id = source.account_id

WHEN MATCHED AND (
        target.account_name, target.region, target.segment, target.industry,
        target.created_date, target.owner_employee_id, target.status,
        target.churn_date, target.is_active
    ) IS DISTINCT FROM (
        source.account_name, source.region, source.segment, source.industry,
        source.created_date, source.owner_employee_id, source.status,
        source.churn_date, source.is_active
    )
    THEN UPDATE SET
        account_name      = source.account_name,
        region            = source.region,
        segment           = source.segment,
        industry          = source.industry,
        created_date      = source.created_date,
        owner_employee_id = source.owner_employee_id,
        status            = source.status,
        churn_date        = source.churn_date,
        is_active         = source.is_active

WHEN NOT MATCHED THEN
    INSERT (
        account_id, account_name, region, segment, industry, created_date,
        owner_employee_id, status, churn_date, is_active
    )
    VALUES (
        source.account_id, source.account_name, source.region, source.segment,
        source.industry, source.created_date, source.owner_employee_id,
        source.status, source.churn_date, source.is_active
    );

-- account_key = -1 is the unknown member (004_warehouse_facts.sql) and has no
-- counterpart in any extract by construction; excluded or this would delete
-- it on every run.
DELETE FROM warehouse.dim_account AS target
WHERE target.account_key <> -1
  AND NOT EXISTS (
    SELECT 1 FROM tmp_account_final AS source
    WHERE source.account_id = target.account_id
);
