# Decisions

A running log of real design choices — what was decided, what the alternatives were,
and why. Numbered D-001 onward, append-only.

## D-001 — Project scope: internal data + governance, framed as a modernization (2026-07-23)

**Decision.** Build an internal enterprise lakehouse for a fictional company
(Halcyon), with three deliberate emphases that its sibling project `parvum` does not
cover: (1) internal/corporate data domains (HR, sales, finance, product usage, AI
usage, systems), (2) fine-grained access control and SOX-style audit evidence as a
first-class design driver, and (3) an explicit legacy-SQL-warehouse → Databricks
migration arc, including parity testing between the two.

**Alternatives considered.** (a) Extending `parvum` with a governance phase — rejected
because parvum's domain (client portfolio data served to advisory firms) doesn't
naturally exercise internal-data governance personas, and mixing narratives weakens
both. (b) A full second parvum-scale platform — rejected as low marginal learning;
everything parvum already proves (ingestion, medallion, orchestration, serving) is
reused knowledge here, not the point.

**Consequences.** The project stays small and pointed: the legacy warehouse and the
governance layer get the depth; ingestion mechanics stay simple. Sensitivity labels
are assigned at data-generation time so every access-control claim can be tested
against ground truth.

## D-002 — Two-repo split, same discipline as parvum (2026-07-23)

Public repo (`intus`) carries technical content only and is self-contained; working
notes live in a separate private repo. Same rationale as parvum: the public repo is a
reference project, not a diary.
