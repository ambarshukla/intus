-- dim_employee: the type-2 dimension, and the data-quality rules guarding it.
--
-- The source extract is already effective-dated, so this is not "detect that an
-- attribute changed and close the old row" — it is "reconcile the warehouse's
-- versions with the source's versions". Three things make that non-trivial and
-- they are the reason this file is not a single INSERT:
--
--  1. Overlapping spans must never reach the table. A point-in-time join that
--     returns two rows for one employee is worse than one that returns none,
--     because it silently double-counts instead of failing.
--  2. Surrogate keys must survive a rerun. Facts will reference employee_key,
--     so truncate-and-rebuild — which is otherwise the obvious approach for a
--     full extract — would reissue every key and orphan every fact. That is
--     the whole reason this is a MERGE.
--  3. `is_current` is a derived flag over the whole partition, so it has to be
--     recomputed for every version each run, not just for new rows.

-- --------------------------------------------------------------------------
-- Typed source
-- --------------------------------------------------------------------------

-- Staging is text, so every cast happens here, in one place, where a failure
-- is attributable. nullif(...,'') first: the generator writes NULL as an empty
-- field, and ''::date is an error rather than a NULL.
CREATE TEMP TABLE tmp_employee_source ON COMMIT DROP AS
SELECT
    employee_id,
    valid_from::date                          AS valid_from,
    nullif(valid_to, '')::date                AS valid_to,
    nullif(first_name, '')                    AS first_name,
    nullif(last_name, '')                     AS last_name,
    nullif(work_email, '')                    AS work_email,
    nullif(region, '')                        AS region,
    nullif(location, '')                      AS location,
    nullif(department_code, '')               AS department_code,
    nullif(department_name, '')               AS department_name,
    nullif(job_level, '')::smallint           AS job_level,
    nullif(job_title, '')                     AS job_title,
    nullif(manager_id, '')                    AS manager_id,
    nullif(employment_type, '')               AS employment_type,
    nullif(change_reason, '')                 AS change_reason,
    nullif(hire_date, '')::date               AS hire_date,
    nullif(termination_date, '')::date        AS termination_date,
    nullif(termination_reason, '')            AS termination_reason
FROM staging.hr_employee_history;

CREATE INDEX ON tmp_employee_source (employee_id, valid_from);

-- --------------------------------------------------------------------------
-- Rule HR_OVERLAPPING_SPAN — error, rejected
-- --------------------------------------------------------------------------

-- Of an overlapping pair, the later-starting version is the one at fault: a
-- span whose start was pulled backwards into its predecessor. Rejecting the
-- earlier one instead would discard a version that is probably correct and
-- leave the corrupted one in place.
--
-- Rejecting a version leaves a *gap* in that employee's history — a date range
-- with no version in force — and that is the intended outcome. The alternative
-- is to close the gap by stretching the neighbouring span, which would invent
-- effective dates the source does not support and make a point-in-time query
-- return a confidently wrong answer instead of no answer. A gap is visible;
-- a fabricated span is not.
CREATE TEMP TABLE tmp_employee_overlap ON COMMIT DROP AS
SELECT DISTINCT later.employee_id, later.valid_from
FROM tmp_employee_source AS later
JOIN tmp_employee_source AS earlier
  ON  earlier.employee_id = later.employee_id
  AND earlier.valid_from  < later.valid_from
  AND daterange(earlier.valid_from, earlier.valid_to, '[)')
   && daterange(later.valid_from,   later.valid_to,   '[)');

INSERT INTO warehouse.dq_exception (
    run_id, dataset, rule_code, severity, disposition, target_key, detail
)
SELECT
    current_setting('intus.run_id')::bigint,
    'hr_employee_history',
    'HR_OVERLAPPING_SPAN',
    'error',
    'rejected',
    employee_id || '|' || valid_from,
    'SCD2 span overlaps an earlier version of the same employee'
FROM tmp_employee_overlap;

-- --------------------------------------------------------------------------
-- Rule HR_ORPHAN_MANAGER — warning, repaired
-- --------------------------------------------------------------------------

