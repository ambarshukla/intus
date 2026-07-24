-- Silver dimensions: the DML half of the star schema's dimension side — the
-- lakehouse's equivalent of `warehouse/transform/{10,20,30,40,50}_dim_*.sql`,
-- ported file-for-file in the same order, one dialect over.
--
-- Runs after 20_silver_schema.sql (structure) and before 22_silver_facts.sql
-- (which resolves foreign keys against the dimensions this file builds) —
-- task ordering in databricks.yml, not a transaction, does the job Postgres's
-- single-transaction migration did.
--
-- First statement truncates dq_exception: this file's rule inserts, and
-- 22_silver_facts.sql's after it, are the whole of one run's findings. See
-- 20_silver_schema.sql's header and D-025 for why there is no run_id to
-- scope by instead — Delta's own version history on this table stands in for
-- what the dropped `warehouse.transform_run` bookkeeping gave Postgres.
TRUNCATE TABLE intus.silver.dq_exception;

-- --------------------------------------------------------------------------
-- dim_date — generated, not derived
-- --------------------------------------------------------------------------
-- Same reasoning as 10_dim_date.sql: a range wider than the data avoids holes
-- where a variance report needs a zero, not an omission. `generate_series` has
-- no Databricks equivalent; `sequence()` + `explode()` is the idiomatic
-- substitute, confirmed live to produce the same one-row-per-day result.
--
-- Idempotent through MERGE ... WHEN NOT MATCHED, Delta's substitute for
-- Postgres's `ON CONFLICT DO NOTHING`: a date's attributes cannot change, so
-- a rerun should neither duplicate nor rewrite rows.

MERGE INTO intus.silver.dim_date AS target
USING (
    SELECT
        CAST(date_format(day, 'yyyyMMdd') AS INT)         AS date_key,
        day                                                AS full_date,
        CAST(year(day) AS SMALLINT)                        AS year,
        CAST(quarter(day) AS SMALLINT)                     AS quarter,
        CAST(month(day) AS SMALLINT)                       AS month,
        date_format(day, 'MMMM')                           AS month_name,
        CAST(day(day) AS SMALLINT)                         AS day_of_month,
        -- Spark's dayofweek() is 1=Sunday..7=Saturday; rotated to ISO
        -- (1=Monday..7=Sunday), confirmed against known Sunday/Thursday dates.
        CAST(((dayofweek(day) + 5) % 7) + 1 AS SMALLINT)   AS day_of_week,
        date_format(day, 'EEEE')                           AS day_name,
        -- weekofyear() is already ISO-8601 in Spark, unlike Postgres's plain
        -- `extract(week from ...)`, which happens to agree with ISO too.
        CAST(weekofyear(day) AS SMALLINT)                  AS iso_week,
        (((dayofweek(day) + 5) % 7) + 1) >= 6               AS is_weekend,
        -- Mirrors intus_gen.fiscal: fiscal year = calendar year.
        concat('FY', year(day), '-M', date_format(day, 'MM'))  AS fiscal_period,
        concat('FY', year(day), '-Q', quarter(day))             AS fiscal_quarter,
        CAST(year(day) AS SMALLINT)                              AS fiscal_year
    FROM (
        SELECT explode(sequence(DATE'2010-01-01', DATE'2035-12-31', INTERVAL 1 DAY)) AS day
    )
) AS source
ON target.date_key = source.date_key
WHEN NOT MATCHED THEN INSERT *;

-- --------------------------------------------------------------------------
-- dim_department — conformed from two sources
-- --------------------------------------------------------------------------

MERGE INTO intus.silver.dim_department AS target
USING (
    SELECT
        hr.department_code,
        hr.department_name,
        fin.cost_center
    FROM (
        SELECT DISTINCT department_code, department_name
        FROM intus.bronze.hr_employee_history
        WHERE department_code IS NOT NULL AND department_code <> ''
    ) AS hr
    LEFT JOIN (
        SELECT department_code, min(cost_center) AS cost_center
        FROM intus.bronze.fin_budget
        WHERE department_code IS NOT NULL AND department_code <> ''
        GROUP BY department_code
    ) AS fin ON fin.department_code = hr.department_code
) AS source
ON target.department_code = source.department_code

