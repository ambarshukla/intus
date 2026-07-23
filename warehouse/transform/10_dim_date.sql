-- dim_date: generated, not derived.
--
-- A date dimension built from `SELECT DISTINCT` over the facts has holes in it
-- exactly where the business cares — the months with no activity, which are
-- the ones a variance report needs to show as zero rather than omit. Generating
-- a fixed range wider than the data avoids that entirely.
--
-- Idempotent through ON CONFLICT DO NOTHING: rerunning the transform neither
-- duplicates nor rewrites rows, and a date's attributes cannot change.

INSERT INTO warehouse.dim_date (
    date_key, full_date, year, quarter, month, month_name,
    day_of_month, day_of_week, day_name, iso_week, is_weekend,
    fiscal_period, fiscal_quarter, fiscal_year
)
SELECT
    (to_char(day, 'YYYYMMDD'))::integer                      AS date_key,
    day                                                      AS full_date,
    extract(year FROM day)::smallint                         AS year,
    extract(quarter FROM day)::smallint                      AS quarter,
    extract(month FROM day)::smallint                        AS month,
    -- 'FMMonth'/'FMDay' are always English regardless of lc_time; only the
    -- TM-prefixed patterns localise. That matters for a column other systems
    -- will join on.
    to_char(day, 'FMMonth')                                  AS month_name,
    extract(day FROM day)::smallint                          AS day_of_month,
    extract(isodow FROM day)::smallint                       AS day_of_week,
    to_char(day, 'FMDay')                                    AS day_name,
    extract(week FROM day)::smallint                         AS iso_week,
    extract(isodow FROM day) >= 6                            AS is_weekend,
    -- Mirrors intus_gen.fiscal: fiscal year = calendar year.
    'FY' || extract(year FROM day) || '-M' || to_char(day, 'MM')      AS fiscal_period,
    'FY' || extract(year FROM day) || '-Q' || extract(quarter FROM day) AS fiscal_quarter,
    extract(year FROM day)::smallint                         AS fiscal_year
-- The range must cover more than the generated dataset's own start/end
-- dates: executives are backdated up to seven years before the world's
-- start_date (intus_gen.world._build_people), so a compensation or employee-
-- history row can legitimately carry a date years earlier than anything else
-- in the extract. 2010 is comfortably ahead of that; 2035 leaves headroom for
-- --as-of dates later than today's default without needing this file
-- touched. A date this table cannot cover fails with a foreign key error at
-- fact-load time, not a wrong answer, so a real bug here was cheap to find —
-- see docs/DECISIONS.md D-015.
FROM generate_series(DATE '2010-01-01', DATE '2035-12-31', INTERVAL '1 day') AS series(day)
ON CONFLICT (date_key) DO NOTHING;
