-- fact_access_event: authentication and access logs, and the domain the
-- governance phase is really about. Three rules, ordered here by how much
-- they lean on warehouse.employee_key_as_of rather than on staging alone —
-- the centrepiece rule exists only because that function returns NULL for a
-- date no version covers.

CREATE TEMP TABLE tmp_access_source ON COMMIT DROP AS
SELECT
    event_id,
    nullif(employee_id, '')          AS employee_id,
    department_code,
    event_ts::timestamp              AS event_ts,
    nullif(system, '')               AS system,
    nullif(action, '')               AS action,
    nullif(resource, '')             AS resource,
    nullif(source_ip, '')            AS source_ip,
    nullif(source_country, '')       AS source_country,
    nullif(result, '')               AS result,
    mfa_used::boolean                AS mfa_used
FROM staging.sec_access_event;

CREATE INDEX ON tmp_access_source (employee_id, event_ts);

-- --------------------------------------------------------------------------
-- Rule SEC_MISSING_ACTOR — error, flagged
-- --------------------------------------------------------------------------

-- An access event nobody can be held to is a finding in itself, not a data
-- entry gap to quietly fill. Kept in the fact (mapped to the unknown member)
-- rather than dropped, because the event still happened and still belongs in
-- the audit trail — only the "who" is missing.
INSERT INTO warehouse.dq_exception (
    run_id, dataset, rule_code, severity, disposition, target_key, detail
)
SELECT
    current_setting('intus.run_id')::bigint,
    'sec_access_event',
    'SEC_MISSING_ACTOR',
    'error',
    'flagged',
    event_id,
    'employee_id is NULL; event is unattributable'
FROM tmp_access_source
WHERE employee_id IS NULL;

-- --------------------------------------------------------------------------
-- Rule SEC_LOGIN_AFTER_TERMINATION — error, flagged (the centrepiece)
-- --------------------------------------------------------------------------

-- Built directly on employee_key_as_of returning NULL: the person exists in
-- dim_employee (so this is not a missing actor) but no version of them
-- covers this event's date, and the reason is specifically that their last
-- version closed before it — i.e. they had already left. Kept, not rejected,
-- for the same reason as the missing-actor rule: an ITGC exception that gets
-- deleted on discovery is not evidence of anything.
INSERT INTO warehouse.dq_exception (
    run_id, dataset, rule_code, severity, disposition, target_key, detail
)
SELECT
    current_setting('intus.run_id')::bigint,
    'sec_access_event',
    'SEC_LOGIN_AFTER_TERMINATION',
    'error',
    'flagged',
    source.event_id,
    'successful login by ' || source.employee_id || ' at ' || source.event_ts
        || ', ' || (source.event_ts::date - person.termination_date) || ' day(s) after termination on '
        || person.termination_date
FROM tmp_access_source AS source
JOIN warehouse.dim_employee AS person
  ON person.employee_id = source.employee_id AND person.is_current
WHERE source.employee_id IS NOT NULL
  AND source.action = 'LOGIN'
  AND source.result = 'SUCCESS'
  AND person.termination_date IS NOT NULL
  AND source.event_ts::date >= person.termination_date
  AND warehouse.employee_key_as_of(source.employee_id, source.event_ts::date) IS NULL;

-- --------------------------------------------------------------------------
-- Rule SEC_IMPOSSIBLE_TRAVEL — error, flagged
-- --------------------------------------------------------------------------

