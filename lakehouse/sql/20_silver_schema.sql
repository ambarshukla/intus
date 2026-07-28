-- Silver schema: the conformed dimensions and facts, structure only — the
-- lakehouse's equivalent of `warehouse/sql/003_warehouse_dimensions.sql` and
-- `004_warehouse_facts.sql`, one dialect over. The DML that populates these
-- tables lives in 21_silver_dimensions.sql and 22_silver_facts.sql, for the
-- same reason the Postgres warehouse keeps structure and transform apart: a
-- transform reruns on every load, a migration does not.
--
-- Every statement here is idempotent (`IF NOT EXISTS`, `CREATE OR REPLACE
-- FUNCTION`), so this file is safe to run every time the job runs, same as
-- bronze's `CREATE OR REPLACE TABLE`.
--
-- --------------------------------------------------------------------------
-- Where this file stops matching Postgres, and why
-- --------------------------------------------------------------------------
--
-- Three guarantees the Postgres schema gets from the database itself have no
-- Delta equivalent, confirmed live against the workspace before writing any
-- of this (not assumed from docs):
--
--   1. No exclusion constraint. `ex_dim_employee_no_overlap` (GiST, "no two
--      spans for one employee may overlap") has no Delta counterpart — a
--      CHECK constraint referencing another row is rejected outright
--      (`DELTA_UNSUPPORTED_EXPRESSION_CHECK_CONSTRAINT`, tested with a plain
--      `NOT EXISTS (SELECT ...)` CHECK). Delta CHECK constraints are
--      single-row only.
--   2. PRIMARY KEY and FOREIGN KEY exist as syntax but enforce nothing —
--      tested live: declared a primary key, inserted a duplicate anyway, it
--      went in without complaint. Declaring them here would read as a
--      guarantee this platform does not provide, so this file does not
--      declare them at all. The single-row CHECK constraints below (e.g.
--      `valid_to > valid_from`) are kept because those the platform *does*
--      enforce.
--   3. No `ON CONFLICT`. Delta's idempotent-insert idiom is `MERGE`, used
--      throughout this file for the same "insert this sentinel row once,
--      harmlessly on every rerun" job Postgres's `ON CONFLICT DO NOTHING`
--      does for the unknown-member rows below.
--
-- What replaces guarantee (1): the no-overlap and single-current-version
-- rules move entirely into the transform's own logic in
-- 21_silver_dimensions.sql — a self-join that detects and rejects an
-- overlapping span, and a `ROW_NUMBER()` that can only ever mark one version
-- current — the same shape HR_OVERLAPPING_SPAN already used in Postgres, just
-- without a database-level backstop behind it. See docs/DECISIONS.md D-024
-- for the alternatives considered and why this is the chosen tradeoff, not
-- an oversight.
--
-- What replaces the Postgres `run_id` / `warehouse.transform_run` bookkeeping
-- that ties every `dq_exception` row to the run that produced it: nothing —
-- it is dropped. `intus.silver.dq_exception` is truncated and rebuilt on
-- every run (21_silver_dimensions.sql, first statement), same
-- truncate-and-reload shape as every fact table, and Delta's own version
-- history (`DESCRIBE HISTORY intus.silver.dq_exception`) recovers what any
-- past run found if that is ever needed — the identical reasoning
-- `staging.load_audit` got dropped for in bronze (D-023), applied a second
-- time to a second piece of hand-rolled bookkeeping. See D-025.

-- --------------------------------------------------------------------------
-- Data quality
-- --------------------------------------------------------------------------

-- CHECK constraints cannot be declared inline in CREATE TABLE on this
-- platform ("Only PRIMARY KEY and FOREIGN KEY constraints are currently
-- supported" here) — confirmed live, so they are added via ALTER TABLE
-- below instead, same as the exploratory probing that established the rest
-- of this file's design.
CREATE TABLE IF NOT EXISTS intus.silver.dq_exception (
    exception_id    BIGINT GENERATED ALWAYS AS IDENTITY,
    dataset         STRING NOT NULL,
    rule_code       STRING NOT NULL,
    severity        STRING NOT NULL,
    disposition     STRING NOT NULL,
    -- Same contract as the Postgres column: the source row's primary key,
    -- components joined by '|', because detections are scored against the
    -- generator's defect manifest by this exact string.
    target_key      STRING NOT NULL,
    detail          STRING NOT NULL,
    detected_at     TIMESTAMP NOT NULL
) USING DELTA;

-- DROP IF EXISTS then ADD, not a bare ADD, so this file stays safe to rerun
-- every job execution — confirmed live that a bare re-ADD on an unchanged
-- rerun fails with "constraint already exists", where Postgres's original
-- migration only ever ran once and never had to consider this.
ALTER TABLE intus.silver.dq_exception DROP CONSTRAINT IF EXISTS ck_dq_exception_severity;
ALTER TABLE intus.silver.dq_exception
    ADD CONSTRAINT ck_dq_exception_severity CHECK (severity IN ('error', 'warning'));
ALTER TABLE intus.silver.dq_exception DROP CONSTRAINT IF EXISTS ck_dq_exception_disposition;
ALTER TABLE intus.silver.dq_exception
    ADD CONSTRAINT ck_dq_exception_disposition CHECK (disposition IN ('rejected', 'repaired', 'flagged'));

-- --------------------------------------------------------------------------
-- dim_date
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS intus.silver.dim_date (
    date_key        INT NOT NULL,
    full_date       DATE NOT NULL,
    year            SMALLINT NOT NULL,
    quarter         SMALLINT NOT NULL,
    month           SMALLINT NOT NULL,
    month_name      STRING NOT NULL,
    day_of_month    SMALLINT NOT NULL,
    day_of_week     SMALLINT NOT NULL,  -- 1 = Monday, ISO
    day_name        STRING NOT NULL,
    iso_week        SMALLINT NOT NULL,
    is_weekend      BOOLEAN NOT NULL,
    fiscal_period   STRING NOT NULL,
    fiscal_quarter  STRING NOT NULL,
    fiscal_year     SMALLINT NOT NULL
) USING DELTA;

-- --------------------------------------------------------------------------
-- dim_department
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS intus.silver.dim_department (
    department_key  BIGINT GENERATED BY DEFAULT AS IDENTITY,
    department_code STRING NOT NULL,
    department_name STRING NOT NULL,
    cost_center     STRING
) USING DELTA;

MERGE INTO intus.silver.dim_department AS target
USING (
    SELECT -1L AS department_key, 'UNKNOWN' AS department_code,
           'Unknown Department' AS department_name, CAST(NULL AS STRING) AS cost_center
) AS source
ON target.department_key = source.department_key
WHEN NOT MATCHED THEN INSERT *;

-- --------------------------------------------------------------------------
-- dim_employee (slowly changing, type 2)
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS intus.silver.dim_employee (
    employee_key         BIGINT GENERATED BY DEFAULT AS IDENTITY,
    employee_id          STRING NOT NULL,
    valid_from           DATE NOT NULL,
    valid_to             DATE,           -- exclusive; NULL means open-ended
    is_current           BOOLEAN NOT NULL,
    first_name           STRING,
    last_name            STRING,
    full_name            STRING,
    work_email           STRING,
    region               STRING,
    location             STRING,
    department_code      STRING,
    department_name      STRING,
    job_level            SMALLINT,
    job_title            STRING,
    manager_employee_id  STRING,
    employment_type      STRING,
    change_reason        STRING,
    hire_date            DATE,
    termination_date     DATE,
    termination_reason   STRING
) USING DELTA;

-- No CHECK constraint on this table (Phase 4 reversal of Phase 3b's original
-- choice) — confirmed live that Unity Catalog refuses to attach EITHER a row
-- filter or a column mask to a table that has one at all
-- (ROW_LEVEL_SECURITY_FEATURE_NOT_SUPPORTED.CHECK_CONSTRAINT /
-- COLUMN_MASKS_FEATURE_NOT_SUPPORTED.CHECK_CONSTRAINT), and this table needs
-- both (40_governance_schema.sql, 41_governance_apply.sql). DROP IF EXISTS
-- with nothing re-adding it, so a rerun against an environment still carrying
-- Phase 3b's constraint removes it rather than re-erroring. See D-031 for the
-- full trade-off: the guarantee this constraint gave (valid_to > valid_from)
-- moves entirely to the transform, the same posture D-024 already settled on
-- for the harder no-overlap invariant on this same table.
ALTER TABLE intus.silver.dim_employee DROP CONSTRAINT IF EXISTS ck_dim_employee_span;

COMMENT ON COLUMN intus.silver.dim_employee.is_current IS
    'Latest version of this employee. Terminated employees still have one.';
COMMENT ON COLUMN intus.silver.dim_employee.manager_employee_id IS
    'NULL when the extract referenced a manager who is not in the employee population.';

MERGE INTO intus.silver.dim_employee AS target
USING (
    SELECT
        -1L AS employee_key, 'UNKNOWN' AS employee_id, DATE'1900-01-01' AS valid_from,
        CAST(NULL AS DATE) AS valid_to, true AS is_current,
        CAST(NULL AS STRING) AS first_name, CAST(NULL AS STRING) AS last_name,
        'Unknown Employee' AS full_name, CAST(NULL AS STRING) AS work_email,
        CAST(NULL AS STRING) AS region, CAST(NULL AS STRING) AS location,
        CAST(NULL AS STRING) AS department_code, CAST(NULL AS STRING) AS department_name,
        CAST(NULL AS SMALLINT) AS job_level, CAST(NULL AS STRING) AS job_title,
        CAST(NULL AS STRING) AS manager_employee_id, CAST(NULL AS STRING) AS employment_type,
        CAST(NULL AS STRING) AS change_reason, CAST(NULL AS DATE) AS hire_date,
        CAST(NULL AS DATE) AS termination_date, CAST(NULL AS STRING) AS termination_reason
) AS source
ON target.employee_key = source.employee_key
WHEN NOT MATCHED THEN INSERT *;

-- --------------------------------------------------------------------------
-- Point-in-time dimension lookup
-- --------------------------------------------------------------------------

-- Same contract as warehouse.employee_key_as_of: the employee_key in force on
-- a given date, or NULL if no version covers it (the gap is deliberate — see
-- the Postgres original for the full argument; SEC_LOGIN_AFTER_TERMINATION in
-- 22_silver_facts.sql is built directly on this returning NULL).
CREATE OR REPLACE FUNCTION intus.silver.employee_key_as_of(p_employee_id STRING, p_as_of DATE)
RETURNS BIGINT
RETURN (
    SELECT employee_key
    FROM intus.silver.dim_employee
    WHERE employee_id = p_employee_id
      AND p_as_of >= valid_from
      AND (valid_to IS NULL OR p_as_of < valid_to)
    LIMIT 1
);

-- Same contract as warehouse.employee_key_best: falls back to the nearest
-- known version instead of NULL, for facts that need "who did this" rather
-- than "who was in this role that exact day".
CREATE OR REPLACE FUNCTION intus.silver.employee_key_best(p_employee_id STRING, p_as_of DATE)
RETURNS BIGINT
RETURN coalesce(
    intus.silver.employee_key_as_of(p_employee_id, p_as_of),
    (
        SELECT employee_key FROM intus.silver.dim_employee
        WHERE employee_id = p_employee_id
        ORDER BY valid_from DESC
        LIMIT 1
    )
);

-- --------------------------------------------------------------------------
-- dim_account (slowly changing, type 1)
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS intus.silver.dim_account (
    account_key        BIGINT GENERATED BY DEFAULT AS IDENTITY,
    account_id         STRING NOT NULL,
    account_name       STRING NOT NULL,
    region             STRING,
    segment            STRING,
    industry           STRING,
    created_date       DATE,
    owner_employee_id  STRING,
    status             STRING,
    churn_date         DATE,
    is_active          BOOLEAN NOT NULL
) USING DELTA;

MERGE INTO intus.silver.dim_account AS target
USING (
    SELECT
        -1L AS account_key, 'UNKNOWN' AS account_id, 'Unknown Account' AS account_name,
        CAST(NULL AS STRING) AS region, CAST(NULL AS STRING) AS segment,
        CAST(NULL AS STRING) AS industry, CAST(NULL AS DATE) AS created_date,
        CAST(NULL AS STRING) AS owner_employee_id, CAST(NULL AS STRING) AS status,
        CAST(NULL AS DATE) AS churn_date, false AS is_active
) AS source
ON target.account_key = source.account_key
WHEN NOT MATCHED THEN INSERT *;

-- --------------------------------------------------------------------------
-- dim_product
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS intus.silver.dim_product (
    product_key   BIGINT GENERATED BY DEFAULT AS IDENTITY,
    product_code  STRING NOT NULL,
    product_name  STRING NOT NULL
) USING DELTA;

MERGE INTO intus.silver.dim_product AS target
USING (
    SELECT -1L AS product_key, 'UNKNOWN' AS product_code, 'Unknown Product' AS product_name
) AS source
ON target.product_key = source.product_key
WHEN NOT MATCHED THEN INSERT *;

-- --------------------------------------------------------------------------
-- Fact tables
-- --------------------------------------------------------------------------
-- No DEFERRABLE FK dance here (contrast with 004_warehouse_facts.sql's long
-- comment about it): Unity Catalog does not enforce foreign keys at all, so
-- there is no mid-transaction violation to defer in the first place. The
-- ordering that mattered in Postgres — dimensions fully rebuilt before facts
-- resolve keys against them — still matters here, just enforced by task
-- ordering in databricks.yml (`depends_on`) instead of one transaction.

CREATE TABLE IF NOT EXISTS intus.silver.fact_compensation (
    compensation_id    STRING NOT NULL,
    employee_key       BIGINT NOT NULL,
    date_key           INT NOT NULL,
    pay_grade          STRING,
    annual_salary_usd  DECIMAL(12, 2),
    bonus_target_pct   DECIMAL(5, 4),
    equity_units       INT,
    currency           STRING,
    change_reason      STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS intus.silver.fact_performance_review (
    review_id              STRING NOT NULL,
    employee_key           BIGINT NOT NULL,
    reviewer_employee_key  BIGINT,
    date_key               INT NOT NULL,
    review_period          STRING,
    rating                 SMALLINT,
    rating_label           STRING,
    promotion_recommended  BOOLEAN
) USING DELTA;

CREATE TABLE IF NOT EXISTS intus.silver.fact_subscription (
    subscription_id    STRING NOT NULL,
    account_key        BIGINT NOT NULL,
    product_key        BIGINT NOT NULL,
    start_date_key      INT NOT NULL,
    end_date_key         INT,
    seats                 INT,
    arr_usd               DECIMAL(12, 2),
    billing_frequency     STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS intus.silver.fact_invoice (
    invoice_id       STRING NOT NULL,
    account_key      BIGINT NOT NULL,
    -- Degenerate reference, not a foreign key to fact_subscription — same
    -- reasoning as Postgres: two facts referencing each other by surrogate
    -- key turns a star schema into a graph.
    subscription_id  STRING,
    issue_date_key   INT NOT NULL,
    due_date_key     INT NOT NULL,
    paid_date_key    INT,
    amount_usd       DECIMAL(12, 2),
    currency         STRING,
    status           STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS intus.silver.fact_opportunity (
    opportunity_id      STRING NOT NULL,
    account_key         BIGINT NOT NULL,
    owner_employee_key  BIGINT NOT NULL,
    product_key         BIGINT NOT NULL,
    created_date_key    INT NOT NULL,
    close_date_key      INT,
    opportunity_type    STRING,
    stage               STRING,
    amount_usd          DECIMAL(12, 2),
    probability_pct     SMALLINT,
    is_won              BOOLEAN
) USING DELTA;

-- No surrogate key, same reason as Postgres: the grain (date/account/product)
-- is its own natural key and nothing downstream references a usage row.
CREATE TABLE IF NOT EXISTS intus.silver.fact_usage_daily (
    date_key        INT NOT NULL,
    account_key     BIGINT NOT NULL,
    product_key     BIGINT NOT NULL,
    active_users    INT,
    sessions        INT,
    api_calls       INT,
    storage_gb      DECIMAL(12, 3),
    avg_latency_ms  INT,
    error_count     INT
) USING DELTA;

CREATE TABLE IF NOT EXISTS intus.silver.fact_ai_usage (
    event_id            STRING NOT NULL,
    -- Kept alongside employee_key, same reason as Postgres: recoverable for
    -- investigation even when the FK resolution fell back to -1.
    employee_id         STRING NOT NULL,
    employee_key        BIGINT NOT NULL,
    department_key      BIGINT NOT NULL,
    date_key            INT NOT NULL,
    event_ts            TIMESTAMP NOT NULL,
    model               STRING,
    feature             STRING,
    prompt_tokens       INT,
    completion_tokens   INT,
    cost_usd            DECIMAL(14, 6),
    latency_ms          INT,
    flagged_by_policy   BOOLEAN
) USING DELTA;

CREATE TABLE IF NOT EXISTS intus.silver.fact_access_event (
    event_id         STRING NOT NULL,
    employee_id      STRING,  -- nullable: SEC_MISSING_ACTOR means there is none
    employee_key     BIGINT NOT NULL,
    department_key   BIGINT NOT NULL,
    date_key         INT NOT NULL,
    event_ts         TIMESTAMP NOT NULL,
    system           STRING,
    action           STRING,
    resource         STRING,
    source_ip        STRING,
    source_country   STRING,
    result           STRING,
    mfa_used         BOOLEAN
) USING DELTA;

CREATE TABLE IF NOT EXISTS intus.silver.fact_gl_actual (
    actual_id                STRING NOT NULL,
    department_key           BIGINT NOT NULL,
    date_key                 INT NOT NULL,
    -- Degenerate: the finance business key for the accounting period, not
    -- derivable from date_key alone in the general case.
    fiscal_period             STRING,
    gl_account                STRING,
    gl_account_name           STRING,
    amount_usd                DECIMAL(14, 2),
    vendor                    STRING,
    description               STRING,
    posted_by_employee_key    BIGINT NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS intus.silver.fact_budget (
    budget_id                  STRING NOT NULL,
    department_key             BIGINT NOT NULL,
    fiscal_period               STRING,
    fiscal_year                 SMALLINT,
    fiscal_quarter               SMALLINT,
    gl_account                   STRING,
    gl_account_name              STRING,
    budget_usd                   DECIMAL(14, 2),
    approved_by_employee_key     BIGINT NOT NULL,
    approved_date_key            INT
) USING DELTA;
