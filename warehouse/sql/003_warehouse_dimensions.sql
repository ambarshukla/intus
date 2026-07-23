-- The conformed dimensions, plus the machinery that decides what is allowed
-- into them.
--
-- Structure lives here (versioned, checksummed, applied once). The DML that
-- populates these tables lives in warehouse/transform/ and runs on every load,
-- because a transform is not a migration: it is repeatable, idempotent, and
-- rerun whenever staging changes. Conflating the two is how warehouses end up
-- with migrations nobody dares re-run.

-- Needed for the exclusion constraint on dim_employee below: GiST cannot index
-- plain equality on text without it.
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- --------------------------------------------------------------------------
-- Transform bookkeeping
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS warehouse.transform_run (
    run_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    source_seed     bigint,
    source_scale    text,
    source_as_of    date,
    status          text NOT NULL DEFAULT 'running',
    CONSTRAINT ck_transform_run_status CHECK (status IN ('running', 'succeeded', 'failed'))
);

COMMENT ON TABLE warehouse.transform_run IS
    'One row per transform execution; dq_exception rows hang off it.';

-- --------------------------------------------------------------------------
-- Data quality
-- --------------------------------------------------------------------------

-- Deliberately "exception" rather than "reject": not every problem means
-- throwing the row away, and pretending otherwise produces a warehouse that
-- silently loses data. Three dispositions, and choosing between them is the
-- actual data-quality design work:
--
--   rejected  The row breaks an invariant the target table cannot hold, so it
--             is excluded. Overlapping SCD2 spans, duplicate keys.
--   repaired  The row is kept with an offending value replaced. An employee
--             whose manager is missing from the extract still counts towards
--             headcount; dropping them would lose a person to fix a pointer.
--   flagged   The row is loaded unchanged and recorded as suspicious. A salary
--             ten times its band is probably wrong, but the warehouse is not
--             entitled to decide that unilaterally.
CREATE TABLE IF NOT EXISTS warehouse.dq_exception (
    exception_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id          bigint NOT NULL REFERENCES warehouse.transform_run (run_id),
    dataset         text NOT NULL,
    rule_code       text NOT NULL,
    severity        text NOT NULL,
    disposition     text NOT NULL,
    -- The source row's primary key, components joined by '|'. This exact
    -- format is what lets detections be scored against the generator's defect
    -- manifest, so it is a contract, not a formatting choice.
    target_key      text NOT NULL,
    detail          text NOT NULL,
    detected_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dq_exception_severity CHECK (severity IN ('error', 'warning')),
    CONSTRAINT ck_dq_exception_disposition
        CHECK (disposition IN ('rejected', 'repaired', 'flagged'))
);

CREATE INDEX IF NOT EXISTS ix_dq_exception_run ON warehouse.dq_exception (run_id, rule_code);
CREATE INDEX IF NOT EXISTS ix_dq_exception_rule ON warehouse.dq_exception (rule_code);

-- --------------------------------------------------------------------------
-- dim_date
-- --------------------------------------------------------------------------

-- Generated rather than derived from the data, and keyed by an integer
-- YYYYMMDD rather than a surrogate sequence. The integer key is the one place
-- in a star schema where a "meaningful" key is standard practice: it sorts and
-- ranges correctly, it is readable in a query plan, and a date dimension never
-- needs to version.
CREATE TABLE IF NOT EXISTS warehouse.dim_date (
    date_key        integer PRIMARY KEY,
    full_date       date NOT NULL UNIQUE,
    year            smallint NOT NULL,
    quarter         smallint NOT NULL,
    month           smallint NOT NULL,
    month_name      text NOT NULL,
    day_of_month    smallint NOT NULL,
    day_of_week     smallint NOT NULL,  -- 1 = Monday, ISO
    day_name        text NOT NULL,
    iso_week        smallint NOT NULL,
    is_weekend      boolean NOT NULL,
    -- Mirrors the generator's fiscal calendar (fiscal year = calendar year).
    -- Held as text keys so finance facts join without re-deriving them.
    fiscal_period   text NOT NULL,
    fiscal_quarter  text NOT NULL,
    fiscal_year     smallint NOT NULL
);

