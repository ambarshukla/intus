-- Bronze: the landed extracts, exactly as delivered — the lakehouse's
-- equivalent of `warehouse/sql/002_staging.sql`, one dialect over.
--
-- Same design, different primitive. Postgres staging types every column text
-- and loads via COPY so a single malformed row can't fail the whole batch;
-- read_files() with an explicit STRING schema gets the identical property —
-- every column lands as text, so a bad row is a value the *silver* transform
-- rejects with a reason, not a row the *bronze* load refuses to parse. CREATE
-- OR REPLACE TABLE ... AS SELECT is the truncate-and-reload of this platform:
-- each run replaces the table wholesale, so a rerun is idempotent by
-- construction, exactly like staging's TRUNCATE-then-COPY.
--
-- No primary keys, no constraints — same reason as staging: two seeded
-- defects are duplicate rows, and the point is to detect and report them, not
-- make them unrepresentable at the door.
--
-- Provenance is not a hand-rolled load_audit table here. Delta tracks it for
-- free: `DESCRIBE HISTORY intus.bronze.<table>` gives the operation, the
-- timestamp, and the row counts for every version. Building a second
-- provenance mechanism when the platform already ships one would just be
-- something else to keep in sync.
--
-- Column order and names mirror the generator's declared schemas exactly,
-- same claim staging makes about itself — checked here by
-- `lakehouse/tests/test_bronze_schema.py` against the same `all_datasets()`
-- registry, statically (no live warehouse needed to run the test).

-- --------------------------------------------------------------------------
-- HR
-- --------------------------------------------------------------------------

CREATE OR REPLACE TABLE intus.bronze.hr_employee_history AS
SELECT * FROM read_files(
  '/Volumes/intus/landing/raw/hr_employee_history.csv',
  format => 'csv',
  header => true,
  schema => 'employee_id STRING, valid_from STRING, valid_to STRING, first_name STRING, last_name STRING, work_email STRING, region STRING, location STRING, department_code STRING, department_name STRING, job_level STRING, job_title STRING, manager_id STRING, employment_type STRING, change_reason STRING, hire_date STRING, termination_date STRING, termination_reason STRING'
);

CREATE OR REPLACE TABLE intus.bronze.hr_compensation AS
SELECT * FROM read_files(
  '/Volumes/intus/landing/raw/hr_compensation.csv',
  format => 'csv',
  header => true,
  schema => 'compensation_id STRING, employee_id STRING, effective_from STRING, effective_to STRING, pay_grade STRING, annual_salary_usd STRING, bonus_target_pct STRING, equity_units STRING, currency STRING, change_reason STRING'
);

CREATE OR REPLACE TABLE intus.bronze.hr_performance_review AS
SELECT * FROM read_files(
  '/Volumes/intus/landing/raw/hr_performance_review.csv',
  format => 'csv',
  header => true,
  schema => 'review_id STRING, employee_id STRING, review_period STRING, reviewer_id STRING, rating STRING, rating_label STRING, promotion_recommended STRING, submitted_date STRING'
);

-- --------------------------------------------------------------------------
-- Sales and revenue
-- --------------------------------------------------------------------------

CREATE OR REPLACE TABLE intus.bronze.crm_account AS
SELECT * FROM read_files(
  '/Volumes/intus/landing/raw/crm_account.csv',
  format => 'csv',
  header => true,
  schema => 'account_id STRING, account_name STRING, region STRING, segment STRING, industry STRING, created_date STRING, owner_employee_id STRING, status STRING, churn_date STRING'
);

CREATE OR REPLACE TABLE intus.bronze.crm_subscription AS
SELECT * FROM read_files(
  '/Volumes/intus/landing/raw/crm_subscription.csv',
  format => 'csv',
  header => true,
  schema => 'subscription_id STRING, account_id STRING, product_code STRING, product_name STRING, start_date STRING, end_date STRING, seats STRING, arr_usd STRING, billing_frequency STRING'
);

CREATE OR REPLACE TABLE intus.bronze.crm_opportunity AS
SELECT * FROM read_files(
  '/Volumes/intus/landing/raw/crm_opportunity.csv',
  format => 'csv',
  header => true,
  schema => 'opportunity_id STRING, account_id STRING, owner_employee_id STRING, product_code STRING, opportunity_type STRING, created_date STRING, close_date STRING, stage STRING, amount_usd STRING, probability_pct STRING, is_won STRING'
);

CREATE OR REPLACE TABLE intus.bronze.crm_invoice AS
SELECT * FROM read_files(
  '/Volumes/intus/landing/raw/crm_invoice.csv',
  format => 'csv',
  header => true,
  schema => 'invoice_id STRING, account_id STRING, subscription_id STRING, issue_date STRING, due_date STRING, paid_date STRING, amount_usd STRING, currency STRING, status STRING'
);

-- --------------------------------------------------------------------------
-- Product telemetry
-- --------------------------------------------------------------------------

CREATE OR REPLACE TABLE intus.bronze.usage_daily AS
SELECT * FROM read_files(
  '/Volumes/intus/landing/raw/usage_daily.csv',
  format => 'csv',
  header => true,
  schema => 'usage_date STRING, account_id STRING, product_code STRING, active_users STRING, sessions STRING, api_calls STRING, storage_gb STRING, avg_latency_ms STRING, error_count STRING'
);

-- --------------------------------------------------------------------------
-- Internal AI usage
-- --------------------------------------------------------------------------

CREATE OR REPLACE TABLE intus.bronze.ai_usage_event AS
SELECT * FROM read_files(
  '/Volumes/intus/landing/raw/ai_usage_event.csv',
  format => 'csv',
  header => true,
  schema => 'event_id STRING, event_ts STRING, employee_id STRING, department_code STRING, region STRING, model STRING, feature STRING, prompt_tokens STRING, completion_tokens STRING, cost_usd STRING, latency_ms STRING, flagged_by_policy STRING'
);

-- --------------------------------------------------------------------------
-- Finance
-- --------------------------------------------------------------------------

CREATE OR REPLACE TABLE intus.bronze.fin_budget AS
SELECT * FROM read_files(
  '/Volumes/intus/landing/raw/fin_budget.csv',
  format => 'csv',
  header => true,
  schema => 'budget_id STRING, fiscal_period STRING, fiscal_year STRING, fiscal_quarter STRING, cost_center STRING, department_code STRING, gl_account STRING, gl_account_name STRING, budget_usd STRING, approved_by STRING, approved_date STRING'
);

CREATE OR REPLACE TABLE intus.bronze.fin_actual AS
SELECT * FROM read_files(
  '/Volumes/intus/landing/raw/fin_actual.csv',
  format => 'csv',
  header => true,
  schema => 'actual_id STRING, fiscal_period STRING, posting_date STRING, cost_center STRING, department_code STRING, gl_account STRING, gl_account_name STRING, amount_usd STRING, vendor STRING, description STRING, posted_by STRING'
);

-- --------------------------------------------------------------------------
-- Systems and security
-- --------------------------------------------------------------------------

CREATE OR REPLACE TABLE intus.bronze.sec_access_event AS
SELECT * FROM read_files(
  '/Volumes/intus/landing/raw/sec_access_event.csv',
  format => 'csv',
  header => true,
  schema => 'event_id STRING, event_ts STRING, employee_id STRING, department_code STRING, system STRING, action STRING, resource STRING, source_ip STRING, source_country STRING, result STRING, mfa_used STRING'
);