-- Two access events close together, from countries in different broad
-- regions. Two things about this rule are less obvious than they look, and
-- both came from watching the first version of this query score against the
-- manifest and finding it badly wrong.
--
-- First: only the *later* event has to be a successful login. The generator
-- (intus_gen.domains.access._impossible_travel) fabricates one new LOGIN/
-- SUCCESS row near an existing event of *any* action and result — the
-- existing row is the anomaly's anchor, not itself required to be a login.
-- Requiring both sides to be LOGIN/SUCCESS missed two of every three seeded
-- pairs, because the anchor event was as likely to be a QUERY as a LOGIN.
--
-- Second: "different country" is not the same claim as "different region",
-- and only the second one is actually implausible. source_country is drawn
-- per event from a 3-4-country pool *per region*
-- (intus_gen.domains.access._COUNTRY_BY_REGION) — so two ordinary events for the same
-- person, minutes apart, legitimately land on different countries within
-- their own region often enough to swamp the real defect in noise. The
-- region lookup below is a second copy of that same table, kept honest by
-- tests/test_dq.py::test_region_lookup_matches_the_generator (the AI-pricing
-- pattern again: duplicate a small reference on purpose, catch drift with a
-- test) — and it is *region* that must differ, not raw country text.
--
-- DISTINCT ON collapses multiple candidate anchors for one login down to the
-- single nearest one: without it, a login with three qualifying anchor
-- events in the preceding hour would be flagged three times over.
INSERT INTO warehouse.dq_exception (
    run_id, dataset, rule_code, severity, disposition, target_key, detail
)
SELECT DISTINCT ON (later.event_id)
    current_setting('intus.run_id')::bigint,
    'sec_access_event',
    'SEC_IMPOSSIBLE_TRAVEL',
    'error',
    'flagged',
    later.event_id,
    later.employee_id || ' logged in from ' || later.source_country || ' (' || later_region.region
        || ') ' || extract(epoch FROM later.event_ts - earlier.event_ts) / 60
        || ' minute(s) after an event from ' || earlier.source_country || ' (' || earlier_region.region || ')'
FROM tmp_access_source AS later
JOIN tmp_access_source AS earlier
  ON  earlier.employee_id = later.employee_id
  AND earlier.event_id <> later.event_id
  AND earlier.event_ts < later.event_ts
  AND later.event_ts - earlier.event_ts <= INTERVAL '1 hour'
JOIN (
    VALUES
        ('US', 'Americas'), ('CA', 'Americas'), ('BR', 'Americas'),
        ('GB', 'EMEA'), ('IE', 'EMEA'), ('NL', 'EMEA'), ('DE', 'EMEA'),
        ('SG', 'APAC'), ('AU', 'APAC'), ('IN', 'APAC'), ('JP', 'APAC')
) AS later_region (country, region) ON later_region.country = later.source_country
JOIN (
    VALUES
        ('US', 'Americas'), ('CA', 'Americas'), ('BR', 'Americas'),
        ('GB', 'EMEA'), ('IE', 'EMEA'), ('NL', 'EMEA'), ('DE', 'EMEA'),
        ('SG', 'APAC'), ('AU', 'APAC'), ('IN', 'APAC'), ('JP', 'APAC')
) AS earlier_region (country, region) ON earlier_region.country = earlier.source_country
WHERE later.employee_id IS NOT NULL
  AND later.action = 'LOGIN' AND later.result = 'SUCCESS'
  AND later_region.region <> earlier_region.region
ORDER BY later.event_id, (later.event_ts - earlier.event_ts) ASC;

TRUNCATE warehouse.fact_access_event;

INSERT INTO warehouse.fact_access_event (
    event_id, employee_id, employee_key, department_key, date_key, event_ts,
    system, action, resource, source_ip, source_country, result, mfa_used
)
SELECT
    source.event_id,
    source.employee_id,
    -- employee_key_best, not employee_key_as_of: a post-termination login is
    -- exactly the row where strict point-in-time resolution returns NULL —
    -- that NULL is what the rule above detects — but the fact should still
    -- point at the actual person. An audit trail that resolves its most
    -- important finding to "Unknown Employee" has defeated its own purpose.
    coalesce(
        CASE
            WHEN source.employee_id IS NOT NULL
            THEN warehouse.employee_key_best(source.employee_id, source.event_ts::date)
        END,
        -1
    ),
    coalesce(department.department_key, -1),
    (to_char(source.event_ts::date, 'YYYYMMDD'))::integer,
    source.event_ts,
    source.system,
    source.action,
    source.resource,
    source.source_ip,
    source.source_country,
    source.result,
    source.mfa_used
FROM tmp_access_source AS source
LEFT JOIN warehouse.dim_department AS department
       ON department.department_code = source.department_code;
