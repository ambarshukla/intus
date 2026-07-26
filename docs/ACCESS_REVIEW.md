# Access review

A periodic access review answers one question with evidence, not assertion:
*does who can actually see what still match who is supposed to be able to?*
This document is both the template intus's governance layer is designed to be
reviewed against, and the record of the first review actually performed
against it — on the live workspace, the day the governance layer shipped.

## What is under review

Two axes, matching the independent-axes design in `docs/DECISIONS.md` D-029:

- **Row-level scope** (`intus.governance.department_scope`) — which
  department(s) a persona's members see rows for in the tables a row filter
  is attached to (`dim_employee`, `fact_compensation`,
  `fact_performance_review`, `fact_access_event`, `fact_ai_usage`,
  `fact_gl_actual`, `fact_budget`).
- **Column-level capability** (`intus.governance.capability_grant`) —
  whether a persona's members see a masked column's real value at all,
  independent of which rows they can see.

Both tables are the actual source of truth — this document is a snapshot of
their contents on the review date, not a hand-maintained copy that could
silently drift from what the SQL actually enforces. Re-running the two
`SELECT` statements below against the live catalog is how a future review
should start.

```sql
SELECT * FROM intus.governance.department_scope ORDER BY group_name;
SELECT * FROM intus.governance.capability_grant ORDER BY group_name, capability;
```

## Persona → access matrix (as of 2026-07-26)

| Persona (group) | Row scope | Column capability | Tables granted |
|---|---|---|---|
| `grp_exec` | All departments | None | `intus.silver` (schema-wide SELECT), `intus.gold` |
| `grp_hr_analyst` | All departments | `view_performance_rating`, `view_hr_sensitive` | `dim_employee`, `fact_compensation`, `fact_performance_review` |
| `grp_total_rewards` | All departments | `view_compensation_detail` | `fact_compensation` |
| `grp_security` | All departments | `view_pii_network`, `view_pii_identity` | `fact_access_event`, `fact_ai_usage` |
| `grp_fp_a` | All departments | None | `fact_gl_actual`, `fact_budget` |
| `grp_sales_ops` | N/A (whole-table GRANT, no row filter — see D-029) | None | `dim_account`, `fact_opportunity`, `fact_subscription`, `fact_invoice` |
| `grp_dept_manager_engineering` | Engineering only | None | `fact_compensation`, `fact_performance_review`, `fact_access_event`, `fact_ai_usage`, `fact_gl_actual`, `fact_budget` |

Reading this table correctly requires holding both columns in mind at once:
`grp_exec` sees every department's *rows* but no persona's masked *values*
unless separately granted — broad visibility is not broad disclosure. This is
the single most important property this review exists to keep true over time;
a future reviewer's first job is checking that no persona has accumulated a
capability grant nobody remembers deciding to give it.

## Review performed 2026-07-26

**Scope.** The governance layer as built in this PR — every row filter,
column mask, and GRANT in `lakehouse/sql/40_governance_schema.sql` /
`41_governance_apply.sql`, tested against the live workspace, not read off
the SQL and assumed correct.

**Method.** For each independent axis, the reviewing session's own account
toggled its group membership and re-queried, rather than trusting that a
`CREATE FUNCTION` succeeding meant the function did what it claimed:

1. **Default-deny, before any group membership.** `fact_compensation` and
   `fact_gl_actual` returned zero rows; `dim_employee.termination_reason`/
   `job_level` returned NULL for every row. Confirms a persona with no
   assigned group sees nothing, not everything — the direction a
   misconfiguration here should fail in.
2. **Row scope alone** (`grp_dept_manager_engineering`, no capability grant).
   `fact_gl_actual` returned Engineering only; `fact_compensation` returned
   57 Engineering-scoped rows with every RESTRICTED column still NULL.
   Confirms row visibility does not imply column disclosure.
3. **Row scope + capability together** (adding `grp_total_rewards`, whose
   department scope is company-wide). `fact_compensation` returned 221
   rows — every department, since Total Rewards' own scope is company-wide
   and a principal's *union* of group scopes applies — with real
   `annual_salary_usd`/`bonus_target_pct`/`equity_units` values, while
   `fact_performance_review.rating` remained NULL (no
   `view_performance_rating` capability). Confirms the two axes compose
   correctly when a principal holds more than one persona.

**Finding, not a defect: propagation delay.** Both the grant (step 2→3
above) and the subsequent revocation took roughly ten to fifteen minutes of
real wall-clock time to take effect after the underlying group-membership API
call returned success — see D-033 for the full account. **This changes what
"as of" means for any access review going forward**: a group-membership
change's *timestamp* is not the same as the change's *effective* timestamp,
and a review (or an offboarding process) that assumes otherwise could report
a control as active before it actually is, in either direction. Any future
review should query effective state directly (`is_account_group_member`
against the account being reviewed), not infer it from an API response.

**Outcome.** No discrepancy found between designed and actual access for the
personas above — the first review has nothing to remediate, which is the
expected and correct outcome for a layer reviewed on the day it was built.

## Known gap: segregation of duties

This review was performed by the same session that designed and built the
governance layer being reviewed. A real SOX-style access review requires an
independent reviewer — the person checking access is not the person who
granted it. This project is a single-author reference project and cannot
meaningfully demonstrate that separation; it is named here rather than
silently assumed away, the same treatment every other known gap in this
project gets (see `docs/CUTOVER_PLAN.md`'s open items for the same pattern
applied to the migration).

## Cadence

No review schedule is enforced by the platform (Free Edition has no
account-console workflow for this). A real deployment would tie this
document's matrix to a recurring calendar review (quarterly is a common SOX
baseline) with a named owner and a diff against the previous review's
snapshot — a natural extension of the D-010 "duplicate small reference data,
test for drift" pattern this project already uses everywhere else, applied to
governance state itself rather than schema.
