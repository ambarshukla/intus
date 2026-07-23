-- fact_ai_usage: internal LLM usage and cost, plus two governance rules that
-- exist to be seen rather than to be cleaned up.

CREATE TEMP TABLE tmp_ai_usage_source ON COMMIT DROP AS
SELECT
    event_id,
    employee_id,
    department_code,
    event_ts::timestamp             AS event_ts,
    nullif(model, '')               AS model,
    nullif(feature, '')             AS feature,
    prompt_tokens::integer          AS prompt_tokens,
    completion_tokens::integer      AS completion_tokens,
    cost_usd::numeric               AS cost_usd,
    latency_ms::integer             AS latency_ms,
    flagged_by_policy::boolean      AS flagged_by_policy
FROM staging.ai_usage_event;

-- --------------------------------------------------------------------------
-- Rule AI_COST_MISMATCH — error, flagged
-- --------------------------------------------------------------------------

-- Recomputed from the model's known per-1k-token rates, rather than detected
-- statistically. A per-model average would be defeated by the data's own
-- natural variance: prompt and completion token counts are drawn from wide
-- distributions (see intus_gen.domains.ai_usage), so a correct request's cost
-- can legitimately sit far from the mean, and a statistical threshold would
-- either miss the seeded defect or flag ordinary long requests as broken.
--
-- The rates below are a second copy of MODELS in intus_gen.domains.ai_usage.
-- That duplication is deliberate, not an oversight — reconciling against a
-- known-correct rate card is exactly what a real cost-governance control
-- does, and the alternative (a generated pricing table) is more machinery
-- than a rate list that changes on the order of once a quarter deserves.
-- The two copies are kept honest by
-- tests/test_dq.py::test_ai_pricing_matches_the_generator, following the
-- same pattern as the hand-written staging DDL (D-010): duplicate on
-- purpose, catch drift with a test.
INSERT INTO warehouse.dq_exception (
    run_id, dataset, rule_code, severity, disposition, target_key, detail
)
SELECT
    current_setting('intus.run_id')::bigint,
    'ai_usage_event',
    'AI_COST_MISMATCH',
    'error',
    'flagged',
    source.event_id,
    'cost_usd ' || source.cost_usd || ' does not reconcile to '
        || round(expected.cost_usd, 6) || ' from token counts at the known rate'
FROM tmp_ai_usage_source AS source
JOIN (
    VALUES
        ('atlas-large', 0.0030, 0.0150),
        ('atlas-mini',  0.0008, 0.0040),
        ('orion-pro',   0.0050, 0.0200),
        ('orion-lite',  0.0004, 0.0016)
) AS rates (model, input_usd_per_1k, output_usd_per_1k)
  ON rates.model = source.model
CROSS JOIN LATERAL (
    SELECT source.prompt_tokens / 1000.0 * rates.input_usd_per_1k
         + source.completion_tokens / 1000.0 * rates.output_usd_per_1k AS cost_usd
) AS expected
-- A wide relative tolerance (10%), not an equality check: the generator's own
-- rounding (money(), 6 places) can move the last digit, and the point of this
-- rule is to catch a 3x-12x corruption, not to police floating-point noise.
WHERE abs(source.cost_usd - expected.cost_usd) > 0.10 * expected.cost_usd;

-- --------------------------------------------------------------------------
-- Rule AI_UNKNOWN_MODEL — warning, flagged
-- --------------------------------------------------------------------------

-- Shadow AI: usage against a model outside the approved catalog. Warning,
-- not error — the point of this rule is visibility for cost governance, not
-- that anything is broken.
INSERT INTO warehouse.dq_exception (
    run_id, dataset, rule_code, severity, disposition, target_key, detail
)
SELECT
    current_setting('intus.run_id')::bigint,
    'ai_usage_event',
    'AI_UNKNOWN_MODEL',
    'warning',
    'flagged',
    event_id,
    'model ' || coalesce(model, 'NULL') || ' is not in the approved catalog'
FROM tmp_ai_usage_source
WHERE model NOT IN ('atlas-large', 'atlas-mini', 'orion-pro', 'orion-lite');

TRUNCATE warehouse.fact_ai_usage;

INSERT INTO warehouse.fact_ai_usage (
    event_id, employee_id, employee_key, department_key, date_key, event_ts,
    model, feature, prompt_tokens, completion_tokens, cost_usd, latency_ms,
    flagged_by_policy
)
SELECT
    source.event_id,
    source.employee_id,
    coalesce(warehouse.employee_key_best(source.employee_id, source.event_ts::date), -1),
    coalesce(department.department_key, -1),
    (to_char(source.event_ts::date, 'YYYYMMDD'))::integer,
    source.event_ts,
    source.model,
    source.feature,
    source.prompt_tokens,
    source.completion_tokens,
    source.cost_usd,
    source.latency_ms,
    source.flagged_by_policy
FROM tmp_ai_usage_source AS source
LEFT JOIN warehouse.dim_department AS department
       ON department.department_code = source.department_code;
