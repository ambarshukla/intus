-- fact_performance_review: no seeded defects in this dataset, so this file is
-- the plain case — pure typing and point-in-time key resolution, with no
-- rule section. Included to show that not every fact needs one; a data-
-- quality section that exists only to exist would be exactly the kind of
-- decoration the defect-manifest design was built to avoid.

CREATE TEMP TABLE tmp_review_source ON COMMIT DROP AS
SELECT
    review_id,
    employee_id,
    nullif(reviewer_id, '')                    AS reviewer_id,
    submitted_date::date                       AS submitted_date,
    nullif(review_period, '')                  AS review_period,
    rating::smallint                           AS rating,
    nullif(rating_label, '')                   AS rating_label,
    promotion_recommended::boolean              AS promotion_recommended
FROM staging.hr_performance_review;

TRUNCATE warehouse.fact_performance_review;

INSERT INTO warehouse.fact_performance_review (
    review_id, employee_key, reviewer_employee_key, date_key,
    review_period, rating, rating_label, promotion_recommended
)
SELECT
    source.review_id,
    coalesce(warehouse.employee_key_best(source.employee_id, source.submitted_date), -1),
    CASE
        WHEN source.reviewer_id IS NOT NULL
        THEN warehouse.employee_key_best(source.reviewer_id, source.submitted_date)
    END,
    (to_char(source.submitted_date, 'YYYYMMDD'))::integer,
    source.review_period,
    source.rating,
    source.rating_label,
    source.promotion_recommended
FROM tmp_review_source AS source;
