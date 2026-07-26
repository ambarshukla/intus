-- Governance: attaching the row filters and column masks defined in
-- 40_governance_schema.sql to the actual silver tables. Split into its own
-- file for the same reason silver is split into schema/dimensions/facts —
-- "what the controls are" and "what they're attached to" are different
-- concerns, and a rerun of this file alone (say, after adding a new grant)
-- doesn't need to touch the function definitions.
--
-- Verified live before writing this file, not assumed: a row filter (or
-- mask) attached to a silver TABLE is inherited automatically by any gold
-- VIEW built on it, including through a GROUP BY — a department a viewer
-- can't see at the row level does not leak back in through an aggregate.
-- This is why governance is enforced exactly once, here, at silver, rather
-- than re-implemented per gold view: gold has nothing further to do.
--
-- SET ROW FILTER / SET MASK are themselves idempotent — reapplying the same
-- function on every rerun simply reassigns the same filter, unlike
-- 20_silver_schema.sql's CHECK constraint which needs an explicit
-- DROP CONSTRAINT IF EXISTS first to be rerun-safe.

-- dim_employee: masks only, no row filter — confirmed live that Unity
-- Catalog refuses BOTH features on a table with a CHECK constraint, and this
-- table's ck_dim_employee_span is worth keeping over gaining a row filter
-- here (D-031). An unfiltered company directory (name, department, title)
-- is also the more realistic default anyway; termination_reason ("HR only,
-- never in BI") and job_level ("HR and managers only") are the columns the
-- classification actually calls out, so mask exactly those instead of
-- hiding whole rows nothing asked to hide.
ALTER TABLE intus.silver.dim_employee
    ALTER COLUMN termination_reason SET MASK intus.governance.mask_termination_reason;
ALTER TABLE intus.silver.dim_employee
    ALTER COLUMN job_level SET MASK intus.governance.mask_job_level;

-- fact_compensation: RESTRICTED — "masked outside Total Rewards"
-- (data-catalog.md's own description of annual_salary_usd). Row-scoped by
-- employee's department (a manager sees their team's compensation records
-- exist and when they changed); amounts stay masked regardless, per the
-- independent-axes design in 40_governance_schema.sql's header.
ALTER TABLE intus.silver.fact_compensation
    SET ROW FILTER intus.governance.rf_department_by_employee ON (employee_key);
ALTER TABLE intus.silver.fact_compensation
    ALTER COLUMN annual_salary_usd SET MASK intus.governance.mask_compensation_amount;
ALTER TABLE intus.silver.fact_compensation
    ALTER COLUMN bonus_target_pct SET MASK intus.governance.mask_compensation_pct;
ALTER TABLE intus.silver.fact_compensation
    ALTER COLUMN equity_units SET MASK intus.governance.mask_compensation_units;

-- fact_performance_review: RESTRICTED — "visible to HR and the reviewing
-- manager" per the classification. The reviewing-manager half isn't modelled
-- here (see 40_governance_schema.sql's header); HR's half is enforced.
ALTER TABLE intus.silver.fact_performance_review
    SET ROW FILTER intus.governance.rf_department_by_employee ON (employee_key);
ALTER TABLE intus.silver.fact_performance_review
    ALTER COLUMN rating SET MASK intus.governance.mask_rating_value;
ALTER TABLE intus.silver.fact_performance_review
    ALTER COLUMN rating_label SET MASK intus.governance.mask_rating_label;
ALTER TABLE intus.silver.fact_performance_review
    ALTER COLUMN promotion_recommended SET MASK intus.governance.mask_promotion_flag;

-- fact_access_event: RESTRICTED — source_ip is personal data under GDPR
-- (data-catalog.md's own note), employee_id is the same "requesting
-- individual" concern fact_ai_usage has below; department_key is a direct
-- column here, so the plain scope check applies with no lookup needed.
ALTER TABLE intus.silver.fact_access_event
    SET ROW FILTER intus.governance.rf_department_key ON (department_key);
ALTER TABLE intus.silver.fact_access_event
    ALTER COLUMN source_ip SET MASK intus.governance.mask_source_ip;
ALTER TABLE intus.silver.fact_access_event
    ALTER COLUMN employee_id SET MASK intus.governance.mask_employee_identity;

-- fact_ai_usage: RESTRICTED employee_id and flagged_by_policy, same
-- reasoning as fact_access_event above; spans every department the same
-- way (every team uses AI tools), direct department_key column.
ALTER TABLE intus.silver.fact_ai_usage
    SET ROW FILTER intus.governance.rf_department_key ON (department_key);
ALTER TABLE intus.silver.fact_ai_usage
    ALTER COLUMN employee_id SET MASK intus.governance.mask_employee_identity;
ALTER TABLE intus.silver.fact_ai_usage
    ALTER COLUMN flagged_by_policy SET MASK intus.governance.mask_ai_policy_flag;

-- fact_gl_actual, fact_budget: CONFIDENTIAL, and — unlike the CRM tables in
-- the GRANT section below — genuinely span every department in one table
-- (every department has GL activity and a budget), so a row filter is the
-- right control here, not just a GRANT. No RESTRICTED columns of their own
-- to mask.
ALTER TABLE intus.silver.fact_gl_actual
    SET ROW FILTER intus.governance.rf_department_key ON (department_key);
ALTER TABLE intus.silver.fact_budget
    SET ROW FILTER intus.governance.rf_department_key ON (department_key);

-- --------------------------------------------------------------------------
-- Role-based access: GRANTs per persona
-- --------------------------------------------------------------------------
--
-- Row filters and masks decide what a query returns; a GRANT decides
-- whether a principal can run the query at all. Every persona group needs
-- both: without USE CATALOG/USE SCHEMA/SELECT, a group member gets
-- PERMISSION_DENIED before a row filter ever runs — verified live, since
-- this project's own account_admin session already proved row filters and
-- masks apply even to an admin (a real, positive SOX-relevant finding:
-- privileged accounts are not silently exempt from these controls just by
-- being admins), which only happens because GRANTs and row-level policies
-- are independent layers, not one combined check.
--
-- Gold is granted broadly to every persona: none of the seven `gold.*`
-- views expose RESTRICTED-tier data at individual grain
-- (test_gold_schema.py asserts this directly), so there is nothing a wider
-- grant there could leak.

GRANT USE CATALOG ON CATALOG intus TO `grp_exec`;
GRANT USE CATALOG ON CATALOG intus TO `grp_hr_analyst`;
GRANT USE CATALOG ON CATALOG intus TO `grp_total_rewards`;
GRANT USE CATALOG ON CATALOG intus TO `grp_security`;
GRANT USE CATALOG ON CATALOG intus TO `grp_fp_a`;
GRANT USE CATALOG ON CATALOG intus TO `grp_sales_ops`;
GRANT USE CATALOG ON CATALOG intus TO `grp_dept_manager_engineering`;

GRANT USE SCHEMA ON SCHEMA intus.silver TO `grp_exec`;
GRANT USE SCHEMA ON SCHEMA intus.silver TO `grp_hr_analyst`;
GRANT USE SCHEMA ON SCHEMA intus.silver TO `grp_total_rewards`;
GRANT USE SCHEMA ON SCHEMA intus.silver TO `grp_security`;
GRANT USE SCHEMA ON SCHEMA intus.silver TO `grp_fp_a`;
GRANT USE SCHEMA ON SCHEMA intus.silver TO `grp_sales_ops`;
GRANT USE SCHEMA ON SCHEMA intus.silver TO `grp_dept_manager_engineering`;

GRANT USE SCHEMA ON SCHEMA intus.gold TO `grp_exec`;
GRANT USE SCHEMA ON SCHEMA intus.gold TO `grp_hr_analyst`;
GRANT USE SCHEMA ON SCHEMA intus.gold TO `grp_total_rewards`;
GRANT USE SCHEMA ON SCHEMA intus.gold TO `grp_security`;
GRANT USE SCHEMA ON SCHEMA intus.gold TO `grp_fp_a`;
GRANT USE SCHEMA ON SCHEMA intus.gold TO `grp_sales_ops`;
GRANT USE SCHEMA ON SCHEMA intus.gold TO `grp_dept_manager_engineering`;

-- Exec / compliance oversight: broad read access to both layers, still
-- subject to every mask above (masking is a capability grant, not a row
-- GRANT — grp_exec has none of the compensation/rating/PII capabilities).
GRANT SELECT ON SCHEMA intus.silver TO `grp_exec`;
GRANT SELECT ON SCHEMA intus.gold TO `grp_exec`;

-- Gold's seven views: safe for every persona, per the boundary noted above.
GRANT SELECT ON SCHEMA intus.gold TO `grp_hr_analyst`;
GRANT SELECT ON SCHEMA intus.gold TO `grp_total_rewards`;
GRANT SELECT ON SCHEMA intus.gold TO `grp_security`;
GRANT SELECT ON SCHEMA intus.gold TO `grp_fp_a`;
GRANT SELECT ON SCHEMA intus.gold TO `grp_sales_ops`;
GRANT SELECT ON SCHEMA intus.gold TO `grp_dept_manager_engineering`;

-- HR analyst: employee directory, compensation existence (amounts masked
-- unless also Total Rewards), performance ratings (unmasked, per capability).
GRANT SELECT ON TABLE intus.silver.dim_employee TO `grp_hr_analyst`;
GRANT SELECT ON TABLE intus.silver.fact_compensation TO `grp_hr_analyst`;
GRANT SELECT ON TABLE intus.silver.fact_performance_review TO `grp_hr_analyst`;

-- Total Rewards: compensation only — this group's whole purpose.
GRANT SELECT ON TABLE intus.silver.fact_compensation TO `grp_total_rewards`;

-- Security: the two event logs their capability grant unmasks.
GRANT SELECT ON TABLE intus.silver.fact_access_event TO `grp_security`;
GRANT SELECT ON TABLE intus.silver.fact_ai_usage TO `grp_security`;

-- FP&A: finance facts, company-wide (department_scope has them as NULL —
-- see 40_governance_schema.sql).
GRANT SELECT ON TABLE intus.silver.fact_gl_actual TO `grp_fp_a`;
GRANT SELECT ON TABLE intus.silver.fact_budget TO `grp_fp_a`;

-- Sales Operations: the CRM tables, whole-table CONFIDENTIAL boundary, no
-- row filter needed (see 40_governance_schema.sql's header) — every row
-- already belongs to this business function alone.
GRANT SELECT ON TABLE intus.silver.dim_account TO `grp_sales_ops`;
GRANT SELECT ON TABLE intus.silver.fact_opportunity TO `grp_sales_ops`;
GRANT SELECT ON TABLE intus.silver.fact_subscription TO `grp_sales_ops`;
GRANT SELECT ON TABLE intus.silver.fact_invoice TO `grp_sales_ops`;

-- Engineering department manager: the narrow persona from
-- 40_governance_schema.sql, granted every department-scoped table its row
-- filter already restricts to Engineering's own rows.
GRANT SELECT ON TABLE intus.silver.fact_compensation TO `grp_dept_manager_engineering`;
GRANT SELECT ON TABLE intus.silver.fact_performance_review TO `grp_dept_manager_engineering`;
GRANT SELECT ON TABLE intus.silver.fact_access_event TO `grp_dept_manager_engineering`;
GRANT SELECT ON TABLE intus.silver.fact_ai_usage TO `grp_dept_manager_engineering`;
GRANT SELECT ON TABLE intus.silver.fact_gl_actual TO `grp_dept_manager_engineering`;
GRANT SELECT ON TABLE intus.silver.fact_budget TO `grp_dept_manager_engineering`;
