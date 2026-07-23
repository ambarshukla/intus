-- dim_department: one definition of "department", assembled from two sources.
--
-- Code and name come from the HR extract; the cost centre comes from finance.
-- That is what *conformed* means in practice — an HR headcount report and a
-- finance spend report slice by the same dimension, so "Engineering" means the
-- same thing in both. Building two department lookups instead, one per source,
-- is how an organisation ends up with two headcount numbers and an argument.
--
-- MERGE rather than truncate-and-rebuild, for the reason that governs every
-- dimension here: surrogate keys must survive a rerun, because facts reference
-- them. Rebuilding would reissue every key and orphan every fact.

MERGE INTO warehouse.dim_department AS target
USING (
    SELECT
        hr.department_code,
        hr.department_name,
        fin.cost_center
    FROM (
        SELECT DISTINCT department_code, department_name
        FROM staging.hr_employee_history
        WHERE department_code IS NOT NULL AND department_code <> ''
    ) AS hr
    LEFT JOIN (
        -- One cost centre per department in this dataset; min() makes the
        -- assumption explicit and keeps the join single-valued rather than
        -- silently multiplying rows if that ever stops being true.
        SELECT department_code, min(cost_center) AS cost_center
        FROM staging.fin_budget
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