WHEN MATCHED AND (target.department_name, target.cost_center)
              IS DISTINCT FROM (source.department_name, source.cost_center)
    THEN UPDATE SET
        department_name = source.department_name,
        cost_center     = source.cost_center

WHEN NOT MATCHED THEN
    INSERT (department_code, department_name, cost_center)
    VALUES (source.department_code, source.department_name, source.cost_center);

-- --------------------------------------------------------------------------
-- dim_employee (slowly changing, type 2)
-- --------------------------------------------------------------------------
-- `CREATE OR REPLACE TEMPORARY VIEW` is this file's substitute for Postgres's
-- `CREATE TEMP TABLE ... ON COMMIT DROP` — confirmed live that a temporary
-- view created earlier in a session is visible to a MERGE issued later in the
-- same session, which is what one job-task file execution is. No `CREATE
-- INDEX` on the views either: Delta has no index concept for a query to use
-- (file-level statistics, not a page index) — the Postgres original's
-- `CREATE INDEX ... (employee_id, valid_from)` lines have nothing to port to.

CREATE OR REPLACE TEMPORARY VIEW tmp_employee_source AS
SELECT
    employee_id,
    CAST(valid_from AS DATE)                       AS valid_from,
    CAST(nullif(valid_to, '') AS DATE)              AS valid_to,
    nullif(first_name, '')                          AS first_name,
    nullif(last_name, '')                           AS last_name,
    nullif(work_email, '')                          AS work_email,
    nullif(region, '')                              AS region,
    nullif(location, '')                            AS location,
    nullif(department_code, '')                     AS department_code,
    nullif(department_name, '')                     AS department_name,
    CAST(nullif(job_level, '') AS SMALLINT)         AS job_level,
    nullif(job_title, '')                           AS job_title,
    nullif(manager_id, '')                          AS manager_id,
    nullif(employment_type, '')                     AS employment_type,
    nullif(change_reason, '')                       AS change_reason,
    CAST(nullif(hire_date, '') AS DATE)             AS hire_date,
    CAST(nullif(termination_date, '') AS DATE)      AS termination_date,
    nullif(termination_reason, '')                  AS termination_reason
FROM intus.bronze.hr_employee_history;

-- ----------------------------------------------------------------------
-- Rule HR_OVERLAPPING_SPAN — error, rejected
-- ----------------------------------------------------------------------
-- This self-join is the entire guarantee now — see 20_silver_schema.sql's
-- header and D-024. There is no exclusion constraint behind it on this
-- platform, so the query below is not a check *in addition to* a database
-- rule, it *is* the rule. The range-overlap test collapses to
-- "later starts before earlier ends" because the join already orders the
-- pair (earlier.valid_from < later.valid_from); the general two-sided
-- overlap test `daterange() && daterange()` needs both directions checked,
-- this ordered special case only needs one.
CREATE OR REPLACE TEMPORARY VIEW tmp_employee_overlap AS
SELECT DISTINCT later.employee_id, later.valid_from
FROM tmp_employee_source AS later
JOIN tmp_employee_source AS earlier
  ON  earlier.employee_id = later.employee_id
  AND earlier.valid_from  < later.valid_from
  AND (earlier.valid_to IS NULL OR later.valid_from < earlier.valid_to);

INSERT INTO intus.silver.dq_exception (
    dataset, rule_code, severity, disposition, target_key, detail, detected_at
)
SELECT
    'hr_employee_history',
    'HR_OVERLAPPING_SPAN',
    'error',
    'rejected',
    concat(employee_id, '|', valid_from),
    'SCD2 span overlaps an earlier version of the same employee',
    current_timestamp()
FROM tmp_employee_overlap;