-- Repaired rather than rejected, and the distinction is the point: an employee
-- whose manager is absent from the extract is still an employee. Dropping the
-- row would lose a person from headcount in order to fix a pointer, so the
-- pointer is nulled and the fact recorded.
CREATE TEMP TABLE tmp_employee_orphan_manager ON COMMIT DROP AS
SELECT source.employee_id, source.valid_from, source.manager_id
FROM tmp_employee_source AS source
WHERE source.manager_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM tmp_employee_source AS manager
      WHERE manager.employee_id = source.manager_id
  );

INSERT INTO warehouse.dq_exception (
    run_id, dataset, rule_code, severity, disposition, target_key, detail
)
SELECT
    current_setting('intus.run_id')::bigint,
    'hr_employee_history',
    'HR_ORPHAN_MANAGER',
    'warning',
    'repaired',
    employee_id || '|' || valid_from,
    'manager_id ' || manager_id || ' is not in the employee population; set to NULL'
FROM tmp_employee_orphan_manager;

-- --------------------------------------------------------------------------
-- Rule HR_MISSING_TERMINATION_REASON — warning, flagged
-- --------------------------------------------------------------------------

-- Loaded unchanged. The warehouse can see that a mandatory field is blank; it
-- cannot know what belongs there, and inventing a value would be worse than
-- reporting the gap.
INSERT INTO warehouse.dq_exception (
    run_id, dataset, rule_code, severity, disposition, target_key, detail
)
SELECT
    current_setting('intus.run_id')::bigint,
    'hr_employee_history',
    'HR_MISSING_TERMINATION_REASON',
    'warning',
    'flagged',
    employee_id || '|' || valid_from,
    'employee terminated on ' || termination_date || ' with no reason recorded'
FROM tmp_employee_source
WHERE termination_date IS NOT NULL
  AND termination_reason IS NULL;

-- --------------------------------------------------------------------------
-- Validated source
-- --------------------------------------------------------------------------

CREATE TEMP TABLE tmp_employee_final ON COMMIT DROP AS
SELECT
    source.employee_id,
    source.valid_from,
    source.valid_to,
    -- is_current means "latest version of this employee", which is not the
    -- same as "still employed": a leaver's final version is current and
    -- closed. Computed over the surviving versions, after rejections.
    (row_number() OVER (
        PARTITION BY source.employee_id ORDER BY source.valid_from DESC
    ) = 1) AS is_current,
    source.first_name,
    source.last_name,
    source.first_name || ' ' || source.last_name AS full_name,
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

CREATE INDEX ON tmp_employee_final (employee_id, valid_from);

-- --------------------------------------------------------------------------
-- Reconcile
-- --------------------------------------------------------------------------

-- Versions the source no longer carries. Postgres 16's MERGE has no
-- WHEN NOT MATCHED BY SOURCE clause (added in 17), so this is a separate
-- statement — and it must run before the MERGE, or a row being deleted could
-- still collide with a row being inserted under the no-overlap constraint.
DELETE FROM warehouse.dim_employee AS target
WHERE NOT EXISTS (
    SELECT 1 FROM tmp_employee_final AS source
    WHERE source.employee_id = target.employee_id
      AND source.valid_from  = target.valid_from
);

-- Clear is_current before recomputing it. The partial unique index allows one
-- current version per employee and is checked as each row is written, so
-- flipping the flag from an old row to a new one inside a single MERGE can
-- transiently violate it depending on row order. Clearing first makes the
-- outcome independent of that order.
UPDATE warehouse.dim_employee SET is_current = false WHERE is_current;

MERGE INTO warehouse.dim_employee AS target
USING tmp_employee_final AS source
   ON target.employee_id = source.employee_id
  AND target.valid_from  = source.valid_from

WHEN MATCHED AND (
        target.valid_to, target.is_current, target.first_name, target.last_name,
        target.full_name, target.work_email, target.region, target.location,
        target.department_code, target.department_name, target.job_level,
        target.job_title, target.manager_employee_id, target.employment_type,
        target.change_reason, target.hire_date, target.termination_date,
        target.termination_reason
    ) IS DISTINCT FROM (
        source.valid_to, source.is_current, source.first_name, source.last_name,
        source.full_name, source.work_email, source.region, source.location,
        source.department_code, source.department_name, source.job_level,
        source.job_title, source.manager_employee_id, source.employment_type,
        source.change_reason, source.hire_date, source.termination_date,
        source.termination_reason
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
