-- Governance: the schema Phase 4 is built around. Two small reference
-- tables (who gets what, not what the data says) plus the row-filter and
-- column-mask functions that read them. Runs after 22_silver_facts.sql
-- (task ordering in databricks.yml) since the department-key lookups below
-- need dim_department and dim_employee already populated.
--
-- Design note, worth stating up front because it is the load-bearing
-- decision of this file: row-level scope and column-level capability are
-- two INDEPENDENT axes, tracked in two separate tables, not one permission
-- check. Being able to see a row (your department's compensation records
-- exist) does not imply being able to see a masked column's true value
-- (the actual salary) — a department manager sees their team's compensation
-- *rows* (grain: who, when, what changed) with the *amounts* still masked
-- unless they are also Total Rewards. Conflating the two into one grant
-- would make "can see the row" quietly imply "can see everything in it",
-- which is exactly the kind of scope creep a SOX-style access review exists
-- to catch.
--
-- Coverage: every RESTRICTED-tier column the generator declares
-- (`Dataset.columns_at(Tier.RESTRICTED)` — its own docstring already states
-- this as the governance phase's job) is masked by the time this file and
-- 41_governance_apply.sql have both run: compensation amounts, performance
-- ratings, dim_employee's termination_reason/job_level, security event
-- source IP, and the requester-identity/policy-flag columns on the two
-- IT/security event logs. `lakehouse/tests/test_governance_coverage.py`
-- asserts this against the classification directly, the same drift-check
-- shape as every other D-010 duplication in this project, so a new
-- RESTRICTED column added to a generator later fails CI here instead of
-- silently shipping unmasked.
--
-- dim_employee gets masks only, not a row filter, for a real platform
-- reason recorded in 20_silver_schema.sql and D-031: Unity Catalog refuses
-- either feature on a table with a CHECK constraint, and this table's is
-- worth keeping over gaining a row filter here.
--
-- CONFIDENTIAL-tier tables are handled differently, deliberately: the
-- tier's own definition ("restricted to a business function by row-level
-- security" — docs/data-catalog.md) is, for the CRM tables, actually a
-- whole-table boundary (every row already belongs to Sales Operations,
-- there is no per-row split to filter), so the right control is a GRANT to
-- `grp_sales_ops`, not a row filter with nothing to differentiate on — see
-- the GRANT section below. Finance's fin_budget/fin_actual are the
-- exception: rows there DO span every department, so those two get the
-- same department-scoped row filter as the RESTRICTED-tier facts, on top
-- of the GRANT. See D-029 for the fuller reasoning.

CREATE SCHEMA IF NOT EXISTS intus.governance;

-- --------------------------------------------------------------------------
-- Reference tables
-- --------------------------------------------------------------------------

-- Row-level scope: which department(s) a group's members may see rows for.
-- NULL department_key means "every department" (exec / compliance-style
-- oversight roles) — deliberately NOT the sentinel -1 that dim_department
-- already uses for its own "Unknown Department" row, which would otherwise
-- silently collide two unrelated meanings of the same value. A group with
-- no row here sees zero rows in every department-scoped table: default-deny,
-- not default-allow, which is the direction a mistake here should fail in.
CREATE TABLE IF NOT EXISTS intus.governance.department_scope (
    group_name     STRING NOT NULL,
    department_key BIGINT  -- NULL = all departments
) USING DELTA;

-- Column-level capability: whether a group's members see a masked column's
-- real value at all, independent of which rows they can see.
CREATE TABLE IF NOT EXISTS intus.governance.capability_grant (
    group_name STRING NOT NULL,
    capability STRING NOT NULL
) USING DELTA;

-- A governance-owned copy of employee -> department, refreshed from
-- dim_employee/dim_department every run. Confirmed live and necessary, not
-- a stylistic choice: Unity Catalog refuses to let a row filter function
-- scan a table that itself carries a row filter or column mask, even for
-- unrelated columns (UNSUPPORTED_NESTED_ROW_OR_COLUMN_ACCESS_POLICY) — and
-- dim_employee carries column masks (below). rf_department_by_employee
-- reads this table instead of dim_employee directly, sidestepping the
-- restriction the same way real entitlement systems do: an identity/scope
-- mapping table maintained specifically so authorization checks never have
-- to touch the governed data itself.
CREATE TABLE IF NOT EXISTS intus.governance.employee_department (
    employee_key   BIGINT NOT NULL,
    department_key BIGINT
) USING DELTA;

TRUNCATE TABLE intus.governance.employee_department;

INSERT INTO intus.governance.employee_department (employee_key, department_key)
SELECT employee.employee_key, department.department_key
FROM intus.silver.dim_employee AS employee
LEFT JOIN intus.silver.dim_department AS department
       ON department.department_name = employee.department_name;

-- Truncate-and-reload, same idiom every fact table and reference table in
-- this project uses: this file is the desired-state config, not an
-- append log, so a rerun should leave exactly these rows, not these rows
-- plus whatever an earlier run also inserted.
TRUNCATE TABLE intus.governance.department_scope;

INSERT INTO intus.governance.department_scope (group_name, department_key)
-- Company-wide oversight roles: HR (needs every employee to do HR analyst
-- work at all), Total Rewards (compensation admins), Security (incident
-- response can't be scoped to one department), and executive/compliance.
SELECT 'grp_exec', CAST(NULL AS BIGINT)
UNION ALL SELECT 'grp_hr_analyst', CAST(NULL AS BIGINT)
UNION ALL SELECT 'grp_total_rewards', CAST(NULL AS BIGINT)
UNION ALL SELECT 'grp_security', CAST(NULL AS BIGINT)
-- FP&A plans across the whole company, not one department — company-wide by
-- design, unlike the department manager persona below. This is also why
-- fin_budget/fin_actual's CONFIDENTIAL tier gets a row filter at all (every
-- department's rows exist in one table) where the CRM tables' CONFIDENTIAL
-- tier does not (every row already belongs to Sales Operations alone) — see
-- the GRANT section's comment for that half of the distinction.
UNION ALL SELECT 'grp_fp_a', CAST(NULL AS BIGINT)
-- One deliberately NARROW persona: an Engineering department manager, scoped
-- to exactly one department. This is the row that actually proves the
-- filter restricts something — a persona that sees everything proves the
-- mechanism doesn't error, not that it denies. The department_key is looked
-- up rather than hard-coded, since surrogate keys are assigned at load time
-- (D-014: stable across reruns, but not literal constants known in advance).
UNION ALL
SELECT 'grp_dept_manager_engineering', department_key
FROM intus.silver.dim_department
WHERE department_name = 'Engineering';

TRUNCATE TABLE intus.governance.capability_grant;

INSERT INTO intus.governance.capability_grant (group_name, capability)
-- Total Rewards is the only group that sees real compensation amounts —
-- "masked outside Total Rewards" is the classification's own wording
-- (docs/data-catalog.md), enforced here rather than merely documented.
SELECT 'grp_total_rewards', 'view_compensation_detail'
-- Performance ratings: "visible to HR and the reviewing manager" per the
-- classification. The reviewing-manager half of that isn't modelled here
-- (it is a per-row relationship, not a group grant) — named as a gap, not
-- silently dropped; HR's half is a straightforward capability grant.
UNION ALL SELECT 'grp_hr_analyst', 'view_performance_rating'
-- Source IP is personal data under GDPR (data-catalog.md's own note) —
-- masked from everyone except the security team investigating an incident.
UNION ALL SELECT 'grp_security', 'view_pii_network'
-- termination_reason ("HR only, never in BI") and job_level ("visible to HR
-- and managers only") on dim_employee — the manager half of job_level, like
-- the reviewing-manager half of performance ratings above, is a per-row
-- relationship this capability model doesn't reach; HR's half is enforced.
UNION ALL SELECT 'grp_hr_analyst', 'view_hr_sensitive'
-- Requester identity and policy-flag columns on the two IT/security-owned
-- event logs — the same persona already owns source_ip above, so the same
-- group, a new capability. This is the last RESTRICTED-tier column pair
-- left uncovered after the ones above (see coverage note in the file
-- header) — with this grant, every RESTRICTED column the generator declares
-- (sensitivity.py's own stated contract) is covered by a mask.
UNION ALL SELECT 'grp_security', 'view_pii_identity';

-- --------------------------------------------------------------------------
-- Row filter functions
-- --------------------------------------------------------------------------
--
-- A real dialect trap, found live and worth flagging in-line as well as in
-- DECISIONS D-030: name a function parameter the same as a column on the
-- table it queries (e.g. `dept STRING` alongside a lookup table's own `dept`
-- column) and an unqualified reference inside the function body resolves to
-- the TABLE column, not the parameter — `s.dept = dept` silently becomes
-- `s.dept = s.dept`, always true, no error, no warning. Every parameter
-- below is prefixed `p_` specifically to make that collision impossible by
-- construction, not just by care.

CREATE OR REPLACE FUNCTION intus.governance.rf_department_key(p_department_key BIGINT)
RETURNS BOOLEAN
RETURN EXISTS (
    SELECT 1
    FROM intus.governance.department_scope AS scope
    WHERE is_account_group_member(scope.group_name)
      AND (scope.department_key IS NULL OR scope.department_key = p_department_key)
);

-- fact_compensation and fact_performance_review carry only employee_key —
-- resolve employee -> department through the employee_department mapping
-- above (NOT dim_employee directly — see that table's comment for why),
-- then the same scope check again. Two shapes of table (a direct
-- department_key column, and an employee-key indirection), one shared
-- filter underneath both.
CREATE OR REPLACE FUNCTION intus.governance.rf_department_by_employee(p_employee_key BIGINT)
RETURNS BOOLEAN
RETURN intus.governance.rf_department_key(
    (SELECT mapping.department_key
     FROM intus.governance.employee_department AS mapping
     WHERE mapping.employee_key = p_employee_key
     LIMIT 1)
);

-- --------------------------------------------------------------------------
-- Column mask functions
-- --------------------------------------------------------------------------
--
-- One function per (column type, capability) pair, not one generic
-- parameterised function — Unity Catalog masks take extra context only as
-- OTHER COLUMNS from the same row (`USING COLUMNS (...)`), never as a literal
-- constant, so "which capability to check" cannot be a parameter; it has to
-- be baked into which function is attached to which column. Each body is one
-- line reading the shared capability_grant table, so nothing here duplicates
-- logic, only the type signature, which the platform requires.

CREATE OR REPLACE FUNCTION intus.governance.mask_compensation_amount(v DECIMAL(12, 2))
RETURNS DECIMAL(12, 2)
RETURN CASE WHEN EXISTS (
    SELECT 1 FROM intus.governance.capability_grant AS grant
    WHERE grant.capability = 'view_compensation_detail' AND is_account_group_member(grant.group_name)
) THEN v ELSE CAST(NULL AS DECIMAL(12, 2)) END;

CREATE OR REPLACE FUNCTION intus.governance.mask_compensation_pct(v DECIMAL(5, 4))
RETURNS DECIMAL(5, 4)
RETURN CASE WHEN EXISTS (
    SELECT 1 FROM intus.governance.capability_grant AS grant
    WHERE grant.capability = 'view_compensation_detail' AND is_account_group_member(grant.group_name)
) THEN v ELSE CAST(NULL AS DECIMAL(5, 4)) END;

CREATE OR REPLACE FUNCTION intus.governance.mask_compensation_units(v INT)
RETURNS INT
RETURN CASE WHEN EXISTS (
    SELECT 1 FROM intus.governance.capability_grant AS grant
    WHERE grant.capability = 'view_compensation_detail' AND is_account_group_member(grant.group_name)
) THEN v ELSE CAST(NULL AS INT) END;

CREATE OR REPLACE FUNCTION intus.governance.mask_rating_value(v SMALLINT)
RETURNS SMALLINT
RETURN CASE WHEN EXISTS (
    SELECT 1 FROM intus.governance.capability_grant AS grant
    WHERE grant.capability = 'view_performance_rating' AND is_account_group_member(grant.group_name)
) THEN v ELSE CAST(NULL AS SMALLINT) END;

CREATE OR REPLACE FUNCTION intus.governance.mask_rating_label(v STRING)
RETURNS STRING
RETURN CASE WHEN EXISTS (
    SELECT 1 FROM intus.governance.capability_grant AS grant
    WHERE grant.capability = 'view_performance_rating' AND is_account_group_member(grant.group_name)
) THEN v ELSE CAST(NULL AS STRING) END;

CREATE OR REPLACE FUNCTION intus.governance.mask_promotion_flag(v BOOLEAN)
RETURNS BOOLEAN
RETURN CASE WHEN EXISTS (
    SELECT 1 FROM intus.governance.capability_grant AS grant
    WHERE grant.capability = 'view_performance_rating' AND is_account_group_member(grant.group_name)
) THEN v ELSE CAST(NULL AS BOOLEAN) END;

CREATE OR REPLACE FUNCTION intus.governance.mask_source_ip(v STRING)
RETURNS STRING
RETURN CASE WHEN EXISTS (
    SELECT 1 FROM intus.governance.capability_grant AS grant
    WHERE grant.capability = 'view_pii_network' AND is_account_group_member(grant.group_name)
) THEN v ELSE CAST(NULL AS STRING) END;

CREATE OR REPLACE FUNCTION intus.governance.mask_termination_reason(v STRING)
RETURNS STRING
RETURN CASE WHEN EXISTS (
    SELECT 1 FROM intus.governance.capability_grant AS grant
    WHERE grant.capability = 'view_hr_sensitive' AND is_account_group_member(grant.group_name)
) THEN v ELSE CAST(NULL AS STRING) END;

CREATE OR REPLACE FUNCTION intus.governance.mask_job_level(v SMALLINT)
RETURNS SMALLINT
RETURN CASE WHEN EXISTS (
    SELECT 1 FROM intus.governance.capability_grant AS grant
    WHERE grant.capability = 'view_hr_sensitive' AND is_account_group_member(grant.group_name)
) THEN v ELSE CAST(NULL AS SMALLINT) END;

-- Shared by fact_ai_usage.employee_id and fact_access_event.employee_id —
-- same capability, same column type, same meaning ("requesting employee",
-- both RESTRICTED for the same reason), so one function for both.
CREATE OR REPLACE FUNCTION intus.governance.mask_employee_identity(v STRING)
RETURNS STRING
RETURN CASE WHEN EXISTS (
    SELECT 1 FROM intus.governance.capability_grant AS grant
    WHERE grant.capability = 'view_pii_identity' AND is_account_group_member(grant.group_name)
) THEN v ELSE CAST(NULL AS STRING) END;

CREATE OR REPLACE FUNCTION intus.governance.mask_ai_policy_flag(v BOOLEAN)
RETURNS BOOLEAN
RETURN CASE WHEN EXISTS (
    SELECT 1 FROM intus.governance.capability_grant AS grant
    WHERE grant.capability = 'view_pii_identity' AND is_account_group_member(grant.group_name)
) THEN v ELSE CAST(NULL AS BOOLEAN) END;
