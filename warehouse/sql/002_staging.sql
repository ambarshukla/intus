-- Staging: the landed extracts, exactly as delivered.
--
-- Every column is text, and that is the single most important decision in this
-- file. The extracts contain deliberate defects — a salary with an extra zero,
-- a fiscal period that disagrees with its posting date, a close date before the
-- open date. If staging were typed, the COPY would fail on the first bad row
-- and the load would be an all-or-nothing affair: one malformed field in a
-- million-row file and nothing lands, with a message pointing at a byte offset.
--
-- Landing everything as text moves rejection from the *load* to the
-- *transform*, where it belongs. The transform can then reject a row with a
-- business reason ("close_date precedes created_date") instead of the driver
-- reporting "invalid input syntax for type date", and — the part that matters —
-- can record what it rejected rather than failing the batch.
--
-- No primary keys either. Two of the seeded defects are duplicate rows; a
-- primary key here would reject them at the door, and the point is to detect
-- and report them, not to make them unrepresentable.
--
-- Column order and names mirror the generator's declared schemas exactly, so
-- COPY needs no column list. A test asserts that correspondence against the
-- live catalog, because "mirrors exactly" is a claim that rots silently.

-- --------------------------------------------------------------------------
-- HR
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS staging.hr_employee_history (
    employee_id         text,
    valid_from          text,
    valid_to            text,
    first_name          text,
    last_name           text,
    work_email          text,
    region              text,
    location            text,
    department_code     text,
    department_name     text,
    job_level           text,
    job_title           text,
    manager_id          text,
    employment_type     text,
    change_reason       text,
    hire_date           text,
    termination_date    text,
    termination_reason  text
);

CREATE TABLE IF NOT EXISTS staging.hr_compensation (
    compensation_id     text,
    employee_id         text,
    effective_from      text,
    effective_to        text,
    pay_grade           text,
    annual_salary_usd   text,
    bonus_target_pct    text,
    equity_units        text,
    currency            text,
    change_reason       text
);

CREATE TABLE IF NOT EXISTS staging.hr_performance_review (
    review_id             text,
    employee_id           text,
    review_period         text,
    reviewer_id           text,
    rating                text,
    rating_label          text,
    promotion_recommended text,
    submitted_date        text
);

-- --------------------------------------------------------------------------
-- Sales and revenue
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS staging.crm_account (
    account_id          text,
    account_name        text,
    region              text,
    segment             text,
    industry            text,
    created_date        text,
    owner_employee_id   text,
    status              text,
    churn_date          text
);

CREATE TABLE IF NOT EXISTS staging.crm_subscription (
    subscription_id     text,
    account_id          text,
    product_code        text,
    product_name        text,
    start_date          text,
    end_date            text,
    seats               text,
    arr_usd             text,
    billing_frequency   text
);

CREATE TABLE IF NOT EXISTS staging.crm_opportunity (
    opportunity_id      text,
    account_id          text,
    owner_employee_id   text,
    product_code        text,
    opportunity_type    text,
    created_date        text,
    close_date          text,
    stage               text,
    amount_usd          text,
    probability_pct     text,
    is_won              text
);

CREATE TABLE IF NOT EXISTS staging.crm_invoice (
    invoice_id          text,
    account_id          text,
    subscription_id     text,
    issue_date          text,
    due_date            text,
    paid_date           text,
    amount_usd          text,
    currency            text,
    status              text
);

-- --------------------------------------------------------------------------
-- Product telemetry
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS staging.usage_daily (
    usage_date          text,
    account_id          text,
    product_code        text,
    active_users        text,
    sessions            text,
    api_calls           text,
    storage_gb          text,
    avg_latency_ms      text,
    error_count         text
);

-- --------------------------------------------------------------------------
-- Internal AI usage
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS staging.ai_usage_event (
    event_id            text,
    event_ts            text,
    employee_id         text,
    department_code     text,
    region              text,
    model               text,
    feature             text,
    prompt_tokens       text,
    completion_tokens   text,
    cost_usd            text,
    latency_ms          text,
    flagged_by_policy   text
);

-- --------------------------------------------------------------------------
-- Finance
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS staging.fin_budget (
    budget_id           text,
    fiscal_period       text,
    fiscal_year         text,
    fiscal_quarter      text,
    cost_center         text,
    department_code     text,
    gl_account          text,
    gl_account_name     text,
    budget_usd          text,
    approved_by         text,
    approved_date       text
);

CREATE TABLE IF NOT EXISTS staging.fin_actual (
    actual_id           text,
    fiscal_period       text,
    posting_date        text,
    cost_center         text,
    department_code     text,
    gl_account          text,
    gl_account_name     text,
    amount_usd          text,
    vendor              text,
    description         text,
    posted_by           text
);

-- --------------------------------------------------------------------------
-- Systems and security
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS staging.sec_access_event (
    event_id            text,
    event_ts            text,
    employee_id         text,
    department_code     text,
    system              text,
    action              text,
    resource            text,
    source_ip           text,
    source_country      text,
    result              text,
    mfa_used            text
);

-- --------------------------------------------------------------------------
-- Load audit
-- --------------------------------------------------------------------------

-- One row per file per load. The SHA-256 comes from the generator's manifest,
-- so "which extract is in staging right now?" has an answer that survives the
-- next reload — and a re-run of the same extract is identifiable as such
-- rather than looking like fresh data.
CREATE TABLE IF NOT EXISTS staging.load_audit (
    load_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset         text        NOT NULL,
    source_file     text        NOT NULL,
    source_sha256   text        NOT NULL,
    manifest_seed   bigint      NOT NULL,
    manifest_scale  text        NOT NULL,
    as_of_date      date        NOT NULL,
    rows_expected   bigint      NOT NULL,
    rows_loaded     bigint      NOT NULL,
    loaded_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_load_audit_dataset ON staging.load_audit (dataset, loaded_at DESC);

COMMENT ON TABLE staging.load_audit IS
    'One row per dataset per load, carrying the generator manifest hash for provenance.';
