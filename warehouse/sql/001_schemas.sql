-- Three schemas, three responsibilities. The separation is the oldest idea in
-- warehousing and still the right one: each layer has a different contract, so
-- mixing them means no layer has a contract at all.
--
--   staging    Landed source files, exactly as delivered. Every column text,
--              no constraints, no keys. Truncate-and-reload each run.
--   warehouse  The conformed star schema. Typed, constrained, keyed. Nothing
--              reaches it without passing validation.
--   reporting  Views the business consumes. No storage of its own, so a
--              report can never quietly disagree with the facts beneath it.

CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS warehouse;
CREATE SCHEMA IF NOT EXISTS reporting;

COMMENT ON SCHEMA staging IS
    'Raw landed extracts, all columns text, no constraints. Truncated and reloaded each run.';
COMMENT ON SCHEMA warehouse IS
    'Conformed star schema: typed, constrained dimensions and facts.';
COMMENT ON SCHEMA reporting IS
    'Business-facing views over the warehouse schema. Views only, no tables.';