-- ----------------------------------------------------------------------
-- Rule HR_ORPHAN_MANAGER — warning, repaired
-- ----------------------------------------------------------------------
CREATE OR REPLACE TEMPORARY VIEW tmp_employee_orphan_manager AS
SELECT source.employee_id, source.valid_from, source.manager_id
FROM tmp_employee_source AS source
WHERE source.manager_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM tmp_employee_source AS manager
      WHERE manager.employee_id = source.manager_id
  );

INSERT INTO intus.silver.dq_exception (
    dataset, rule_code, severity, disposition, target_key, detail, detected_at
)
SELECT
    'hr_employee_history',
    'HR_ORPHAN_MANAGER',
    'warning',
    'repaired',
    concat(employee_id, '|', valid_from),
    concat('manager_id ', manager_id, ' is not in the employee population; set to NULL'),
    current_timestamp()
FROM tmp_employee_orphan_manager;

-- ----------------------------------------------------------------------
-- Rule HR_MISSING_TERMINATION_REASON — warning, flagged
-- ----------------------------------------------------------------------
INSERT INTO intus.silver.dq_exception (
    dataset, rule_code, severity, disposition, target_key, detail, detected_at
)
SELECT
    'hr_employee_history',
    'HR_MISSING_TERMINATION_REASON',
    'warning',
    'flagged',
    concat(employee_id, '|', valid_from),
    concat('employee terminated on ', termination_date, ' with no reason recorded'),
    current_timestamp()
FROM tmp_employee_source
WHERE termination_date IS NOT NULL
  AND termination_reason IS NULL;

-- ----------------------------------------------------------------------
-- Validated source
-- ----------------------------------------------------------------------
CREATE OR REPLACE TEMPORARY VIEW tmp_employee_final AS
SELECT
    source.employee_id,
    source.valid_from,
    source.valid_to,
    -- Same "only one current version, over the surviving rows" rule as
    -- Postgres. There is no partial unique index behind this either (same
    -- reason as the overlap rule): this ROW_NUMBER is the whole guarantee.
    (row_number() OVER (
        PARTITION BY source.employee_id ORDER BY source.valid_from DESC
    ) = 1) AS is_current,
    source.first_name,
    source.last_name,
    concat(source.first_name, ' ', source.last_name) AS full_name,
    source.work_email,
    source.region,
    source.location,
    source.department_code,
    source.department_name,
    source.job_level,
    source.job_title,
    CASE WHEN orphan.employee_id IS NULL THEN source.manager_id END AS manager_employee_id,
    source.employment_type,
    source.change_reason,
    source.hire_date,
    source.termination_date,
    source.termination_reason
FROM tmp_employee_source AS source
LEFT JOIN tmp_employee_overlap AS overlap
       ON overlap.employee_id = source.employee_id
      AND overlap.valid_from  = source.valid_from
LEFT JOIN tmp_employee_orphan_manager AS orphan
       ON orphan.employee_id = source.employee_id
      AND orphan.valid_from  = source.valid_from
WHERE overlap.employee_id IS NULL;

-- ----------------------------------------------------------------------
-- Reconcile
-- ----------------------------------------------------------------------
-- Versions the source no longer carries. Delta MERGE supports
-- `WHEN NOT MATCHED BY SOURCE`, unlike Postgres 16 — but this stays a
-- separate DELETE anyway, for the identical reason the Postgres file gives:
-- it must run before the upsert, or a row being deleted could still collide
-- with a row being inserted under the (here, transform-only) no-overlap rule.
--
-- employee_key = -1 excluded: it has no counterpart in any extract by
-- construction, so this delete would otherwise remove it on every run.
DELETE FROM intus.silver.dim_employee AS target
WHERE target.employee_key <> -1
  AND NOT EXISTS (
    SELECT 1 FROM tmp_employee_final AS source
    WHERE source.employee_id = target.employee_id
      AND source.valid_from  = target.valid_from
);

-- Clear is_current before recomputing it, same reason as Postgres: flipping
-- the flag from an old row to a new one inside one MERGE can transiently
-- disagree with "at most one current" depending on row order, and there is
-- no partial unique index here to make that safe either way — clearing first
-- makes the outcome independent of order regardless.
UPDATE intus.silver.dim_employee SET is_current = false
WHERE is_current AND employee_key <> -1;