-- --------------------------------------------------------------------------
-- dim_department
-- --------------------------------------------------------------------------

-- A conformed dimension assembled from two unrelated sources: code and name
-- come from the HR extract, the cost centre from the finance extract. That is
-- what "conformed" means in practice — one agreed definition serving both, so
-- an HR headcount report and a finance spend report slice by the same thing.
CREATE TABLE IF NOT EXISTS warehouse.dim_department (
    department_key  bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    department_code text NOT NULL UNIQUE,
    department_name text NOT NULL,
    cost_center     text
);

-- --------------------------------------------------------------------------
-- dim_employee (slowly changing, type 2)
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS warehouse.dim_employee (
    employee_key        bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    employee_id         text NOT NULL,
    valid_from          date NOT NULL,
    valid_to            date,           -- exclusive; NULL means open-ended
    is_current          boolean NOT NULL,
    first_name          text,
    last_name           text,
    full_name           text,
    work_email          text,
    region              text,
    location            text,
    department_code     text,
    department_name     text,
    job_level           smallint,
    job_title           text,
    manager_employee_id text,
    employment_type     text,
    change_reason       text,
    hire_date           date,
    termination_date    date,
    termination_reason  text,

    CONSTRAINT uq_dim_employee_version UNIQUE (employee_id, valid_from),
    CONSTRAINT ck_dim_employee_span CHECK (valid_to IS NULL OR valid_to > valid_from),

    -- The SCD2 invariant, enforced by the database rather than trusted from
    -- the transform: one employee cannot have two versions covering the same
    -- day. The transform rejects overlaps before they get here, so this should
    -- never fire — which is precisely why it is worth having. A constraint
    -- that only fires when the code above it is wrong is the cheapest possible
    -- insurance against a point-in-time join silently returning two rows.
    CONSTRAINT ex_dim_employee_no_overlap EXCLUDE USING gist (
        employee_id WITH =,
        daterange(valid_from, valid_to, '[)') WITH &&
    )
);

-- `is_current` marks the latest version of an employee, which is not the same
-- as "still employed": a leaver's final version is current *and* closed. A
-- partial unique index says "at most one per employee" without forbidding the
-- many historical rows.
CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_employee_current
    ON warehouse.dim_employee (employee_id) WHERE is_current;

CREATE INDEX IF NOT EXISTS ix_dim_employee_natural
    ON warehouse.dim_employee (employee_id, valid_from);

COMMENT ON COLUMN warehouse.dim_employee.is_current IS
    'Latest version of this employee. Terminated employees still have one.';
COMMENT ON COLUMN warehouse.dim_employee.manager_employee_id IS
    'NULL when the extract referenced a manager who is not in the employee population.';

-- --------------------------------------------------------------------------
-- dim_account (slowly changing, type 1)
-- --------------------------------------------------------------------------

-- Type 1 deliberately, to contrast with dim_employee: the CRM extract carries
-- only current account state, with no history to preserve. Modelling it as
-- type 2 would manufacture versions the source cannot substantiate — the
-- dimension would claim to know when an account changed segment, and it does
-- not.
CREATE TABLE IF NOT EXISTS warehouse.dim_account (
    account_key         bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    account_id          text NOT NULL UNIQUE,
    account_name        text NOT NULL,
    region              text,
    segment             text,
    industry            text,
    created_date        date,
    owner_employee_id   text,
    status              text,
    churn_date          date,
    is_active           boolean NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_dim_account_owner ON warehouse.dim_account (owner_employee_id);

-- --------------------------------------------------------------------------
-- dim_product
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS warehouse.dim_product (
    product_key     bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    product_code    text NOT NULL UNIQUE,
    product_name    text NOT NULL
);