MERGE INTO intus.silver.dim_employee AS target
USING tmp_employee_final AS source
   ON target.employee_id = source.employee_id
  AND target.valid_from  = source.valid_from

-- A tuple/struct IS DISTINCT FROM, the shape every other MERGE's change
-- detection in this file uses (dim_department, dim_account below), fails
-- here specifically — confirmed live: `DATATYPE_MISMATCH.CAST_WITHOUT_
-- SUGGESTION`, Spark unable to unify target's struct type with source's
-- because `is_current` and `manager_employee_id` carry `COMMENT ON COLUMN`
-- metadata (added above) that the struct built from `target.*` inherits and
-- the struct built from `source.*` does not — a mismatch the other MERGEs in
-- this file never hit because none of their compared columns are commented.
-- Postgres's row-value `IS DISTINCT FROM` has no such sensitivity. Rewritten
-- as an OR-chain of scalar comparisons, which builds no struct at all.
WHEN MATCHED AND (
       target.valid_to            IS DISTINCT FROM source.valid_to
    OR target.is_current          IS DISTINCT FROM source.is_current
    OR target.first_name          IS DISTINCT FROM source.first_name
    OR target.last_name           IS DISTINCT FROM source.last_name
    OR target.full_name           IS DISTINCT FROM source.full_name
    OR target.work_email          IS DISTINCT FROM source.work_email
    OR target.region              IS DISTINCT FROM source.region
    OR target.location            IS DISTINCT FROM source.location
    OR target.department_code     IS DISTINCT FROM source.department_code
    OR target.department_name     IS DISTINCT FROM source.department_name
    OR target.job_level           IS DISTINCT FROM source.job_level
    OR target.job_title           IS DISTINCT FROM source.job_title
    OR target.manager_employee_id IS DISTINCT FROM source.manager_employee_id
    OR target.employment_type     IS DISTINCT FROM source.employment_type
    OR target.change_reason       IS DISTINCT FROM source.change_reason
    OR target.hire_date           IS DISTINCT FROM source.hire_date
    OR target.termination_date    IS DISTINCT FROM source.termination_date
    OR target.termination_reason  IS DISTINCT FROM source.termination_reason
    )
    THEN UPDATE SET
        valid_to            = source.valid_to,
        is_current          = source.is_current,
        first_name          = source.first_name,
        last_name           = source.last_name,
        full_name           = source.full_name,
        work_email          = source.work_email,
        region              = source.region,
        location            = source.location,
        department_code     = source.department_code,
        department_name     = source.department_name,
        job_level           = source.job_level,
        job_title           = source.job_title,
        manager_employee_id = source.manager_employee_id,
        employment_type     = source.employment_type,
        change_reason       = source.change_reason,
        hire_date           = source.hire_date,
        termination_date    = source.termination_date,
        termination_reason  = source.termination_reason

WHEN NOT MATCHED THEN
    INSERT (
        employee_id, valid_from, valid_to, is_current, first_name, last_name,
        full_name, work_email, region, location, department_code,
        department_name, job_level, job_title, manager_employee_id,
        employment_type, change_reason, hire_date, termination_date,
        termination_reason
    )
    VALUES (
        source.employee_id, source.valid_from, source.valid_to, source.is_current,
        source.first_name, source.last_name, source.full_name, source.work_email,
        source.region, source.location, source.department_code,
        source.department_name, source.job_level, source.job_title,
        source.manager_employee_id, source.employment_type, source.change_reason,
        source.hire_date, source.termination_date, source.termination_reason
    );

-- --------------------------------------------------------------------------
-- dim_account (slowly changing, type 1)
-- --------------------------------------------------------------------------

CREATE OR REPLACE TEMPORARY VIEW tmp_account_source AS
SELECT
    account_id,
    nullif(account_name, '')                AS account_name,
    nullif(region, '')                      AS region,
    nullif(segment, '')                     AS segment,
    nullif(industry, '')                    AS industry,
    CAST(nullif(created_date, '') AS DATE)  AS created_date,
    nullif(owner_employee_id, '')           AS owner_employee_id,
    nullif(status, '')                      AS status,
    CAST(nullif(churn_date, '') AS DATE)    AS churn_date
FROM intus.bronze.crm_account;

-- ----------------------------------------------------------------------
-- Rule CRM_DUPLICATE_ACCOUNT — error, rejected
-- ----------------------------------------------------------------------
INSERT INTO intus.silver.dq_exception (
    dataset, rule_code, severity, disposition, target_key, detail, detected_at
)
SELECT
    'crm_account',
    'CRM_DUPLICATE_ACCOUNT',
    'error',
    'rejected',
    account_id,
    concat('account appears ', count(*), ' times in the extract; kept 1, rejected ', count(*) - 1),
    current_timestamp()
FROM tmp_account_source
GROUP BY account_id
HAVING count(*) > 1;

-- `QUALIFY` + `ROW_NUMBER()` is this file's substitute for Postgres's
-- `SELECT DISTINCT ON (...) ... ORDER BY ...` — confirmed live that Postgres's
-- `DISTINCT ON` syntax does not exist on this platform at all
-- (`UNRESOLVED_ROUTINE` on the bare `ON`, since the parser reads it as a
-- function call). `QUALIFY` filters on a window function's result the same
-- way `HAVING` filters on an aggregate's, so the ordering rule ("whichever
-- row, but deterministically") reads the same as the original.
CREATE OR REPLACE TEMPORARY VIEW tmp_account_final AS
SELECT
    account_id,
    account_name,
    region,
    segment,
    industry,
    created_date,
    owner_employee_id,
    status,
    churn_date,
    (churn_date IS NULL) AS is_active
FROM tmp_account_source
QUALIFY row_number() OVER (
    PARTITION BY account_id ORDER BY account_name, created_date
) = 1;

MERGE INTO intus.silver.dim_account AS target
USING tmp_account_final AS source
   ON target.account_id = source.account_id

-- Same struct-comparison failure as dim_employee above, isolated further by
-- hitting it a second time: `is_active` is BOOLEAN NOT NULL on this table,
-- same as `is_current` was there, and dim_department's two-column tuple
-- comparison (no NOT NULL BOOLEAN column in it) never hits this — a NOT NULL
-- BOOLEAN column specifically defeats Spark's struct-type unification here,
-- confirmed by elimination across the three tuple comparisons in this file.
-- Same OR-chain fix, same reason.
WHEN MATCHED AND (
       target.account_name      IS DISTINCT FROM source.account_name
    OR target.region             IS DISTINCT FROM source.region
    OR target.segment            IS DISTINCT FROM source.segment
    OR target.industry           IS DISTINCT FROM source.industry
    OR target.created_date       IS DISTINCT FROM source.created_date
    OR target.owner_employee_id  IS DISTINCT FROM source.owner_employee_id
    OR target.status             IS DISTINCT FROM source.status
    OR target.churn_date         IS DISTINCT FROM source.churn_date
    OR target.is_active          IS DISTINCT FROM source.is_active
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

DELETE FROM intus.silver.dim_account AS target
WHERE target.account_key <> -1
  AND NOT EXISTS (
    SELECT 1 FROM tmp_account_final AS source
    WHERE source.account_id = target.account_id
);

-- --------------------------------------------------------------------------
-- dim_product
-- --------------------------------------------------------------------------

MERGE INTO intus.silver.dim_product AS target
USING (
    SELECT
        product_code,
        max(product_name) AS product_name
    FROM intus.bronze.crm_subscription
    WHERE product_code IS NOT NULL AND product_code <> ''
    GROUP BY product_code
) AS source
ON target.product_code = source.product_code

WHEN MATCHED AND target.product_name IS DISTINCT FROM source.product_name
    THEN UPDATE SET product_name = source.product_name

WHEN NOT MATCHED THEN
    INSERT (product_code, product_name)
    VALUES (source.product_code, source.product_name);
